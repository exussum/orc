from datetime import datetime
from typing import Any

from apscheduler.executors.pool import ThreadPoolExecutor
from apscheduler.job import Job
from apscheduler.schedulers.base import BaseScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger

from orc import config
from orc import model as m


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

    def run_now(self, job: Job, **extra_kwargs: Any) -> Any:
        return job.func(*job.args, ctx=self.ctx, **{**job.kwargs, **extra_kwargs})


def schedule_once(scheduler: BaseScheduler, func: Any, when: datetime, **kwargs: Any) -> Job:
    return scheduler.add_job(func, DateTrigger(when, timezone=config.settings.tz), **kwargs)


def schedule_cron(scheduler: BaseScheduler, func: Any, crontab: str, **kwargs: Any) -> Job:
    return scheduler.add_job(func, CronTrigger.from_crontab(crontab, timezone=config.settings.tz), **kwargs)
