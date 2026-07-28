from src.db.session import SessionLocal
from src.logging import get_logger
from src.services.health_service import check_heartbeats

log = get_logger("health_check")


def check_system_health() -> None:
    with SessionLocal() as session:
        stale = check_heartbeats(session)

    if stale:
        log.warning("stale_consumers", consumers=stale)
    else:
        log.info("all_consumers_healthy")