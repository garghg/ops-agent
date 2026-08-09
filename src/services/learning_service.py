from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.db.models import CorrectionFactor, FactorHistory
from src.schemas.anomaly import AnomalyType
from src.services.anomaly_service import persist_anomaly
from src.services.config_services import resolve_config


def update_factor(
    session: Session,
    tenant_id: str,
    kind: str,
    scope_key: str,
    observation: float,
    half_life: int,
    clamp_low: float,
    clamp_high: float,
    business_date: date,
    clamp_alert_threshold: int = 5,
    default_value: float = 1.0,
):
    row = session.scalar(
        select(CorrectionFactor)
        .where(CorrectionFactor.tenant_id == tenant_id)
        .where(CorrectionFactor.kind == kind)
        .where(CorrectionFactor.scope_key == scope_key)
    )

    if not row:
        row = CorrectionFactor(
            tenant_id=tenant_id,
            kind=kind,
            scope_key=scope_key,
            value=default_value,
            clamp_low=clamp_low,
            clamp_high=clamp_high,
            half_life=half_life,
        )

        session.add(row)
        session.flush()

    alpha = 1 - 0.5 ** (1 / half_life)
    old_value = float(row.value)
    raw = old_value + alpha * (observation - old_value)
    
    if row.evidence_count == 0:
        raw = observation

    clamped = False
    clamp_hit = None

    if raw < clamp_low:
        raw = clamp_low
        clamped = True
        clamp_hit = "low"
    elif raw > clamp_high:
        raw = clamp_high
        clamped = True
        clamp_hit = "high"

    row.value = raw
    row.evidence_count += 1

    if clamped:
        row.consecutive_clamps += 1
        if row.consecutive_clamps >= clamp_alert_threshold:
            config = resolve_config(tenant_id, session)
            severity = (
                1 if AnomalyType.CONDITION_CHANGE in config.anomalies.tier1_types else 2
            )
            persist_anomaly(
                session,
                tenant_id,
                AnomalyType.CONDITION_CHANGE,
                f"{kind}:{scope_key}",
                severity,
                business_date,
                {
                    "kind": kind,
                    "scope_key": scope_key,
                    "current_value": float(raw),
                    "clamp_hit": clamp_hit,
                    "consecutive_clamps": row.consecutive_clamps,
                    "last_observation": float(observation),
                },
                f"Correction factor {kind}:{scope_key} has been pinned at its {clamp_hit} bound ({float(raw)}) for {row.consecutive_clamps} consecutive updates. Possible condition change.",
                config.anomalies.cooldown_hours if config else 48,
            )
    else:
        row.consecutive_clamps = 0

    session.add(
        FactorHistory(
            tenant_id=tenant_id,
            correction_factor_id=row.id,
            old_value=old_value,
            new_value=raw,
            observation=observation,
            clamped=clamped,
        )
    )

    session.commit()


def get_factor(
    session: Session, tenant_id: str, kind: str, scope_key: str, default: float = 1.0
):
    row = session.scalar(
        select(CorrectionFactor)
        .where(CorrectionFactor.tenant_id == tenant_id)
        .where(CorrectionFactor.kind == kind)
        .where(CorrectionFactor.scope_key == scope_key)
    )

    if not row:
        return default

    return row.value


def reset_factor(
    session: Session, tenant_id: str, kind: str, scope_key: str, default: float = 1.0
):
    row = session.scalar(
        select(CorrectionFactor)
        .where(CorrectionFactor.tenant_id == tenant_id)
        .where(CorrectionFactor.kind == kind)
        .where(CorrectionFactor.scope_key == scope_key)
    )
    
    if not row:
        return
    
    old_value = row.value
    row.value = default
    row.evidence_count = 0
    row.consecutive_clamps = 0
    
    session.add(
        FactorHistory(
            tenant_id=tenant_id,
            correction_factor_id=row.id,
            old_value=old_value,
            new_value=default,
            observation=default,
            clamped=False,
        )
    )
    
    session.commit()