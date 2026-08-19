from datetime import date, time

import typer
from rich.console import Console
from rich.prompt import Prompt
from rich.table import Table
from sqlalchemy import select

from src.cli.context import get_tenant
from src.db.models import (
    AvailabilityException,
    AvailabilityRule,
    Certification,
    Employee,
)

app = typer.Typer()
console = Console()

WEEKDAYS = [
    "Monday",
    "Tuesday",
    "Wednesday",
    "Thursday",
    "Friday",
    "Saturday",
    "Sunday",
]


def _select_employee(session, tenant_id, active_only=True):
    query = (
        select(Employee).where(Employee.tenant_id == tenant_id).order_by(Employee.name)
    )
    if active_only:
        query = query.where(Employee.is_active == True)

    employees = session.scalars(query).all()

    if not employees:
        label = "active employees" if active_only else "employees"
        console.print(f"[yellow]No {label}.[/yellow]")
        return None

    for i, emp in enumerate(employees, 1):
        console.print(f"  [{i}] {emp.name}")

    while True:
        try:
            idx = int(Prompt.ask("\nSelect employee"))
            if 1 <= idx <= len(employees):
                return employees[idx - 1]
        except ValueError:
            pass
        console.print("[red]Invalid selection.[/red]")


@app.command()
def list():
    session, tenant = get_tenant()

    employees = session.scalars(
        select(Employee)
        .where(Employee.tenant_id == tenant.id)
        .order_by(Employee.is_active.desc(), Employee.name)
    ).all()

    if not employees:
        console.print("[yellow]No employees.[/yellow]")
        return

    emp_ids = [e.id for e in employees]

    certs = session.execute(
        select(Certification.employee_id, Certification.cert_type).where(
            Certification.employee_id.in_(emp_ids)
        )
    ).all()

    cert_map = {}
    for emp_id, cert_type in certs:
        cert_map.setdefault(emp_id, []).append(cert_type)

    rules = session.scalars(
        select(AvailabilityRule)
        .where(AvailabilityRule.employee_id.in_(emp_ids))
        .order_by(AvailabilityRule.day_of_week)
    ).all()

    rule_map = {}
    for rule in rules:
        rule_map.setdefault(rule.employee_id, []).append(rule)

    table = Table(title=f"Employees -- {tenant.name}")
    table.add_column("Name")
    table.add_column("Hours", justify="right")
    table.add_column("Shift", justify="right")
    table.add_column("Certs")
    table.add_column("Availability")
    table.add_column("Active")

    for emp in employees:
        emp_certs = cert_map.get(emp.id, [])
        emp_rules = rule_map.get(emp.id, [])

        avail_parts = []
        for rule in emp_rules:
            day = WEEKDAYS[rule.day_of_week][:3]
            start = rule.start_time.strftime("%H:%M")
            end = rule.end_time.strftime("%H:%M")
            avail_parts.append(f"{day} {start}-{end}")

        table.add_row(
            emp.name,
            f"≤{emp.max_weekly_hours}/wk",
            f"{emp.min_shift_hours}-{emp.max_shift_hours}h",
            ", ".join(emp_certs) if emp_certs else "--",
            ", ".join(avail_parts) if avail_parts else "--",
            "[green]✓[/green]" if emp.is_active else "[red]✗[/red]",
        )

    console.print(table)


@app.command()
def add():
    session, tenant = get_tenant()

    name = Prompt.ask("Employee name")

    while True:
        try:
            max_weekly = int(Prompt.ask("Max weekly hours"))
            if max_weekly > 0:
                break
            console.print("[red]Must be positive.[/red]")
        except ValueError:
            console.print("[red]Invalid number.[/red]")

    while True:
        try:
            min_shift = int(Prompt.ask("Min shift length (hours)"))
            if min_shift > 0:
                break
            console.print("[red]Must be positive.[/red]")
        except ValueError:
            console.print("[red]Invalid number.[/red]")

    while True:
        try:
            max_shift = int(Prompt.ask("Max shift length (hours)"))
            if max_shift >= min_shift:
                break
            console.print(f"[red]Must be ≥ {min_shift}.[/red]")
        except ValueError:
            console.print("[red]Invalid number.[/red]")

    is_keyholder = Prompt.ask("Keyholder?", choices=["y", "n"], default="n") == "y"

    emp = Employee(
        tenant_id=tenant.id,
        name=name,
        max_weekly_hours=max_weekly,
        min_shift_hours=min_shift,
        max_shift_hours=max_shift,
        is_keyholder=is_keyholder,
    )
    session.add(emp)
    session.flush()

    # Prompt for availability immediately
    console.print(f"\n[bold]Set weekly availability for {name}:[/bold]")
    console.print("  Enter start and end times for each day, or 's' to skip.\n")

    for day_idx, day_name in enumerate(WEEKDAYS):
        raw = Prompt.ask(f"  {day_name} (e.g. 09:00-17:00, 's' to skip)")
        if not raw or raw.lower() == "s":
            console.print(f"{day_name} skipped")
            continue

        try:
            start_str, end_str = raw.split("-")
            start = time.fromisoformat(start_str.strip())
            end = time.fromisoformat(end_str.strip())

            session.add(
                AvailabilityRule(
                    employee_id=emp.id,
                    day_of_week=day_idx,
                    start_time=start,
                    end_time=end,
                )
            )
        except (ValueError, TypeError):
            console.print(f"  [red]Invalid format, skipping {day_name}.[/red]")

    session.commit()
    console.print(f"[green]✓ Added employee '{name}'[/green]")


@app.command()
def deactivate():
    session, tenant = get_tenant()

    emp = _select_employee(session, tenant.id)
    if not emp:
        return

    confirm = Prompt.ask(f"  Deactivate {emp.name}?", choices=["y", "n"], default="n")
    if confirm == "y":
        emp.is_active = False
        session.commit()
        console.print(f"[green]✓ {emp.name} deactivated[/green]")
    else:
        console.print("[yellow]Cancelled.[/yellow]")


@app.command()
def availability():
    session, tenant = get_tenant()

    emp = _select_employee(session, tenant.id)
    if not emp:
        return

    existing = session.scalars(
        select(AvailabilityRule)
        .where(AvailabilityRule.employee_id == emp.id)
        .order_by(AvailabilityRule.day_of_week)
    ).all()

    if existing:
        console.print(f"\n  Current availability for {emp.name}:")
        for rule in existing:
            day = WEEKDAYS[rule.day_of_week]
            console.print(
                f"    {day}: {rule.start_time.strftime('%H:%M')}-{rule.end_time.strftime('%H:%M')}"
            )

    console.print(f"\n[bold]Update availability for {emp.name}:[/bold]")
    console.print(
        "  Enter start-end for each day, 's' to skip (keep current), 'x' to clear.\n"
    )

    for day_idx, day_name in enumerate(WEEKDAYS):
        current = next((r for r in existing if r.day_of_week == day_idx), None)
        hint = (
            f" [{current.start_time.strftime('%H:%M')}-{current.end_time.strftime('%H:%M')}]"
            if current
            else ""
        )

        raw = Prompt.ask(f"  {day_name}{hint} (e.g. 09:00-17:00, 's' skip, 'x' clear)")

        if raw.lower() == "s":
            continue

        if raw.lower() == "x":
            if current:
                session.delete(current)
            continue

        try:
            start_str, end_str = raw.split("-")
            start = time.fromisoformat(start_str.strip())
            end = time.fromisoformat(end_str.strip())

            if current:
                current.start_time = start
                current.end_time = end
            else:
                session.add(
                    AvailabilityRule(
                        employee_id=emp.id,
                        day_of_week=day_idx,
                        start_time=start,
                        end_time=end,
                    )
                )
        except (ValueError, TypeError):
            console.print(f"  [red]Invalid format, skipping {day_name}.[/red]")

    session.commit()
    console.print(f"[green]✓ Updated availability for {emp.name}[/green]")


@app.command()
def exception():
    session, tenant = get_tenant()

    emp = _select_employee(session, tenant.id)
    if not emp:
        return

    date_str = Prompt.ask("Date (YYYY-MM-DD)")
    try:
        exc_date = date.fromisoformat(date_str)
    except ValueError:
        console.print("[red]Invalid date format.[/red]")
        return

    existing = session.scalar(
        select(AvailabilityException)
        .where(AvailabilityException.employee_id == emp.id)
        .where(AvailabilityException.exception_date == exc_date)
    )
    if existing:
        console.print(
            f"[yellow]Exception already exists for {exc_date}: "
            f"{'available' if existing.is_available else 'unavailable'}[/yellow]"
        )
        overwrite = Prompt.ask("Overwrite?", choices=["y", "n"], default="n")
        if overwrite == "n":
            return
        session.delete(existing)
        session.flush()

    avail_choice = Prompt.ask(
        "Available or unavailable?", choices=["available", "unavailable"]
    )
    is_available = avail_choice == "available"

    start_time = None
    end_time = None
    if is_available:
        times = Prompt.ask("Time window (e.g. 09:00-17:00)")
        try:
            start_str, end_str = times.split("-")
            start_time = time.fromisoformat(start_str.strip())
            end_time = time.fromisoformat(end_str.strip())
        except (ValueError, TypeError):
            console.print("[red]Invalid format.[/red]")
            return

    session.add(
        AvailabilityException(
            employee_id=emp.id,
            exception_date=exc_date,
            is_available=is_available,
            start_time=start_time,
            end_time=end_time,
        )
    )
    session.commit()

    label = (
        f"available {start_time.strftime('%H:%M')}-{end_time.strftime('%H:%M')}"
        if is_available
        else "unavailable"
    )
    console.print(f"[green]✓ {emp.name} on {exc_date}: {label}[/green]")


@app.command()
def certify():
    session, tenant = get_tenant()

    emp = _select_employee(session, tenant.id)
    if not emp:
        return

    existing_certs = session.scalars(
        select(Certification).where(Certification.employee_id == emp.id)
    ).all()

    if existing_certs:
        console.print(f"\n  Current certifications for {emp.name}:")
        for c in existing_certs:
            console.print(f"    {c.cert_type}")

    cert_type = Prompt.ask("Certification type (e.g. food_safety)")

    already = session.scalar(
        select(Certification)
        .where(Certification.employee_id == emp.id)
        .where(Certification.cert_type == cert_type)
    )
    if already:
        console.print(f"[yellow]{emp.name} already has '{cert_type}'.[/yellow]")
        return

    session.add(
        Certification(
            employee_id=emp.id,
            cert_type=cert_type,
        )
    )

    session.commit()
    console.print(f"[green]✓ Added '{cert_type}' to {emp.name}[/green]")
