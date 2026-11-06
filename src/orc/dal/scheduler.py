import asyncio
import logging
import signal
import threading
from collections.abc import Callable
from datetime import datetime, tzinfo
from typing import Any

import orc_queue as oq
from orc_queue import Coordinator, Events, Job, JobQueue, MemoryStore, Service, SqliteStore, ThreadLane, ThreadService, aware, utcnow
from sqlalchemy.engine.url import make_url

from orc import model as m


class Scheduler:
    def __init__(self, jobs_db: str, tz: tzinfo) -> None:
        self._tz = tz
        path = make_url(jobs_db).database
        assert path is not None
        lock = threading.RLock()
        wake = threading.Event()
        self.events = Events()
        self._queues: dict[m.Queue, JobQueue[Any]] = {
            m.Queue.ADHOC: JobQueue(lock, wake, MemoryStore(), 0, self.events, name=m.Queue.ADHOC),
            m.Queue.CALENDAR: JobQueue(lock, wake, MemoryStore(), 0, self.events, name=m.Queue.CALENDAR),
            m.Queue.FETCH: JobQueue(lock, wake, MemoryStore(), 0, self.events, name=m.Queue.FETCH),
            m.Queue.IOT: JobQueue(lock, wake, SqliteStore(path, m.Queue.IOT), 0, self.events, name=m.Queue.IOT),
            m.Queue.PRESENCE: JobQueue(lock, wake, MemoryStore(), 0, self.events, name=m.Queue.PRESENCE),
            m.Queue.TRAVEL: JobQueue(lock, wake, SqliteStore(path, m.Queue.TRAVEL), 0, self.events, name=m.Queue.TRAVEL),
        }
        self._lock = lock
        self._lanes = [
            ThreadLane([self._queues[m.Queue.ADHOC], self._queues[m.Queue.CALENDAR], self._queues[m.Queue.IOT]]),
            ThreadLane([self._queues[m.Queue.FETCH], self._queues[m.Queue.PRESENCE], self._queues[m.Queue.TRAVEL]]),
        ]
        self._services: list[Service] = []
        self._thread = threading.Thread(target=self._run, name="orc-queue", daemon=True)

    def service(self, fn: Callable[[threading.Event], None], *, name: str) -> None:
        self._services.append(ThreadService(fn, name=name))

    def start(self, ctx: m.AppContext) -> None:
        self._coordinator: Coordinator[Any] = Coordinator(self._lanes, self._services, context=ctx)
        self._thread.start()

    def _run(self) -> None:
        try:
            asyncio.run(self._serve())
        except BaseException:
            logging.getLogger(__name__).critical("scheduler died, stopping the server", exc_info=True)
            signal.raise_signal(signal.SIGTERM)

    async def _serve(self) -> None:
        self._coordinator.start()
        await self._coordinator.run()

    def resume(self) -> None:
        self._coordinator.resume()

    def once(
        self,
        func: Any,
        when: datetime,
        payload: Any = None,
        *,
        id: str | None = None,
        name: str | None = None,
        queue: m.Queue = m.Queue.ADHOC,
    ) -> Job[Any]:
        target = self._queues[queue]
        job = Job(aware(when), func, payload, name=name, **({"id": id} if id else {}))
        with self._lock:
            existing = target.get(job.id) if id else None
            if existing is not None:
                job.disable = existing.disable
            target.add(job)
        return job

    def now(self, func: Any, payload: Any = None, *, name: str | None = None, queue: m.Queue = m.Queue.ADHOC) -> Job[Any]:
        return self.once(func, utcnow(), payload, name=name, queue=queue)

    def cron(self, func: Any, crontab: str, *, id: str, name: str, queue: m.Queue = m.Queue.ADHOC) -> Job[Any]:
        job = oq.schedule(crontab, self._tz, func, id_template=id, name=name)
        with self._lock:
            self._queues[queue].add(job)
        return job

    def cancel(self, id: str) -> bool:
        return self._coordinator.cancel_job(id)

    def pause_job(self, id: str, disable: bool) -> bool:
        return self._coordinator.pause_job(id, disable)

    def pause_queue(self, queue: m.Queue) -> None:
        self._coordinator.pause_queue(queue)

    def release_queue(self, queue: m.Queue) -> None:
        with self._lock:
            self._queues[queue].resume()

    def jobs(self, queue: m.Queue) -> list[Job[Any]]:
        with self._lock:
            return self._queues[queue].jobs()

    def stores(self) -> list[m.QueueStatus]:
        with self._lock:
            return [
                m.QueueStatus(str(q.name), q.paused_at.astimezone(self._tz) if q.paused_at else None, q.jobs())
                for q in self._queues.values()
            ]

    def rebuild(self) -> None:
        with self._lock:
            self._queues[m.Queue.IOT].reset()
