from enum import Enum
from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger
from orc import control


def add_day_jobs(scheduler):
    for time, f in control.build_schedule():
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
