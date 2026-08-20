from typing import Any

from apscheduler.schedulers.base import BaseScheduler
from orc_extras.example.dal.sqlite import Connection
from orc_extras.example.model import ExampleJob, Plan, Runtime

from orc.model import AppContext
from orc.plugins import requires_ctx

_runtime: Runtime | None = None


def set_runtime(runtime: Runtime) -> None:
    global _runtime
    _runtime = runtime


def widget_names() -> list[str]:
    raise NotImplementedError


def zone_names() -> list[str]:
    raise NotImplementedError


def resolve_target(target: str | None) -> str | None:
    pass


def plan(rt: Runtime, job: ExampleJob, tz: Any, connection: Connection) -> Plan:
    raise NotImplementedError


def schedule(scheduler: BaseScheduler, job: ExampleJob, tz: Any) -> None:
    pass


def _dispatch(ctx: AppContext, w: Any, rule: Any, stream: dict[Any, tuple[str, str]]) -> None:
    pass


def status() -> list[dict[str, Any]]:
    raise NotImplementedError


@requires_ctx
def run_job(job: ExampleJob, *, ctx: AppContext) -> None:
    pass
