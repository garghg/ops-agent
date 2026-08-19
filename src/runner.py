import signal
import sys
import threading

from src.consumers.anomaly_consumer import anomaly_consumer
from src.consumers.bom_consumer import bom_consumer
from src.consumers.email_consumer import email_consumer
from src.consumers.forecast_consumer import forecast_consumer
from src.consumers.sales_consumer import sales_consumer
from src.consumers.stock_updater import stock_updater
from src.consumers.summary_consumer import summary_consumer
from src.consumers.weather_consumer import weather_consumer
from src.logging import get_logger, setup_logging
from src.scheduler.cron_scheduler import scheduler

log = get_logger("runner")

CONSUMERS = [
    ("stock_updater", stock_updater),
    ("sales_consumer", sales_consumer),
    ("bom_consumer", bom_consumer),
    ("email_consumer", email_consumer),
    ("forecast_consumer", forecast_consumer),
    ("anomaly_consumer", anomaly_consumer),
    ("summary_consumer", summary_consumer),
    ("weather_consumer", weather_consumer),
]


def run():
    setup_logging()
    log.info("starting_system", consumers=len(CONSUMERS))

    for name, fn in CONSUMERS:
        t = threading.Thread(target=fn, name=name, daemon=True)
        t.start()
        log.info("consumer_started", consumer=name)

    def shutdown(sig, frame):
        log.info("shutdown_signal_received")
        scheduler.shutdown(wait=False)
        sys.exit(0)

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    log.info("scheduler_starting")
    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        log.info("system_stopped")


if __name__ == "__main__":
    run()