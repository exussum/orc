import time
from collections import defaultdict
from collections import namedtuple as nt
from dataclasses import replace
from datetime import datetime, timedelta
from enum import Enum
from itertools import chain

from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger

from orc import config, dal
from orc import model as m

SnapShot = nt("SnapShot", "routine end")
ThemeOverride = nt("ThemeOverride", "name start end")


def non_cron_jobs(scheduler):
    now = datetime.now(tz=config.TZ)
    return [e for e in scheduler.get_jobs() if not isinstance(e.trigger, CronTrigger) and e.trigger.run_date > now]


def unwrap_rule_container(f):
    def wrapper(*args):
        if isinstance(args[0], m.Routine | m.Configs):
            for e in args[0].items:
                f(*((e,) + args[1:]))
        elif len(args) > 1 and isinstance(args[1], m.Routine | m.Configs):
            for e in args[1].items:
                f(
                    *(
                        (
                            args[0],
                            e,
                        )
                        + args[2:]
                    )
                )
        else:
            f(*args)

    return wrapper


class ConfigManager:
    def __init__(self):
        self.snapshot = None
        self.theme_override = None

    def replace_config(self, target_config, end):
        now = datetime.now(tz=config.TZ)

        if not self.snapshot:
            self.snapshot = SnapShot(capture_lights(), end)

        execute(target_config)

    def resume(self, target_config):
        if self.snapshot and datetime.now(tz=config.TZ) <= self.snapshot.end:
            routine = self.snapshot.routine
        else:
            routine = target_config
        self.snapshot = None
        execute(routine)

    def update_snapshot(self, rule):
        what = [rule.what] if isinstance(rule.what, Enum) else rule.what
        items = {e.what: e for e in self.snapshot.routine.items}

        # Explode out the rule w/o creating a sub config explicitly
        items.update({e: replace(rule, what=e) for e in what})
        self.snapshot.routine.items = tuple(items.values())

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
                "day off" if next((e for e in market_schedule if e["date"] == today_iso and e["exchange"] == "NYSE"), None) else "work day"
            )
        else:
            theme_name = "day off"
        return theme_name

    @unwrap_rule_container
    def route_rule(self, rule, force):
        if rule.mandatory and self.snapshot:
            self.update_snapshot(rule)
            execute(rule)
        elif self.snapshot and datetime.now(tz=config.TZ) > self.snapshot.end:
            self.snapshot = None
            execute(rule)
        elif not self.snapshot or force:
            execute(rule)


@unwrap_rule_container
def execute(rule):
    what = [rule.what] if isinstance(rule.what, Enum) else rule.what
    sleep = time.sleep if len(what) > 1 else (lambda _: 1)
    for w in what:
        if w.value < 0:
            print(f"Device {w} not found")
        else:
            if isinstance(w, config.Light):
                (dal.set_light(w, brightness=rule.state) if isinstance(rule.state, int) else dal.set_light(w, on=rule.state == "on"))
            elif isinstance(w, config.Sound):
                dal.set_sound(w, rule.state)
            else:
                raise Exception("Unknown type")
            sleep(0.1)


def capture_lights():
    return m.Configs(*(dal.get_light_state(e) for e in config.Light))


def get_schedule(config_manager):
    result = []
    for x in range(2):
        now = datetime.now(tz=config.TZ) + timedelta(days=x)
        sun_result = dal.get_sun_cycle(now.date())
        sunrise = datetime.fromisoformat(sun_result["sunrise"]) + timedelta(minutes=30)
        sunset = datetime.fromisoformat(sun_result["sunset"])

        cfg = next((e for e in config.THEMES if e.name == config_manager.calculate_theme(now.date())))

        for e in cfg.configs:
            if e.when == "sunrise":
                time = sunrise
            elif e.when == "sunset":
                time = sunset
            else:
                time = now.replace(hour=e.when.hour, minute=e.when.minute, second=0)
            result.append((time.astimezone(config.TZ), e))

    return result


def _make_rule_lambda(config_manager, rule):
    """
    solves for:

    for e in range(2):
       lambda: print(e)
    """
    return lambda force=False: config_manager.route_rule(rule, force)


def setup_scheduler(scheduler, config_manager):
    def f():
        for time, rule in get_schedule(config_manager):
            scheduler.add_job(
                _make_rule_lambda(config_manager, rule),
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


def test(theme):
    time.sleep(1)
    for e in theme.items:
        execute(e)
        time.sleep(2)


def squish_configs(*configs):
    rules = defaultdict(list)
    for routine in configs:
        for rule in routine.items:

            what = [rule.what] if isinstance(rule.what, Enum) else rule.what
            for e in what:
                rules[e].append(m.Config(what=e, state=rule.state))

    return m.Configs(*tuple(chain.from_iterable(squish(e) for e in rules.values())))


def squish(items):
    if not items:
        return ()

    result = (items[-1],)
    if isinstance(result[0].state, int):
        return result

    for e in range(len(items) - 2, -1, -1):
        if isinstance(items[e].state, int):
            result = (items[e], result[0])
            break
    return result
