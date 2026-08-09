from datetime import datetime, time, timedelta
from statistics import median
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.db.models import AutonomyEvent, CapabilityState, POEvent, PurchaseOrder, Tenant
from src.schemas.autonomy import AutonomyEventType, AutonomyState
from src.schemas.orders import OrderBy
from src.schemas.suppliers import POStatus
from src.services.ordering_service.config import (
    APPROVAL_THRESHOLD,
    CONSECUTIVE_REJECTS,
    CRITICAL_FAILURE,
    EDIT_MEDIAN,
    MAX_EDIT,
    PROPOSAL_COUNT,
    SPAN_DAYS,
)


def rollup(session: Session, tenant_id: str, supplier_id: str) -> dict:
    proposals = session.scalars(
        select(POEvent)
        .join(PurchaseOrder, POEvent.purchase_order_id == PurchaseOrder.id)
        .where(POEvent.tenant_id == tenant_id)
        .where(PurchaseOrder.supplier_id == supplier_id)
        .order_by(POEvent.created_at.desc())
    ).all()

    if not proposals:
        return

    count = sum(
        1
        for p in proposals
        if p.from_status is None
        and p.to_status == POStatus.PROPOSED.value
        and p.changed_by == OrderBy.SYSTEM.value
    )

    owner_approvals = sum(
        1
        for p in proposals
        if p.from_status == POStatus.PROPOSED.value
        and p.to_status == POStatus.APPROVED.value
        and p.changed_by == OrderBy.OWNER.value
    )

    owner_rejections = sum(
        1
        for p in proposals
        if (p.from_status in [POStatus.PROPOSED.value, POStatus.APPROVED.value])
        and p.to_status == POStatus.CANCELLED.value
        and p.changed_by == OrderBy.OWNER.value
    )

    approval_rate = None
    if owner_rejections + owner_approvals != 0:
        approval_rate = owner_approvals / (owner_approvals + owner_rejections)

    edited = []

    for p in proposals:
        if (
            p.edits is not None
            and p.changed_by == OrderBy.OWNER.value
            and p.from_status == POStatus.PROPOSED.value
            and p.to_status == POStatus.PROPOSED.value
        ):
            for e in p.edits:
                if e["from"] != 0:
                    edited.append(abs(e["to"] - e["from"]) / e["from"])

    edit_median = None
    max_edited = None
    if edited:
        edit_median = median(edited)
        max_edited = max(edited)

    consecutive_rejects = 0
    for p in proposals:
        is_rejection = (
            p.from_status in [POStatus.PROPOSED.value, POStatus.APPROVED.value]
            and p.to_status == POStatus.CANCELLED.value
            and p.changed_by == OrderBy.OWNER.value
        )
        is_approval = (
            p.from_status == POStatus.PROPOSED.value
            and p.to_status == POStatus.APPROVED.value
            and p.changed_by == OrderBy.OWNER.value
        )
        if is_rejection:
            consecutive_rejects += 1
        elif is_approval:
            break

    critical_failures = sum(
        1
        for p in proposals
        if p.from_status == POStatus.APPROVED.value
        and p.to_status == POStatus.PROPOSED.value
        and p.changed_by == OrderBy.SYSTEM.value
    )

    span_days = (proposals[0].created_at - proposals[-1].created_at).days

    return {
        "proposal_count": count,
        "span_days": span_days,
        "approval_rate": approval_rate,
        "edit_median": edit_median,
        "max_edit": max_edited,
        "consecutive_rejects": consecutive_rejects,
        "critical_failures": critical_failures,
    }


def evaluate_promotion(session: Session, tenant_id: str, supplier_id: str):
    timezone = session.scalar(select(Tenant.timezone).where(Tenant.id == tenant_id))
    local_today = datetime.now(ZoneInfo(timezone)).date()
    local_start = datetime.combine(local_today, time.min, tzinfo=ZoneInfo(timezone))
    local_end = local_start + timedelta(days=1)

    already_proposed = session.scalar(
        select(AutonomyEvent.id)
        .where(AutonomyEvent.tenant_id == tenant_id)
        .where(AutonomyEvent.supplier_id == supplier_id)
        .where(AutonomyEvent.event_type == AutonomyEventType.PROMOTION_PROPOSED.value)
        .where(AutonomyEvent.created_at >= local_start)
        .where(AutonomyEvent.created_at < local_end)
    )

    if already_proposed:
        return
    
    stats = rollup(session, tenant_id, supplier_id)
    if not stats:
        return

    state = session.scalar(
        select(CapabilityState.state)
        .where(CapabilityState.tenant_id == tenant_id)
        .where(CapabilityState.supplier_id == supplier_id)
    )

    if state == AutonomyState.AUTO_WITHIN_BOUNDS.value:
        return

    gates = (
        stats["proposal_count"] >= PROPOSAL_COUNT
        and stats["span_days"] >= SPAN_DAYS
        and stats["approval_rate"] is not None
        and stats["approval_rate"] >= APPROVAL_THRESHOLD
        and (stats["edit_median"] is None or stats["edit_median"] <= EDIT_MEDIAN)
        and stats["critical_failures"] == CRITICAL_FAILURE
    )

    if not gates:
        return

    evidence = ", ".join(f"{k}: {v}" for k, v in stats.items())

    session.add(
        AutonomyEvent(
            tenant_id=tenant_id,
            supplier_id=supplier_id,
            event_type=AutonomyEventType.PROMOTION_PROPOSED.value,
            from_state=AutonomyState.PROPOSE_ONLY.value,
            to_state=AutonomyState.PROPOSE_ONLY.value,
            reason=f"Promotion eligible - {evidence}",
        )
    )
    session.commit()


def evaluate_demotion(session: Session, tenant_id: str, supplier_id: str):
    stats = rollup(session, tenant_id, supplier_id)
    if not stats:
        return

    state = session.scalar(
        select(CapabilityState)
        .where(CapabilityState.tenant_id == tenant_id)
        .where(CapabilityState.supplier_id == supplier_id)
    )

    if not state or state.state == AutonomyState.PROPOSE_ONLY.value:
        return

    reasons = []
    if stats["consecutive_rejects"] >= CONSECUTIVE_REJECTS:
        reasons.append(f"rejection streak: {stats['consecutive_rejects']}")
    if stats["max_edit"] is not None and stats["max_edit"] > MAX_EDIT:
        reasons.append(f"max edit magnitude: {stats['max_edit']:.0%}")

    if not reasons:
        return

    state.state = AutonomyState.PROPOSE_ONLY.value

    session.add(
        AutonomyEvent(
            tenant_id=tenant_id,
            supplier_id=supplier_id,
            event_type=AutonomyEventType.DEMOTED.value,
            from_state=AutonomyState.AUTO_WITHIN_BOUNDS.value,
            to_state=AutonomyState.PROPOSE_ONLY.value,
            reason=f"Auto-demoted — {', '.join(reasons)}",
        )
    )
    session.commit()
