from datetime import datetime
from typing import Any

from apscheduler.executors.pool import ThreadPoolExecutor
from apscheduler.job import Job
from apscheduler.schedulers.base import BaseScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger

from orc import config
from orc import model as m

_instance: BaseScheduler | None = None


def set_scheduler(scheduler: BaseScheduler) -> None:
    global _instance
    _instance = scheduler


def _scheduler() -> BaseScheduler:
    if _instance is None:
        raise RuntimeError("scheduler not started")
    return _instance


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


def schedule_once(func: Any, when: datetime, **kwargs: Any) -> Job:
    return _scheduler().add_job(func, DateTrigger(when, timezone=config.settings.tz), **kwargs)


def schedule_cron(func: Any, crontab: str, **kwargs: Any) -> Job:
    return _scheduler().add_job(func, CronTrigger.from_crontab(crontab, timezone=config.settings.tz), **kwargs)


def job_exists(id: str) -> bool:
    return _scheduler().get_job(id) is not None


def is_paused(id: str) -> bool:
    return not _scheduler().get_job(id).next_run_time


def pause_job(id: str) -> None:
    _scheduler().get_job(id).pause()


def resume_job(id: str) -> None:
    _scheduler().get_job(id).resume()


def invoke_job(id: str, **kwargs: Any) -> None:
    job = _scheduler().get_job(id)
    job.func(*job.args, **{**job.kwargs, **kwargs})


def fetch_jobs_by_type(type: type) -> list[Job]:
    now = datetime.now(tz=config.settings.tz)
    return [e for e in _scheduler().get_jobs() if e.args and isinstance(e.args[0], type) and e.trigger.run_date > now]


def remove_all_jobs() -> None:
    _scheduler().remove_all_jobs()
