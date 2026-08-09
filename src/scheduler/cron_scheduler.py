from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger

from src.logging import setup_logging
from src.scheduler.jobs import (
    check_system_health,
    poll_autonomy,
    poll_models,
    poll_proposals,
    poll_shop_times,
    sweep_outbox,
)

scheduler = BlockingScheduler()

scheduler.add_job(poll_shop_times, CronTrigger(minute="*"))
scheduler.add_job(sweep_outbox, CronTrigger(hour="*"))
scheduler.add_job(check_system_health, CronTrigger(minute="*/5"))
scheduler.add_job(poll_proposals, CronTrigger(hour="*/2"))
scheduler.add_job(poll_autonomy, CronTrigger(hour="*"))
scheduler.add_job(poll_models, CronTrigger(day_of_week="0"))

if __name__ == "__main__":
    setup_logging()
    scheduler.start()
