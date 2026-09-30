from typing import Any

import example
from example.dal.sqlite import Connection
from example.model import ExampleJob, Plan, Runtime
from orc.kernel import cast
from orc.model import AppContext, DeviceStatus, Scheduler
from orc.plugins import requires_ctx


def runtime(ctx: AppContext) -> Runtime:
    """Shared live state goes in ctx.plugin_state keyed by the plugin's module,
    stored once by setup(); constant wiring travels as arguments or partials."""
    return cast.instance(ctx.plugin_state[example], Runtime)


def widget_names() -> list[str]:
    raise NotImplementedError


def zone_names() -> list[str]:
    raise NotImplementedError


def resolve_target(target: str | None) -> str | None:
    pass


def plan(rt: Runtime, job: ExampleJob, tz: Any, connection: Connection) -> Plan:
    raise NotImplementedError


def schedule(scheduler: Scheduler, job: ExampleJob, tz: Any) -> None:
    pass


def _dispatch(ctx: AppContext, w: Any, command: Any, stream: dict[Any, tuple[str, str]]) -> None:
    pass


def status() -> list[DeviceStatus]:
    raise NotImplementedError


@requires_ctx
def run_job(job: ExampleJob, *, ctx: AppContext) -> None:
    pass
