from datetime import date, time, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select

from src.db.models import (
    AvailabilityException,
    AvailabilityRule,
    Employee,
    Forecast,
    IntradayProfile,
    Schedule,
    Shift,
    Tenant,
)
from src.schemas.forecast import ForecastSeries
from src.schemas.models import ModelVersion
from src.schemas.schedule import ScheduleStatus
from src.services.scheduling_service import (
    _time_to_minutes,
    make_schedule,
    required_per_slot,
)


@pytest.fixture
def tenant(seeded_db):
    return seeded_db.scalar(select(Tenant).limit(1))


@pytest.fixture
def staff(seeded_db, tenant):
    employees = []
    for _, (name, keyholder) in enumerate([
        ("Alice", True),
        ("Bob", False),
        ("Carol", False),
        ("Dave", True),
    ]):
        emp = Employee(
            tenant_id=tenant.id,
            name=name,
            is_keyholder=keyholder,
            max_weekly_hours=40,
            min_shift_hours=3,
            max_shift_hours=8,
        )
        seeded_db.add(emp)
        seeded_db.flush()
        employees.append(emp)

        for dow in range(6):
            seeded_db.add(AvailabilityRule(
                employee_id=emp.id,
                day_of_week=dow,
                start_time=time(10, 0),
                end_time=time(18, 0),
            ))

    seeded_db.flush()
    return employees


@pytest.fixture
def forecast_data(seeded_db, tenant):
    week_start = date(2026, 8, 10)
    as_of = week_start - timedelta(days=1)

    for d in range(7):
        day = week_start + timedelta(days=d)
        seeded_db.add(Forecast(
            tenant_id=tenant.id,
            series=ForecastSeries.TOTAL_UNITS,
            target_date=day,
            model_version=ModelVersion.POISSON_GLM.value,
            point_estimate=Decimal("120.00"),
            forecast_date=as_of,
        ))

    for dow in range(7):
        for hour in range(10, 18):
            seeded_db.add(IntradayProfile(
                tenant_id=tenant.id,
                day_of_week=dow,
                hour=hour,
                fraction=Decimal("0.125"),
                as_of_date=as_of,
            ))

    seeded_db.flush()
    return week_start


class TestRequiredPerSlot:
    def test_returns_grid_for_each_slot(self, seeded_db, tenant, forecast_data):
        grid = required_per_slot(seeded_db, str(tenant.id), forecast_data)
        assert grid is not None
        # 7 days × 8 hours = 224 slots (at default 15-min slot length)
        assert len(grid) == 224

    def test_values_are_at_least_min_staffing(self, seeded_db, tenant, forecast_data):
        grid = required_per_slot(seeded_db, str(tenant.id), forecast_data)
        for val in grid.values():
            assert val >= 1

    def test_returns_none_without_forecasts(self, seeded_db, tenant):
        result = required_per_slot(seeded_db, str(tenant.id), date(2026, 8, 10))
        assert result is None

    def test_returns_none_without_profiles(self, seeded_db, tenant):
        week_start = date(2026, 8, 10)
        seeded_db.add(Forecast(
            tenant_id=tenant.id,
            series=ForecastSeries.TOTAL_UNITS,
            target_date=week_start,
            model_version=ModelVersion.POISSON_GLM.value,
            point_estimate=Decimal("100.00"),
            forecast_date=week_start - timedelta(days=1),
        ))
        seeded_db.flush()
        result = required_per_slot(seeded_db, str(tenant.id), week_start)
        assert result is None


class TestMakeSchedule:
    def test_produces_proposed_schedule(self, seeded_db, tenant, staff, forecast_data):
        result = make_schedule(seeded_db, str(tenant.id), forecast_data)
        assert result["status"] == ScheduleStatus.PROPOSED.value

        schedule = seeded_db.scalar(
            select(Schedule).where(Schedule.tenant_id == tenant.id)
        )
        assert schedule is not None
        assert schedule.status == ScheduleStatus.PROPOSED.value

    def test_creates_shifts(self, seeded_db, tenant, staff, forecast_data):
        make_schedule(seeded_db, str(tenant.id), forecast_data)

        schedule = seeded_db.scalar(
            select(Schedule).where(Schedule.tenant_id == tenant.id)
        )
        shifts = seeded_db.scalars(
            select(Shift).where(Shift.schedule_id == schedule.id)
        ).all()
        assert len(shifts) > 0

    def test_shifts_respect_availability(self, seeded_db, tenant, staff, forecast_data):
        make_schedule(seeded_db, str(tenant.id), forecast_data)

        schedule = seeded_db.scalar(
            select(Schedule).where(Schedule.tenant_id == tenant.id)
        )
        shifts = seeded_db.scalars(
            select(Shift).where(Shift.schedule_id == schedule.id)
        ).all()

        for shift in shifts:
            assert shift.shift_date.weekday() != 6

    def test_shifts_respect_min_shift_length(self, seeded_db, tenant, staff, forecast_data):
        make_schedule(seeded_db, str(tenant.id), forecast_data)

        schedule = seeded_db.scalar(
            select(Schedule).where(Schedule.tenant_id == tenant.id)
        )
        shifts = seeded_db.scalars(
            select(Shift).where(Shift.schedule_id == schedule.id)
        ).all()

        for shift in shifts:
            duration_mins = _time_to_minutes(shift.end_time) - _time_to_minutes(shift.start_time)
            assert duration_mins >= 3 * 60

    def test_shifts_respect_max_shift_length(self, seeded_db, tenant, staff, forecast_data):
        make_schedule(seeded_db, str(tenant.id), forecast_data)

        schedule = seeded_db.scalar(
            select(Schedule).where(Schedule.tenant_id == tenant.id)
        )
        shifts = seeded_db.scalars(
            select(Shift).where(Shift.schedule_id == schedule.id)
        ).all()

        for shift in shifts:
            duration_mins = _time_to_minutes(shift.end_time) - _time_to_minutes(shift.start_time)
            assert duration_mins <= 8 * 60

    def test_respects_exception_block(self, seeded_db, tenant, staff, forecast_data):
        monday = forecast_data
        seeded_db.add(AvailabilityException(
            employee_id=staff[0].id,
            exception_date=monday,
            is_available=False,
        ))
        seeded_db.flush()

        make_schedule(seeded_db, str(tenant.id), forecast_data)

        schedule = seeded_db.scalar(
            select(Schedule).where(Schedule.tenant_id == tenant.id)
        )
        shifts = seeded_db.scalars(
            select(Shift).where(Shift.schedule_id == schedule.id)
        ).all()

        alice_monday = [
            s for s in shifts
            if s.employee_id == staff[0].id and s.shift_date == monday
        ]
        assert len(alice_monday) == 0

    def test_fails_with_no_employees(self, seeded_db, tenant, forecast_data):
        result = make_schedule(seeded_db, str(tenant.id), forecast_data)
        assert result["status"] == ScheduleStatus.FAILED.value

    def test_fails_with_no_availability(self, seeded_db, tenant, forecast_data):
        seeded_db.add(Employee(
            tenant_id=tenant.id,
            name="Lonely",
            max_weekly_hours=40,
            min_shift_hours=3,
            max_shift_hours=8,
        ))
        seeded_db.flush()
        result = make_schedule(seeded_db, str(tenant.id), forecast_data)
        assert result["status"] == ScheduleStatus.FAILED.value

    def test_shortfall_reported_when_understaffed(self, seeded_db, tenant, forecast_data):
        emp = Employee(
            tenant_id=tenant.id,
            name="Solo",
            max_weekly_hours=10,
            min_shift_hours=3,
            max_shift_hours=5,
        )
        seeded_db.add(emp)
        seeded_db.flush()

        for dow in range(6):
            seeded_db.add(AvailabilityRule(
                employee_id=emp.id,
                day_of_week=dow,
                start_time=time(10, 0),
                end_time=time(18, 0),
            ))
        seeded_db.flush()

        result = make_schedule(seeded_db, str(tenant.id), forecast_data)
        assert result["shortfalls"]