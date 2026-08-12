from datetime import date, time

import pytest
from sqlalchemy import select

from src.db.models import (
    CorrectionFactor,
    Employee,
    Schedule,
    ScheduleEdit,
    Shift,
    Tenant,
)
from src.schemas.schedule import (
    DayPart,
    ScheduleEditType,
    ScheduleStatus,
)
from src.services.scheduling_service import calibrate_staffing


@pytest.fixture
def tenant(seeded_db):
    return seeded_db.scalar(select(Tenant).limit(1))


@pytest.fixture
def published_schedule(seeded_db, tenant):
    week_start = date(2026, 8, 10)

    schedule = Schedule(
        tenant_id=tenant.id,
        week_start=week_start,
        status=ScheduleStatus.PUBLISHED.value,
    )
    seeded_db.add(schedule)
    seeded_db.flush()

    emp_a = Employee(
        tenant_id=tenant.id,
        name="Alice",
        max_weekly_hours=40,
        min_shift_hours=3,
        max_shift_hours=8,
    )
    emp_b = Employee(
        tenant_id=tenant.id,
        name="Bob",
        max_weekly_hours=40,
        min_shift_hours=3,
        max_shift_hours=8,
    )
    emp_c = Employee(
        tenant_id=tenant.id,
        name="Carol",
        max_weekly_hours=40,
        min_shift_hours=3,
        max_shift_hours=8,
    )
    seeded_db.add_all([emp_a, emp_b, emp_c])
    seeded_db.flush()

    for emp in [emp_a, emp_b]:
        seeded_db.add(Shift(
            schedule_id=schedule.id,
            employee_id=emp.id,
            shift_date=week_start,
            start_time=time(13, 0),
            end_time=time(17, 0),
        ))

    seeded_db.flush()
    return schedule, week_start, [emp_a, emp_b, emp_c]


class TestCalibrateStaffing:
    def test_add_edit_increases_factor(self, seeded_db, tenant, published_schedule):
        schedule, week_start, employees = published_schedule

        seeded_db.add(Shift(
            schedule_id=schedule.id,
            employee_id=employees[2].id,
            shift_date=week_start,
            start_time=time(13, 0),
            end_time=time(17, 0),
        ))
        seeded_db.add(ScheduleEdit(
            schedule_id=schedule.id,
            edit_type=ScheduleEditType.SHIFT_ADD.value,
            details={
                "employee_id": str(employees[2].id),
                "employee_name": "Carol",
                "shift_date": week_start.isoformat(),
                "start_time": "13:00",
                "end_time": "17:00",
            },
        ))
        seeded_db.flush()

        calibrate_staffing(seeded_db, str(tenant.id), week_start)

        factor = seeded_db.scalar(
            select(CorrectionFactor)
            .where(CorrectionFactor.tenant_id == tenant.id)
            .where(CorrectionFactor.kind == "staffing_ratio")
            .where(CorrectionFactor.scope_key == f"0:{DayPart.AFTERNOON.value}")
        )
        assert factor is not None
        assert float(factor.value) > 1.0

    def test_remove_edit_decreases_factor(self, seeded_db, tenant, published_schedule):
        schedule, week_start, employees = published_schedule

        shift_to_remove = seeded_db.scalar(
            select(Shift)
            .where(Shift.schedule_id == schedule.id)
            .where(Shift.employee_id == employees[1].id)
        )
        seeded_db.delete(shift_to_remove)
        seeded_db.add(ScheduleEdit(
            schedule_id=schedule.id,
            edit_type=ScheduleEditType.SHIFT_REMOVE.value,
            details={
                "employee_id": str(employees[1].id),
                "employee_name": "Bob",
                "shift_date": week_start.isoformat(),
                "start_time": "13:00",
                "end_time": "17:00",
            },
        ))
        seeded_db.flush()

        calibrate_staffing(seeded_db, str(tenant.id), week_start)

        factor = seeded_db.scalar(
            select(CorrectionFactor)
            .where(CorrectionFactor.tenant_id == tenant.id)
            .where(CorrectionFactor.kind == "staffing_ratio")
            .where(CorrectionFactor.scope_key == f"0:{DayPart.AFTERNOON.value}")
        )
        assert factor is not None
        assert float(factor.value) < 1.0

    def test_net_zero_produces_no_update(self, seeded_db, tenant, published_schedule):
        schedule, week_start, employees = published_schedule

        seeded_db.add(Shift(
            schedule_id=schedule.id,
            employee_id=employees[2].id,
            shift_date=week_start,
            start_time=time(13, 0),
            end_time=time(17, 0),
        ))
        seeded_db.add(ScheduleEdit(
            schedule_id=schedule.id,
            edit_type=ScheduleEditType.SHIFT_ADD.value,
            details={
                "employee_id": str(employees[2].id),
                "employee_name": "Carol",
                "shift_date": week_start.isoformat(),
                "start_time": "13:00",
                "end_time": "17:00",
            },
        ))

        shift_to_remove = seeded_db.scalar(
            select(Shift)
            .where(Shift.schedule_id == schedule.id)
            .where(Shift.employee_id == employees[1].id)
        )
        seeded_db.delete(shift_to_remove)
        seeded_db.add(ScheduleEdit(
            schedule_id=schedule.id,
            edit_type=ScheduleEditType.SHIFT_REMOVE.value,
            details={
                "employee_id": str(employees[1].id),
                "employee_name": "Bob",
                "shift_date": week_start.isoformat(),
                "start_time": "13:00",
                "end_time": "17:00",
            },
        ))
        seeded_db.flush()

        calibrate_staffing(seeded_db, str(tenant.id), week_start)

        factor = seeded_db.scalar(
            select(CorrectionFactor)
            .where(CorrectionFactor.tenant_id == tenant.id)
            .where(CorrectionFactor.kind == "staffing_ratio")
        )
        assert factor is None

    def test_skips_when_original_below_minimum(self, seeded_db, tenant):
        week_start = date(2026, 8, 10)

        schedule = Schedule(
            tenant_id=tenant.id,
            week_start=week_start,
            status=ScheduleStatus.PUBLISHED.value,
        )
        seeded_db.add(schedule)
        seeded_db.flush()

        emp = Employee(
            tenant_id=tenant.id,
            name="Solo",
            max_weekly_hours=40,
            min_shift_hours=3,
            max_shift_hours=8,
        )
        seeded_db.add(emp)
        seeded_db.flush()

        # 2 shifts exist, but both were added manually (net +2, original = 0)
        for i, start_hour in enumerate([13, 14]):
            seeded_db.add(Shift(
                schedule_id=schedule.id,
                employee_id=emp.id,
                shift_date=week_start,
                start_time=time(start_hour, 0),
                end_time=time(start_hour + 3, 0),
            ))
            seeded_db.add(ScheduleEdit(
                schedule_id=schedule.id,
                edit_type=ScheduleEditType.SHIFT_ADD.value,
                details={
                    "employee_id": str(emp.id),
                    "employee_name": "Solo",
                    "shift_date": week_start.isoformat(),
                    "start_time": f"{start_hour}:00",
                    "end_time": f"{start_hour + 3}:00",
                },
            ))
        seeded_db.flush()

        calibrate_staffing(seeded_db, str(tenant.id), week_start)

        factor = seeded_db.scalar(
            select(CorrectionFactor)
            .where(CorrectionFactor.tenant_id == tenant.id)
            .where(CorrectionFactor.kind == "staffing_ratio")
        )
        assert factor is None

    def test_no_edits_returns_early(self, seeded_db, tenant):
        week_start = date(2026, 8, 10)
        schedule = Schedule(
            tenant_id=tenant.id,
            week_start=week_start,
            status=ScheduleStatus.PUBLISHED.value,
        )
        seeded_db.add(schedule)
        seeded_db.flush()

        calibrate_staffing(seeded_db, str(tenant.id), week_start)

        factor = seeded_db.scalar(
            select(CorrectionFactor)
            .where(CorrectionFactor.tenant_id == tenant.id)
            .where(CorrectionFactor.kind == "staffing_ratio")
        )
        assert factor is None