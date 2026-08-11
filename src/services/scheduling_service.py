import uuid
from datetime import date, time, timedelta
from itertools import groupby
from math import ceil

from ortools.sat.python import cp_model
from sqlalchemy import select
from sqlalchemy.orm import Session

from src.db.models import (
    AvailabilityException,
    AvailabilityRule,
    Employee,
    Forecast,
    IntradayProfile,
    ModelRegistry,
    Schedule,
    Shift,
)
from src.schemas.forecast import ForecastSeries
from src.schemas.models import ModelVersion
from src.schemas.schedule import Constraints, ScheduleStatus
from src.services.config_services import resolve_config


def _time_to_minutes(t: time) -> int:
    return t.hour * 60 + t.minute


def _minutes_to_time(m: int) -> time:
    return time(m // 60, m % 60)


def required_per_slot(
    session: Session, tenant_id: str, week_start: date
) -> dict | None:
    champion = session.scalar(
        select(ModelRegistry.active_version).where(ModelRegistry.tenant_id == tenant_id)
    )

    if not champion:
        champion = ModelVersion.POISSON_GLM.value

    forecasts = session.scalars(
        select(Forecast)
        .where(Forecast.tenant_id == tenant_id)
        .where(Forecast.target_date >= week_start)
        .where(Forecast.target_date <= week_start + timedelta(days=7))
        .where(Forecast.model_version == champion)
        .where(Forecast.series == ForecastSeries.TOTAL_UNITS)
        .order_by(Forecast.forecast_date.desc())
    ).all()

    if not forecasts:
        return

    latest_demand_date = session.scalar(
        select(IntradayProfile.as_of_date)
        .where(IntradayProfile.tenant_id == tenant_id)
        .order_by(IntradayProfile.as_of_date.desc())
        .limit(1)
    )

    if not latest_demand_date:
        return

    demands = session.scalars(
        select(IntradayProfile)
        .where(IntradayProfile.tenant_id == tenant_id)
        .where(IntradayProfile.as_of_date == latest_demand_date)
    ).all()

    if not demands:
        return

    forecast_map = {}
    for f in forecasts:
        if f.target_date not in forecast_map:
            forecast_map[f.target_date] = f.point_estimate
    demand_map = {(d.day_of_week, d.hour): d.fraction for d in demands}

    config = resolve_config(tenant_id, session)
    open_minutes = config.schedule.opening_hour * 60 + config.schedule.opening_min
    close_minutes = config.schedule.closing_hour * 60 + config.schedule.closing_min
    min_staffing = config.schedule.min_staffing
    service_rate = config.schedule.service_rate
    slot_length_mins = config.schedule.slot_length_minutes
    slots_per_hour = 60 // slot_length_mins
    required = {}

    for d in range(7):
        day = week_start + timedelta(days=d)
        forecast = forecast_map.get(day, 0)
        weekday = day.weekday()
        for slot in range(open_minutes, close_minutes, slot_length_mins):
            fraction = demand_map.get((weekday, slot // 60), 0)
            required[(day, slot)] = max(
                ceil(forecast * fraction / slots_per_hour / service_rate),
                min_staffing,
            )

    return required


def _solver(
    hashset: set,
    demand_grid: dict,
    employee_ids: list[uuid.UUID],
    employees: list[Employee],
    week_start: date,
    slot_length_minutes: int,
    skip_constraints: set[str] | None = None,
) -> tuple:
    model = cp_model.CpModel()
    skip = skip_constraints or set()

    x = {}
    for emp_id, day, slot in hashset:
        x[(emp_id, day, slot)] = model.new_bool_var(f"x_{emp_id}_{day}_{slot}")

    slacks = {}
    for (day, slot), required in demand_grid.items():
        workers = [x[(e, day, slot)] for e in employee_ids if (e, day, slot) in x]
        slack = model.new_int_var(0, required, f"slack_{day}_{slot}")
        model.add(sum(workers) + slack >= required)
        keyholders = [
            x[(e.id, day, slot)]
            for e in employees
            if e.is_keyholder and (e.id, day, slot) in x
        ]
        if keyholders and Constraints.KEYHOLDER.value not in skip:
            model.add(sum(keyholders) >= 1)
        slacks[(day, slot)] = slack

    model.minimize(sum(slacks.values()))
    if Constraints.MAX_WEEKLY_HOURS.value not in skip:
        for e in employees:
            weekly = [x[(e.id, day, slot)] for (eid, day, slot) in x if eid == e.id]
            max_weekly_slots = e.max_weekly_hours * 60 // slot_length_minutes
            model.add(sum(weekly) <= max_weekly_slots)

    if Constraints.SHIFT_LENGTH.value not in skip:
        for e in employees:
            for d in range(7):
                day = week_start + timedelta(days=d)
                slots = sorted(
                    [
                        slot
                        for (eid, eday, slot) in hashset
                        if e.id == eid and eday == day
                    ]
                )

                for i in range(len(slots) - 2):
                    s1 = x[(e.id, day, slots[i])]
                    s2 = x[(e.id, day, slots[i + 1])]
                    s3 = x[(e.id, day, slots[i + 2])]
                    if slots[i + 1] == slots[i] + slot_length_minutes and slots[
                        i + 2
                    ] == slots[i] + (slot_length_minutes * 2):
                        model.add(s1 + s3 - s2 <= 1)

                if slots:
                    works_today = model.new_bool_var(f"works_{e.id}_{day}")
                    total_slots = sum(x[(e.id, day, s)] for s in slots)
                    min_shift_slots = e.min_shift_hours * 60 // slot_length_minutes
                    max_shift_slots = e.max_shift_hours * 60 // slot_length_minutes
                    model.add(total_slots >= min_shift_slots * works_today)
                    model.add(total_slots <= max_shift_slots * works_today)

    solver = cp_model.CpSolver()
    status = solver.solve(model)

    if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        return None, None

    shortfalls = {}
    for (day, slot), slack_var in slacks.items():
        val = solver.value(slack_var)
        if val > 0:
            shortfalls[(day, slot)] = val

    shifts = []
    for (emp_id, day, slot), var in x.items():
        if solver.value(var) == 1:
            shifts.append({"employee_id": emp_id, "day": day, "slot": slot})

    shifts.sort(key=lambda s: (s["employee_id"], s["day"], s["slot"]))

    return shortfalls, shifts


def make_schedule(session: Session, tenant_id: str, week_start: date) -> dict:
    employees = session.scalars(
        select(Employee)
        .where(Employee.tenant_id == tenant_id)
        .where(Employee.is_active)
    ).all()

    if not employees:
        return {"status": ScheduleStatus.FAILED.value, "shortfalls": None}

    employee_ids = [e.id for e in employees]

    availability = session.scalars(
        select(AvailabilityRule).where(AvailabilityRule.employee_id.in_(employee_ids))
    ).all()

    avail_exceptions = session.scalars(
        select(AvailabilityException)
        .where(AvailabilityException.employee_id.in_(employee_ids))
        .where(AvailabilityException.exception_date >= week_start)
        .where(AvailabilityException.exception_date < week_start + timedelta(days=7))
    ).all()

    if not availability:
        return {"status": ScheduleStatus.FAILED.value, "shortfalls": None}

    exceptions_map = {
        (ae.employee_id, ae.exception_date): ae for ae in avail_exceptions
    }
    avail_map = {(a.employee_id, a.day_of_week): a for a in availability}

    hashset = set()
    config = resolve_config(tenant_id, session)
    open_minutes = config.schedule.opening_hour * 60 + config.schedule.opening_min
    close_minutes = config.schedule.closing_hour * 60 + config.schedule.closing_min
    slot_length_mins = config.schedule.slot_length_minutes

    for d in range(7):
        day = week_start + timedelta(days=d)
        for e in employees:
            exception = exceptions_map.get((e.id, day))
            rule = avail_map.get((e.id, day.weekday()))

            slots = set()

            if rule:
                slots = set(
                    range(
                        max(_time_to_minutes(rule.start_time), open_minutes),
                        min(_time_to_minutes(rule.end_time), close_minutes),
                        slot_length_mins,
                    )
                )

            if exception:
                ex_slots = set()
                if exception.start_time and exception.end_time:
                    ex_slots = set(
                        range(
                            max(_time_to_minutes(exception.start_time), open_minutes),
                            min(_time_to_minutes(exception.end_time), close_minutes),
                            slot_length_mins,
                        )
                    )

                if exception.is_available:
                    slots |= ex_slots
                else:
                    if ex_slots:
                        slots -= ex_slots
                    else:
                        slots = set()

            for s in slots:
                hashset.add((e.id, day, s))

    demand_grid = required_per_slot(session, tenant_id, week_start)
    if not demand_grid:
        return {"status": ScheduleStatus.FAILED.value, "shortfalls": None}

    shortfalls, shifts = _solver(
        hashset, demand_grid, employee_ids, employees, week_start, slot_length_mins
    )

    if not shifts:
        return {"status": ScheduleStatus.FAILED.value, "shortfalls": None}

    schedule = Schedule(
        tenant_id=tenant_id,
        week_start=week_start,
        status=ScheduleStatus.PROPOSED.value,
    )
    session.add(schedule)
    session.flush()

    for (emp_id, day), group in groupby(
        shifts, key=lambda s: (s["employee_id"], s["day"])
    ):
        slot_mins = [s["slot"] for s in group]
        start = min(slot_mins)
        end = max(slot_mins) + slot_length_mins

        session.add(
            Shift(
                schedule_id=schedule.id,
                employee_id=emp_id,
                shift_date=day,
                start_time=_minutes_to_time(start),
                end_time=_minutes_to_time(end),
            )
        )

    session.commit()

    diagnostics = diagnose_shortfalls(
        shortfalls,
        hashset,
        demand_grid,
        employee_ids,
        employees,
        week_start,
        slot_length_mins,
    )
    return {
        "status": ScheduleStatus.PROPOSED.value,
        "shortfalls": shortfalls,
        "diagnostics": diagnostics,
    }


def diagnose_shortfalls(
    shortfalls: dict,
    hashset: set,
    demand_grid: dict,
    employee_ids: list[uuid.UUID],
    employees: list[Employee],
    week_start: date,
    slot_length_mins: int,
) -> dict:

    if not shortfalls:
        return {}

    results = {}

    CONSTRAINT_MESSAGES = {
        Constraints.MAX_WEEKLY_HOURS.value: "Increasing an employee's max weekly hours would fix",
        Constraints.SHIFT_LENGTH.value: "Adjusting min/max shift length would fix",
        Constraints.KEYHOLDER.value: "Adding a keyholder to availability would fix",
    }

    for constraint in [
        Constraints.MAX_WEEKLY_HOURS.value,
        Constraints.SHIFT_LENGTH.value,
        Constraints.KEYHOLDER.value,
    ]:
        new_shortfalls, _ = _solver(
            hashset,
            demand_grid,
            employee_ids,
            employees,
            week_start,
            slot_length_mins,
            skip_constraints={constraint},
        )
        fixed = set(shortfalls.keys()) - set((new_shortfalls or {}).keys())
        if fixed:
            slots = [
                f"{day.strftime('%A')} {_minutes_to_time(slot).strftime('%I:%M %p')}"
                for day, slot in sorted(fixed)
            ]
            results[constraint] = {
                "message": CONSTRAINT_MESSAGES[constraint],
                "slots": slots,
            }
    return results
