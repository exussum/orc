import time

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.schedulers.blocking import BlockingScheduler
from flask import Flask
from flask_admin import BaseView, expose
from flask_admin.base import Admin, AdminIndexView

from orc.control import build_schedule, execute, setup_scheduler


def web():
    app = Flask(__name__)
    app.config["FLASK_ADMIN_SWATCH"] = "Slate"

    class OrcAdminView(AdminIndexView):
        @expose("/")
        def index(self):
            return self.render("admin/orc.html")

    admin = Admin(app, name="ORChestration", template_mode="bootstrap3", index_view=OrcAdminView(url="/"))
    setup_scheduler(BackgroundScheduler()).start()
    app.run()


def worker():
    scheduler = setup_scheduler(BlockingScheduler())

    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        pass


def test():
    for when, e in sorted(build_schedule(), key=lambda e: e[0]):
        print(e)
        time.sleep(1)
        execute(e)
        time.sleep(1)
