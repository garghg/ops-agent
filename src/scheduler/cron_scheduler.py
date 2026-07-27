from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger

from src.logging import setup_logging
from src.scheduler.jobs import poll_shop_times, sweep_outbox

scheduler = BlockingScheduler()

scheduler.add_job(poll_shop_times, CronTrigger(minute="*"))
scheduler.add_job(sweep_outbox, CronTrigger(hour="*"))

if __name__ == "__main__":
    setup_logging()
    scheduler.start()
