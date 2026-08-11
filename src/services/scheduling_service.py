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


def required_per_hour(
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
    open_hour = config.schedule.opening_hour
    close_hour = config.schedule.closing_hour
    min_staffing = config.schedule.min_staffing
    service_rate = config.schedule.service_rate
    required = {}
    for d in range(7):
        day = week_start + timedelta(days=d)
        forecast = forecast_map.get(day, 0)
        weekday = day.weekday()
        for h in range(open_hour, close_hour):
            fraction = demand_map.get((weekday, h), 0)
            required[(day, h)] = max(
                ceil(forecast * fraction / service_rate), min_staffing
            )

    return required


def _solver(
    hashset: set,
    demand_grid: dict,
    employee_ids: list[uuid.UUID],
    employees: list[Employee],
    week_start: date,
    skip_constraints: set[str] | None = None,
) -> tuple:
    model = cp_model.CpModel()
    skip = skip_constraints or set()

    x = {}
    for emp_id, day, hour in hashset:
        x[(emp_id, day, hour)] = model.new_bool_var(f"x_{emp_id}_{day}_{hour}")

    slacks = {}
    for (day, hour), required in demand_grid.items():
        workers = [x[(e, day, hour)] for e in employee_ids if (e, day, hour) in x]
        slack = model.new_int_var(0, required, f"slack_{day}_{hour}")
        model.add(sum(workers) + slack >= required)
        keyholders = [
            x[(e.id, day, hour)]
            for e in employees
            if e.is_keyholder and (e.id, day, hour) in x
        ]
        if keyholders and Constraints.KEYHOLDER.value not in skip:
            model.add(sum(keyholders) >= 1)
        slacks[(day, hour)] = slack

    model.minimize(sum(slacks.values()))
    if Constraints.MAX_WEEKLY_HOURS.value not in skip:
        for e in employees:
            weekly = [x[(e.id, day, hour)] for (eid, day, hour) in x if eid == e.id]
            model.add(sum(weekly) <= e.max_weekly_hours)

    if Constraints.SHIFT_LENGTH.value not in skip:
        for e in employees:
            for d in range(7):
                day = week_start + timedelta(days=d)
                hours = sorted(
                    [h for (eid, eday, h) in hashset if e.id == eid and eday == day]
                )

                for i in range(len(hours) - 2):
                    h1 = x[(e.id, day, hours[i])]
                    h2 = x[(e.id, day, hours[i + 1])]
                    h3 = x[(e.id, day, hours[i + 2])]
                    if hours[i + 1] == hours[i] + 1 and hours[i + 2] == hours[i] + 2:
                        model.add(h1 + h3 - h2 <= 1)

                if hours:
                    works_today = model.new_bool_var(f"works_{e.id}_{day}")
                    total_hours = sum(x[(e.id, day, h)] for h in hours)
                    model.add(total_hours >= e.min_shift_hours * works_today)
                    model.add(total_hours <= e.max_shift_hours * works_today)

    solver = cp_model.CpSolver()
    status = solver.solve(model)

    if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        return None, None

    shortfalls = {}
    for (day, hour), slack_var in slacks.items():
        val = solver.value(slack_var)
        if val > 0:
            shortfalls[(day, hour)] = val

    shifts = []
    for (emp_id, day, hour), var in x.items():
        if solver.value(var) == 1:
            shifts.append({"employee_id": emp_id, "day": day, "hour": hour})

    shifts.sort(key=lambda s: (s["employee_id"], s["day"], s["hour"]))

    return shortfalls, shifts


def solve_schedule(session: Session, tenant_id: str, week_start: date) -> dict:
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
    open_hour = config.schedule.opening_hour
    close_hour = config.schedule.closing_hour

    for d in range(7):
        day = week_start + timedelta(days=d)
        for e in employees:
            exception = exceptions_map.get((e.id, day))
            rule = avail_map.get((e.id, day.weekday()))

            hours = set()

            if rule:
                hours = set(
                    range(
                        max(rule.start_time.hour, open_hour),
                        min(rule.end_time.hour, close_hour),
                    )
                )

            if exception:
                ex_hours = set()
                if exception.start_time and exception.end_time:
                    ex_hours = set(
                        range(
                            max(exception.start_time.hour, open_hour),
                            min(exception.end_time.hour, close_hour),
                        )
                    )

                if exception.is_available:
                    hours |= ex_hours
                else:
                    if ex_hours:
                        hours -= ex_hours
                    else:
                        hours = set()

            for h in hours:
                hashset.add((e.id, day, h))

    demand_grid = required_per_hour(session, tenant_id, week_start)
    if not demand_grid:
        return {"status": ScheduleStatus.FAILED.value, "shortfalls": None}

    shortfalls, shifts = _solver(
        hashset, demand_grid, employee_ids, employees, week_start
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
        hours = [s["hour"] for s in group]
        start = min(hours)
        end = max(hours) + 1

        session.add(
            Shift(
                schedule_id=schedule.id,
                employee_id=emp_id,
                shift_date=day,
                start_time=time(start),
                end_time=time(end),
            )
        )

    session.commit()
    diagnostics = diagnose_shortfalls(
        shortfalls, hashset, demand_grid, employee_ids, employees, week_start
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
            skip_constraints={constraint},
        )
        fixed = set(shortfalls.keys()) - set((new_shortfalls or {}).keys())
        if fixed:
            slots = [f"{day.strftime('%A')} {hour}:00" for day, hour in sorted(fixed)]
            results[constraint] = {
                "message": CONSTRAINT_MESSAGES[constraint],
                "slots": slots,
            }
    return results
