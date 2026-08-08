import typer
from rich.console import Console
from rich.table import Table
from sqlalchemy import select

from src.cli.context import get_tenant
from src.db.models import Anomaly, AnomalyFeedback
from src.schemas.anomaly import AnomalyAction

app = typer.Typer()


@app.command()
def list():
    session, tenant = get_tenant()
    console = Console()

    rows = session.scalars(
        select(Anomaly)
        .where(Anomaly.tenant_id == tenant.id)
        .where(Anomaly.suppressed.is_(False))
        .order_by(Anomaly.created_at.desc())
        .limit(20)
    ).all()

    if not rows:
        console.print("[green]No active anomalies.[/green]")
        return

    table = Table(title=f"Anomalies -- {tenant.name}")
    table.add_column("ID", max_width=8)
    table.add_column("Date")
    table.add_column("Type")
    table.add_column("Severity")
    table.add_column("Evidence")

    for a in rows:
        table.add_row(
            str(a.id)[:8],
            str(a.business_date),
            f"{a.anomaly_type}:{a.subject}",
            str(a.severity),
            a.evidence_sentence,
        )

    console.print(table)


@app.command()
def ack(anomaly_id: str):
    session, tenant = get_tenant()
    console = Console()

    anomaly = session.scalar(
        select(Anomaly)
        .where(Anomaly.id == anomaly_id)
        .where(Anomaly.tenant_id == tenant.id)
    )

    if not anomaly:
        console.print("[red]Anomaly not found.[/red]")
        return

    existing = session.scalar(
        select(AnomalyFeedback)
        .where(AnomalyFeedback.anomaly_id == anomaly.id)
        .where(AnomalyFeedback.tenant_id == tenant.id)
    )

    if existing:
        console.print(f"[yellow]Already responded with '{existing.action}'.[/yellow]")
        return

    session.add(
        AnomalyFeedback(
            tenant_id=tenant.id,
            anomaly_id=anomaly.id,
            action=AnomalyAction.ACK,
        )
    )
    session.commit()
    console.print("[green]✓ Anomaly acknowledged.[/green]")


@app.command()
def dismiss(anomaly_id: str):
    session, tenant = get_tenant()
    console = Console()

    anomaly = session.scalar(
        select(Anomaly)
        .where(Anomaly.id == anomaly_id)
        .where(Anomaly.tenant_id == tenant.id)
    )

    if not anomaly:
        console.print("[red]Anomaly not found.[/red]")
        return

    existing = session.scalar(
        select(AnomalyFeedback)
        .where(AnomalyFeedback.anomaly_id == anomaly.id)
        .where(AnomalyFeedback.tenant_id == tenant.id)
    )

    if existing:
        console.print(f"[yellow]Already responded with '{existing.action}'.[/yellow]")
        return

    session.add(
        AnomalyFeedback(
            tenant_id=tenant.id,
            anomaly_id=anomaly.id,
            action=AnomalyAction.DISMISS,
        )
    )
    session.commit()
    console.print("[green]✓ Anomaly dismissed.[/green]")