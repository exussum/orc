"""What the dal and the scheduler call into: device events, IOT runs, expiries and the crons. Any trigger these
cause is built here."""

from datetime import datetime
from typing import Any, assert_never

from orc import api, config
from orc import model as m
from orc.log import Message


def on_message(topic: str, payload: bytes) -> None:
    api.receive_mqtt(topic, payload, m.Broker(topic, origin="mqtt", log_source=m.LogSource.EXTERNAL))


def on_heard(person: str, when: datetime) -> None:
    api.mark_present([person], when, m.Query("ble", log_source=m.LogSource.SYSTEM))


def on_run(payload: m.IotJob, *, ctx: m.AppContext) -> None:
    match payload:
        case m.IotJob(rule, routine_id):
            trigger = m.Scheduled(routine_id, log_source=m.LogSource.ROUTINE)
            log = ctx.log
            entry = log.write(f"`{rule.name}`", trigger)
            log.last_action = entry.action
            with log.opened(entry):
                api.run_schedule_routine(rule, trigger, api.present_names())
        case _:
            assert_never(payload)


def on_expired(*, queue: Any, job: Any, ctx: m.AppContext) -> None:
    ctx.log.write(Message.TASK_EXPIRED.format(id=job.name or job.id), m.System("expired", log_source=m.LogSource.SYSTEM))


def on_cancelled(*, queue: Any, job: Any, ctx: m.AppContext) -> None:
    if job.id in config.ad_hoc_routines:
        ctx.log.write(Message.TASK_CANCELLED.format(id=job.name or job.id), m.Manual(job.id, log_source=m.LogSource.MANUAL))


def rebuild_iot_cron() -> None:
    api.rebuild_iot_schedule(m.Cron("iot", log_source=m.LogSource.SYSTEM))


def check_presence_cron() -> None:
    api.check_presence(m.Cron("presence", log_source=m.LogSource.SYSTEM))


def refresh_tag_keys_cron() -> None:
    api.refresh_tag_keys(m.Cron("findhub", log_source=m.LogSource.SYSTEM))


def refresh_stats_cron() -> None:
    api.refresh_retry_stats()
