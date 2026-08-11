from datetime import date, time, timedelta

import typer
from rich.console import Console
from rich.prompt import Prompt
from rich.table import Table
from sqlalchemy import select
from sqlalchemy.orm import Session

from src.cli.context import get_tenant
from src.db.models import Employee, Schedule, ScheduleEdit, Shift
from src.schemas.schedule import ScheduleEditType, ScheduleStatus
from src.services.config_services import resolve_config
from src.services.scheduling_service import _minutes_to_time, make_schedule

app = typer.Typer()
edit_app = typer.Typer()

WEEKDAY_MAP = [
    "Monday",
    "Tuesday",
    "Wednesday",
    "Thursday",
    "Friday",
    "Saturday",
    "Sunday",
]


def _print_schedule(
    session: Session,
    console: Console,
    schedule: Schedule,
    tenant_id: str,
    result: dict | None = None,
):
    shifts = session.scalars(
        select(Shift)
        .where(Shift.schedule_id == schedule.id)
        .order_by(Shift.shift_date, Shift.start_time)
    ).all()

    emp_ids = {s.employee_id for s in shifts}
    employees = session.scalars(select(Employee).where(Employee.id.in_(emp_ids))).all()
    emp_map = {e.id: e.name for e in employees}

    table = Table(
        title=f"Schedule -- {schedule.week_start} to {schedule.week_start + timedelta(days=6)}"
    )
    table.add_column("Day")
    table.add_column("Employee")
    table.add_column("Start")
    table.add_column("End")

    for shift in shifts:
        table.add_row(
            shift.shift_date.strftime("%A %m/%d"),
            emp_map[shift.employee_id],
            shift.start_time.strftime("%I:%M %p"),
            shift.end_time.strftime("%I:%M %p"),
        )

    console.print(table)
    console.print(f"Status: [green]{schedule.status}[/green]")

    if result and result.get("shortfalls"):
        config = resolve_config(tenant_id, session)
        slot_length_mins = config.schedule.slot_length_minutes
        console.print("\n[yellow]Shortfalls:[/yellow]")
        sorted_gaps = sorted(result["shortfalls"].items())
        ranges = []
        for (day, slot), count in sorted_gaps:
            if (
                ranges
                and ranges[-1]["day"] == day
                and ranges[-1]["count"] == count
                and slot == ranges[-1]["end"] + slot_length_mins
            ):
                ranges[-1]["end"] = slot
            else:
                ranges.append({"day": day, "start": slot, "end": slot, "count": count})

        for r in ranges:
            console.print(
                f"  {r['day'].strftime('%A')} "
                f"{_minutes_to_time(r['start']).strftime('%I:%M %p')}"
                f" - {_minutes_to_time(r['end'] + slot_length_mins).strftime('%I:%M %p')}"
                f" -- short {r['count']}"
            )

    if result and result.get("diagnostics"):
        console.print("\n[yellow]Diagnostics:[/yellow]")
        for info in result["diagnostics"].values():
            console.print(f"  {info['message']}:")
            for slot in info["slots"]:
                console.print(f"    • {slot}")


@app.command()
def propose():
    session, tenant = get_tenant()
    console = Console()

    date_str = Prompt.ask("Date (Monday)")
    week_start = date.fromisoformat(date_str)

    while week_start.weekday() != 0:
        console.print("[yellow]Date must be for a Monday.[/yellow]")
        date_str = Prompt.ask("Date (Monday)")
        week_start = date.fromisoformat(date_str)

    result = make_schedule(session, str(tenant.id), week_start)

    if result["status"] == ScheduleStatus.FAILED.value:
        console.print(
            "[red]Unable to generate schedule. Try removing some constraints.[/red]"
        )
        return

    schedule = session.scalar(
        select(Schedule)
        .where(Schedule.tenant_id == tenant.id)
        .where(Schedule.week_start == week_start)
    )

    _print_schedule(session, console, schedule, str(tenant.id), result)


@app.command()
def show():
    session, tenant = get_tenant()
    console = Console()

    date_str = Prompt.ask("Date (Monday)")
    week_start = date.fromisoformat(date_str)

    while week_start.weekday() != 0:
        console.print("[yellow]Date must be for a Monday.[/yellow]")
        date_str = Prompt.ask("Date (Monday)")
        week_start = date.fromisoformat(date_str)

    schedule = session.scalar(
        select(Schedule)
        .where(Schedule.tenant_id == tenant.id)
        .where(Schedule.week_start == week_start)
    )

    if not schedule:
        console.print("[red]No schedule found for that week.[/red]")
        return

    _print_schedule(session, console, schedule, str(tenant.id))


@edit_app.command()
def add():
    session, tenant = get_tenant()
    console = Console()

    date_str = Prompt.ask("Date (Monday)")
    week_start = date.fromisoformat(date_str)

    while week_start.weekday() != 0:
        console.print("[yellow]Date must be for a Monday.[/yellow]")
        date_str = Prompt.ask("Date (Monday)")
        week_start = date.fromisoformat(date_str)

    schedule = session.scalar(
        select(Schedule)
        .where(Schedule.tenant_id == tenant.id)
        .where(Schedule.week_start == week_start)
    )

    if not schedule:
        console.print("[red]No schedule found for that week.[/red]")
        return

    employees = session.scalars(
        select(Employee)
        .where(Employee.tenant_id == tenant.id)
        .where(Employee.is_active)
    ).all()

    for i, emp in enumerate(employees, 1):
        console.print(f"  [{i}] {emp.name}")

    choice = int(Prompt.ask("Employee")) - 1
    emp = employees[choice]

    shift_date = date.fromisoformat(Prompt.ask("Shift date (YYYY-MM-DD)"))
    start = time.fromisoformat(Prompt.ask("Start time (HH:MM) [24-hr format]"))
    end = time.fromisoformat(Prompt.ask("End time (HH:MM) [24-hr format]"))

    shift = Shift(
        schedule_id=schedule.id,
        employee_id=emp.id,
        shift_date=shift_date,
        start_time=start,
        end_time=end,
    )
    session.add(shift)

    session.add(
        ScheduleEdit(
            schedule_id=schedule.id,
            edit_type=ScheduleEditType.SHIFT_ADD.value,
            details={
                "employee_id": str(emp.id),
                "employee_name": emp.name,
                "shift_date": shift_date.isoformat(),
                "start_time": start.isoformat(),
                "end_time": end.isoformat(),
            },
        )
    )

    session.commit()
    console.print(
        f"[green]Added {emp.name} on {shift_date.strftime('%A %m/%d')} "
        f"{start.strftime('%I:%M %p')} - {end.strftime('%I:%M %p')}[/green]"
    )


def _select_shift(session: Session, tenant_id: str, console: Console):
    date_str = Prompt.ask("Date (Monday)")
    week_start = date.fromisoformat(date_str)

    while week_start.weekday() != 0:
        console.print("[yellow]Date must be for a Monday.[/yellow]")
        date_str = Prompt.ask("Date (Monday)")
        week_start = date.fromisoformat(date_str)

    schedule = session.scalar(
        select(Schedule)
        .where(Schedule.tenant_id == tenant_id)
        .where(Schedule.week_start == week_start)
    )

    if not schedule:
        console.print("[red]No schedule found for that week.[/red]")
        return

    shifts = session.scalars(
        select(Shift).where(Shift.schedule_id == schedule.id)
    ).all()

    if not shifts:
        console.print("[red]No shifts found for that week.[/red]")
        return

    emp_ids = {s.employee_id for s in shifts}
    employees = session.scalars(select(Employee).where(Employee.id.in_(emp_ids))).all()
    emp_map = {e.id: e.name for e in employees}

    for i, shift in enumerate(shifts, 1):
        name = emp_map.get(shift.employee_id)
        weekday = WEEKDAY_MAP[shift.shift_date.weekday()]
        start = shift.start_time
        end = shift.end_time
        if name:
            console.print(
                f"  [{i}] {name} {weekday} {shift.shift_date} {start} - {end}"
            )

    shift_input = Prompt.ask("Enter shift number [enter to skip]")
    if not shift_input:
        console.print("[green]No shifts selected.[/green]")
        return

    shift_idx = int(shift_input)
    if shift_idx > len(shifts) or shift_idx < 1:
        console.print("[red]Invalid shift number entered.[/red]")
        return
    shift = shifts[shift_idx - 1]
    emp_name = emp_map[shift.employee_id]
    return shift, emp_name, schedule


@edit_app.command()
def remove():
    session, tenant = get_tenant()
    console = Console()

    result = _select_shift(session, str(tenant.id), console)
    if not result:
        return
    shift, emp_name, schedule = result

    session.delete(shift)
    session.add(
        ScheduleEdit(
            schedule_id=schedule.id,
            edit_type=ScheduleEditType.SHIFT_REMOVE.value,
            details={
                "employee_id": str(shift.employee_id),
                "employee_name": emp_name,
                "shift_date": shift.shift_date.isoformat(),
                "start_time": shift.start_time.isoformat(),
                "end_time": shift.end_time.isoformat(),
            },
        )
    )

    session.commit()
    console.print(
        f"[green]Removed {emp_name} {shift.shift_date.strftime('%A %m/%d')} "
        f"{shift.start_time.strftime('%I:%M %p')} - {shift.end_time.strftime('%I:%M %p')}[/green]"
    )


@edit_app.command()
def modify():
    session, tenant = get_tenant()
    console = Console()

    result = _select_shift(session, str(tenant.id), console)
    if not result:
        return
    shift, emp_name, schedule = result

    new_start = time.fromisoformat(Prompt.ask("Start time (HH:MM) [24-hr format]"))
    new_end = time.fromisoformat(Prompt.ask("End time (HH:MM) [24-hr format]"))

    old_start = shift.start_time
    old_end = shift.end_time

    shift.start_time = new_start
    shift.end_time = new_end

    session.add(
        ScheduleEdit(
            schedule_id=schedule.id,
            edit_type=ScheduleEditType.SHIFT_MODIFY.value,
            details={
                "employee_id": str(shift.employee_id),
                "employee_name": emp_name,
                "shift_date": shift.shift_date.isoformat(),
                "prev_start_time": old_start.isoformat(),
                "prev_end_time": old_end.isoformat(),
                "new_start_time": shift.start_time.isoformat(),
                "new_end_time": shift.end_time.isoformat(),
            },
        )
    )

    session.commit()
    console.print(
        f"[green]Edited {emp_name} {shift.shift_date.strftime('%A %m/%d')} "
        f"{shift.start_time.strftime('%I:%M %p')} - {shift.end_time.strftime('%I:%M %p')}[/green]"
    )


@app.command()
def approve():
    session, tenant = get_tenant()
    console = Console()

    date_str = Prompt.ask("Date (Monday)")
    week_start = date.fromisoformat(date_str)

    while week_start.weekday() != 0:
        console.print("[yellow]Date must be for a Monday.[/yellow]")
        date_str = Prompt.ask("Date (Monday)")
        week_start = date.fromisoformat(date_str)

    schedule = session.scalar(
        select(Schedule)
        .where(Schedule.tenant_id == tenant.id)
        .where(Schedule.week_start == week_start)
    )

    if not schedule:
        console.print("[red]No schedule found for that week.[/red]")
        return

    if schedule.status != ScheduleStatus.PROPOSED.value:
        console.print(f"[red]Schedule is {schedule.status}, not proposed.[/red]")
        return

    schedule.status = ScheduleStatus.APPROVED.value
    session.commit()
    console.print("[green]Schedule approved.[/green]")


@app.command()
def publish():
    session, tenant = get_tenant()
    console = Console()

    date_str = Prompt.ask("Date (Monday)")
    week_start = date.fromisoformat(date_str)

    while week_start.weekday() != 0:
        console.print("[yellow]Date must be for a Monday.[/yellow]")
        date_str = Prompt.ask("Date (Monday)")
        week_start = date.fromisoformat(date_str)

    schedule = session.scalar(
        select(Schedule)
        .where(Schedule.tenant_id == tenant.id)
        .where(Schedule.week_start == week_start)
    )

    if not schedule:
        console.print("[red]No schedule found for that week.[/red]")
        return

    if schedule.status == ScheduleStatus.PUBLISHED.value:
        console.print("[yellow]Schedule is already published.[/yellow]")
        return

    schedule.status = ScheduleStatus.PUBLISHED.value
    session.commit()
    console.print("[green]Schedule published.[/green]")
