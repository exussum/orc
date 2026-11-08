import asyncio
import atexit
import logging
import signal
import threading
import uuid
from collections.abc import Callable
from datetime import datetime, tzinfo
from typing import Any

import orc_queue as oq
from orc_queue import (
    AnyJob,
    Coordinator,
    Events,
    Job,
    JobQueue,
    MemoryStore,
    Service,
    SqliteStore,
    ThreadLane,
    ThreadService,
    aware,
    utcnow,
)

from orc import model as m


class Scheduler:
    def __init__(self, jobs_db: str, tz: tzinfo) -> None:
        self._tz = tz
        self._jobs_db = jobs_db
        self._lock = threading.RLock()
        self._wake = threading.Event()
        self.events = Events()
        self._queues: dict[m.QueueName, JobQueue[Any]] = {}
        self._lane_queues: dict[m.Lane, list[JobQueue[Any]]] = {lane: [] for lane in m.Lane}
        self._services: list[Service] = []
        self._thread = threading.Thread(target=self._run, name="orc-queue", daemon=True)
        self.queue(m.Queue.ADHOC, lane=m.Lane.DEVICES)
        self.queue(m.Queue.IOT, lane=m.Lane.DEVICES, persist=True)
        self.queue(m.Queue.FETCH, lane=m.Lane.BACKGROUND)
        self.queue(m.Queue.PRESENCE, lane=m.Lane.BACKGROUND)

    def service(self, fn: Callable[[threading.Event], None], *, name: str) -> None:
        self._services.append(ThreadService(fn, name=name))

    def queue(self, name: m.QueueName, *, lane: m.Lane, persist: bool = False) -> None:
        if self._thread.is_alive():
            raise RuntimeError(f"queue {name!r} registered after start")
        store = SqliteStore(self._jobs_db, name) if persist else MemoryStore()
        self._queues[name] = JobQueue(self._lock, self._wake, store, 0, self.events, name=name)
        self._lane_queues[lane].append(self._queues[name])

    def start(self, ctx: m.AppContext) -> None:
        lanes = [ThreadLane(*queues) for queues in self._lane_queues.values()]
        self._coordinator: Coordinator[Any] = Coordinator(*lanes, services=self._services, context=ctx)
        self._thread.start()
        atexit.register(self._coordinator.shutdown, 15)

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

    # fmt: off
    def once(self, func: Any, when: datetime, payload: Any = None, *,
             id: str | None = None, name: str | None = None, queue: m.QueueName = m.Queue.ADHOC) -> AnyJob[Any]:
        job = Job(aware(when), None, func, payload, id=id or str(uuid.uuid4()), name=name)
        with self._lock:
            self._queues[queue].add(job)
        return job
    # fmt: on

    def now(self, func: Any, payload: Any = None, *, name: str | None = None, queue: m.QueueName = m.Queue.ADHOC) -> AnyJob[Any]:
        return self.once(func, utcnow(), payload, name=name, queue=queue)

    def cron(self, func: Any, crontab: str, *, id: str, name: str, queue: m.QueueName = m.Queue.ADHOC) -> AnyJob[Any]:
        job = oq.schedule(crontab, self._tz, func, id_template=id, name=name)
        with self._lock:
            self._queues[queue].add(job)
        return job

    def cancel(self, id: str, queue: m.QueueName) -> bool:
        return self._coordinator.cancel_job(id, queue)

    def pause_job(self, id: str, disable: bool, queue: m.QueueName) -> bool:
        return self._coordinator.pause_job(id, disable, queue)

    def pause_queue(self, queue: m.QueueName) -> None:
        self._coordinator.pause_queue(queue)

    def release_queue(self, queue: m.QueueName) -> None:
        with self._lock:
            self._queues[queue].resume()

    def jobs(self, queue: m.QueueName) -> list[AnyJob[Any]]:
        with self._lock:
            return self._queues[queue].jobs()

    def queues(self) -> dict[m.QueueName, JobQueue[Any]]:
        return dict(self._queues)

    def on_expired(self, fn: Callable[..., None]) -> None:
        oq.on(self.events, oq.Event.EXPIRED, fn)

    def on_cancelled(self, fn: Callable[..., None]) -> None:
        oq.on(self.events, oq.Event.CANCELED, fn)

    def rebuild(self) -> None:
        with self._lock:
            self._queues[m.Queue.IOT].reset()
