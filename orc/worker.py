from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger
import time
from enum import Enum
from orc import control


def add_day_jobs(scheduler):
    for time, f, _ in control.build_schedule():
        scheduler.add_job(f, DateTrigger(time))


def main():
    scheduler = BlockingScheduler()

    def f():
        add_day_jobs(scheduler)

    f()
    scheduler.add_job(f, CronTrigger.from_crontab("30 0 * * *"))

    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        pass


def test():
    for when, e in sorted(control.build_schedule(), key=lambda e: e[0]):
        print(e)
        time.sleep(1)
        control.execute(e)
        time.sleep(1)
