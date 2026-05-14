import copy

from apscheduler.executors.pool import ThreadPoolExecutor

from orc import model as m


class ContextThreadPoolExecutor(ThreadPoolExecutor):
    def __init__(self, ctx: m.AppContext, max_workers=1):
        super().__init__(max_workers=max_workers)
        self.ctx = ctx

    def _do_submit_job(self, job, run_times):
        dispatch_job = copy.copy(job)
        dispatch_job.kwargs = {**job.kwargs, "ctx": self.ctx}
        return super()._do_submit_job(dispatch_job, run_times)

    def run_now(self, job, **extra_kwargs):
        return job.func(*job.args, ctx=self.ctx, **{**job.kwargs, **extra_kwargs})
