import time
from collections import namedtuple as nt
from datetime import datetime, timedelta
from enum import Enum
from zoneinfo import ZoneInfo

from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger

from orc import config, dal

SnapShot = nt("SnapShot", "routine end")
ThemeOverride = nt("ThemeOverride", "name start end")


def non_cron_jobs(scheduler):
    now = datetime.now(tz=ZoneInfo("America/New_York"))
    return [e for e in scheduler.get_jobs() if not isinstance(e.trigger, CronTrigger) and e.trigger.run_date > now]

def pause_jobs(scheduler, up_to):
    for e in (j for j in non_cron_jobs(scheduler) if j.trigger.run_date < up_to):
        e.pause()

def resume_jobs(scheduler):
    for e in non_cron_jobs(scheduler):
        e.resume()


class ConfigManager:
    def __init__(self):
        self.snapshot = None
        self.theme_override = None

    def replace_config(self, target_config, end):
        now = datetime.now(tz=ZoneInfo("America/New_York"))

        if not self.snapshot or self.snapshot.end <= now:
            self.snapshot = SnapShot(capture_lights(), end)

        execute(target_config)

    def resume(self, target_config):
        if self.snapshot and datetime.now(tz=ZoneInfo("America/New_York")) < self.snapshot.end:
            execute(self.snapshot.routine)
            self.snapshot = None
        else:
            execute(target_config)

    def set_theme_override(self, name, start, end):
        self.theme_override = ThemeOverride(name, start, end)

    def calculate_theme(self, today):
        if self.theme_override:
            if self.theme_override.start <= today <= self.theme_override.end:
                return self.theme_override.name
            elif self.theme_override.end < today:
                self.theme_override = None

        if today.weekday() not in (5, 6):
            today_iso = today.strftime("%Y-%m-%d")
            market_schedule = dal.get_holidays()
            theme_name = (
                "holiday"
                if next((e for e in market_schedule if e["date"] == today_iso and e["exchange"] == "NYSE"), None)
                else "work day"
            )
        else:
            theme_name = "day off"
        return theme_name


def capture_lights():
    return config.RoutineConfig(items=[dal.get_light_state(e) for e in config.Light])


def get_schedule(config_manager):
    timezone = ZoneInfo("America/New_York")
    result = []
    for x in range(2):
        now = datetime.now(tz=timezone) + timedelta(days=x)
        sun_result = dal.get_sun_cycle(now.date())
        sunrise = datetime.fromisoformat(sun_result["sunrise"])
        sunset = datetime.fromisoformat(sun_result["sunset"])

        cfg = next((e for e in config.CONFIGS if e.name == config_manager.calculate_theme(now.date())))

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


def setup_scheduler(scheduler, config_manager):
    def f():
        for time, rule in get_schedule(config_manager):
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
