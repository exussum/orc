import logging
from datetime import datetime, tzinfo
from typing import Any

from apscheduler.events import EVENT_ALL_JOBS_REMOVED, EVENT_JOB_MISSED, JobExecutionEvent
from apscheduler.executors.pool import ThreadPoolExecutor
from apscheduler.job import Job
from apscheduler.jobstores.memory import MemoryJobStore
from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger
from sqlalchemy import text

from orc import model as m

_log = logging.getLogger(__name__)

DEFAULT = "default"
MEMORY = "memory"
_DEFAULT_MISFIRE_GRACE = 300


class ContextThreadPoolExecutor(ThreadPoolExecutor):
    def __init__(self, ctx: m.AppContext) -> None:
        super().__init__(max_workers=1)
        self.ctx = ctx

    def _do_submit_job(self, job: Job, run_times: list[datetime]) -> Any:
        dispatch_job = job.__class__.__new__(job.__class__)
        for slot in job.__slots__:
            try:
                setattr(dispatch_job, slot, getattr(job, slot))
            except AttributeError:
                pass
        dispatch_job._jobstore_alias = job._jobstore_alias
        dispatch_job.kwargs = {**job.kwargs, "ctx": self.ctx}
        return super()._do_submit_job(dispatch_job, run_times)


class Scheduler:
    def __init__(self, jobs_db: str, tz: tzinfo) -> None:
        self._tz = tz
        self._inner = BackgroundScheduler(
            jobstores={DEFAULT: SQLAlchemyJobStore(url=jobs_db), MEMORY: MemoryJobStore()},
            job_defaults={"misfire_grace_time": _DEFAULT_MISFIRE_GRACE},
            timezone=tz,
        )

    def start(self, ctx: m.AppContext) -> None:
        self._inner.add_executor(ContextThreadPoolExecutor(ctx), DEFAULT)
        self._inner.add_listener(_log_missed, EVENT_JOB_MISSED)
        self._inner.start(paused=True)

    def resume(self) -> None:
        self._inner.resume()

    def once(self, func: Any, when: datetime, *args: Any, id: str | None = None, name: str | None = None, persist: bool = False) -> Job:
        return self._add(func, DateTrigger(when, timezone=self._tz), args, id, name, persist)

    def now(self, func: Any, *args: Any, name: str | None = None, skip_if_late: bool = False) -> Job:
        grace = _DEFAULT_MISFIRE_GRACE if skip_if_late else None
        return self._add(func, DateTrigger(datetime.now(tz=self._tz), timezone=self._tz), args, None, name, False, misfire_grace_time=grace)

    def cron(self, func: Any, crontab: str, *args: Any, id: str, name: str) -> Job:
        return self._add(func, CronTrigger.from_crontab(crontab, timezone=self._tz), args, id, name, False)

    def cancel(self, id: str) -> bool:
        if job := self._inner.get_job(id):
            job.remove()
            return True
        return False

    def set_paused(self, id: str, paused: bool) -> bool:
        if not (job := self._inner.get_job(id)):
            return False
        job.pause() if paused else job.resume()
        return True

    def matching(self, type: type) -> list[Job]:
        now = datetime.now(tz=self._tz)
        return [e for e in self._inner.get_jobs() if e.args and isinstance(e.args[0], type) and e.trigger.run_date > now]

    def stores(self) -> list[tuple[str, list[Job]]]:
        return [(store, self._inner.get_jobs(jobstore=store)) for store in (DEFAULT, MEMORY)]

    def on_rebuild(self, callback: Any) -> None:
        self._inner.add_listener(lambda event: callback(), EVENT_ALL_JOBS_REMOVED)

    def rebuild(self) -> None:
        self._inner.remove_all_jobs()

    def delete_stale(self) -> None:
        today = datetime.now(tz=self._tz).date().isoformat()
        with self._inner._lookup_jobstore(DEFAULT).engine.begin() as conn:
            conn.execute(
                text(
                    "delete from apscheduler_jobs"
                    " where next_run_time is null"
                    " and substr(id, -10) glob '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]'"
                    " and substr(id, -10) < :today"
                ),
                {"today": today},
            )

    def _add(self, func: Any, trigger: Any, args: tuple[Any, ...], id: str | None, name: str | None, persist: bool, **options: Any) -> Job:
        options |= {"args": args, "name": name, "jobstore": DEFAULT if persist else MEMORY}
        if id:
            options |= {"id": id, "replace_existing": True}
        return self._inner.add_job(func, trigger, **options)


def _log_missed(event: JobExecutionEvent) -> None:
    _log.error("scheduler: missed job %s due at %s", event.job_id, event.scheduled_run_time)
