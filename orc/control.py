import time
from datetime import datetime, timedelta
from enum import Enum
from zoneinfo import ZoneInfo

from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger

from orc import config, dal


def calculate_theme(today):
    if today.weekday() not in (5, 6):
        today_iso = today.strftime("%Y-%m-%d")
        market_schedule = dal.get_holidays()
        theme_name = (
            "holiday"
            if next((e for e in market_schedule if e["date"] == today_iso and e["exchange"] == "NYSE"), None)
            else "non-holiday"
        )
    else:
        theme_name = "holiday"
    return theme_name


def capture_lights():
    return config.RoutineConfig(items=[dal.get_light_state(e) for e in config.Light])


def build_schedule():
    timezone = ZoneInfo("America/New_York")
    result = []
    for x in range(2):
        now = datetime.now(tz=timezone) + timedelta(days=x)
        sun_result = dal.get_sun_cycle(now.date())
        sunrise = datetime.fromisoformat(sun_result["sunrise"])
        sunset = datetime.fromisoformat(sun_result["sunset"])

        theme = calculate_theme(now.date())
        cfg = next((e for e in config.CONFIGS if e.days == theme))

        for e in cfg.configs:
            if e.when == "sunrise":
                time = sunrise
            elif e.when == "sunset":
                time = sunset
            else:
                time = now.replace(hour=e.when.hour, minute=e.when.minute, second=0)
            time = time + e.offset
            result.append((time.astimezone(timezone), e))

    return result


def execute(rule):
    if isinstance(rule, config.RoutineConfig):
        for e in rule.items:
            execute(e)
    else:
        what = [rule.what] if isinstance(rule.what, Enum) else rule.what
        sleep = time.sleep if len(what) > 1 else (lambda _: 1)
        for w in what:
            if w.value < 0:
                print(f"Device {w} not found")
            else:
                if isinstance(rule, config.LightConfig):
                    (
                        dal.set_light(w, brightness=rule.state)
                        if isinstance(rule.state, int)
                        else dal.set_light(w, on=rule.state == "on")
                    )
                else:
                    (cast_initialize(w) if rule.state == "initialize" else dal.set_sound(w, rule.state))
                sleep(0.1)


def _make_rule_lambda(rule):
    """
    solves for:

    for e in range(2):
       lambda: print(e)
    """
    return lambda: execute(rule)


def setup_scheduler(scheduler):
    def f():
        for time, rule in build_schedule():
            scheduler.add_job(
                _make_rule_lambda(rule),
                DateTrigger(time),
                name=rule.name,
                id=f"{rule.name}-{time.date().isoformat()}",
                replace_existing=True,
            )

    f()
    scheduler.add_job(
        f,
        CronTrigger.from_crontab("10 0 * * *"),
        name="schedule todays events",
        id="schedule todays events",
        replace_existing=True,
    )
    return scheduler
