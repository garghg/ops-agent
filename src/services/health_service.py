from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from src.db.models.health import Heartbeat


def record_heartbeat(session: Session, consumer_name: str) -> None:
    stmt = insert(Heartbeat).values(
        consumer_name=consumer_name,
        last_heartbeat=datetime.now(UTC),
    )
    stmt = stmt.on_conflict_do_update(
        constraint="heartbeats_consumer_name_key",
        set_={"last_heartbeat": stmt.excluded.last_heartbeat},
    )
    session.execute(stmt)
    session.commit()
    
def check_heartbeats(session: Session, stale_minutes: int = 10) -> list[str]:
    threshold = datetime.now(UTC) - timedelta(minutes=stale_minutes)
    
    stale = session.scalars(
        select(Heartbeat)
        .where(Heartbeat.last_heartbeat < threshold)
    ).all()
    
    return [h.consumer_name for h in stale]