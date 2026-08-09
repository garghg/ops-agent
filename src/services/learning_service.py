from datetime import date, timedelta

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from src.db.models import (
    BacktestResult,
    CorrectionFactor,
    FactorHistory,
    ModelRegistry,
)
from src.schemas.anomaly import AnomalyType
from src.schemas.models import ModelVersion
from src.services.anomaly_service import persist_anomaly
from src.services.config_services import resolve_config
from src.services.forecast_service.config import PROMOTION_LOOKBACK_DAYS


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


def champion_eval(session: Session, tenant_id: str, challenger: str, as_of_date: date):
    from src.services.forecast_service.core import backtest
    from src.services.forecast_service.metrics import check_promotion_gate
    
    cur_champion = session.scalar(
        select(ModelRegistry.active_version).where(ModelRegistry.tenant_id == tenant_id)
    )

    if not cur_champion:
        cur_champion = ModelVersion.POISSON_GLM.value

    if cur_champion == challenger:
        return
    
    nested = session.begin_nested()

    start = as_of_date - timedelta(days=PROMOTION_LOOKBACK_DAYS)
    backtest(
        session,
        tenant_id,
        str(start),
        str(as_of_date),
        [cur_champion, challenger],
        True,
    )
    results = check_promotion_gate(
        session, tenant_id, str(as_of_date), cur_champion, challenger
    )

    nested.rollback()

    if not results:
        return

    result_row = BacktestResult(
        tenant_id=tenant_id,
        champion_version=cur_champion,
        challenger_version=challenger,
        start_date=start,
        end_date=as_of_date,
        passed=results["passed"],
        skill=results["skills"],
        wape=results["wape"],
        coverage_pct=results["coverage_pct"],
    )
    session.add(result_row)

    if results["passed"]:
        update_stmt = insert(ModelRegistry).values(
            tenant_id=tenant_id,
            active_version=challenger,
            previous_version=cur_champion,
            backtest_evidence=results,
            promoted_at=func.now(),
        )
        update_stmt = update_stmt.on_conflict_do_update(
            constraint="model_registry_tenant_key",
            set_={
                "active_version": update_stmt.excluded.active_version,
                "previous_version": update_stmt.excluded.previous_version,
                "backtest_evidence": update_stmt.excluded.backtest_evidence,
                "promoted_at": update_stmt.excluded.promoted_at,
            },
        )
        session.execute(update_stmt)

    session.commit()
