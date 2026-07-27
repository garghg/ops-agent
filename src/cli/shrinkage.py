import typer
from rich.console import Console
from rich.table import Table
from sqlalchemy import select

from src.db.models.core import Tenant
from src.db.models.shrinkage import ShrinkageRate
from src.db.session import SessionLocal

app = typer.Typer()

@app.command()
def report(tenant_name: str):

    console = Console()

    with SessionLocal() as session:
        tenant = session.scalar(
            select(Tenant).where(Tenant.name.ilike(f"%{tenant_name}%"))
        )
        if not tenant:
            console.print(f"[red]No tenant matching '{tenant_name}'[/red]")
            return

        rates = session.execute(
            select(ShrinkageRate)
            .where(ShrinkageRate.tenant_id == tenant.id)
            .order_by(ShrinkageRate.category)
        ).scalars().all()

        if not rates:
            console.print("[yellow]No shrinkage data yet. Run at least two counts.[/yellow]")
            return

        table = Table(title=f"Shrinkage Rates -- {tenant.name}")
        table.add_column("Category")
        table.add_column("Rate", justify="right")
        table.add_column("Samples", justify="right")
        table.add_column("Last Updated")

        for r in rates:
            table.add_row(
                r.category,
                f"{r.rate * 100:.2f}%",
                str(r.sample_count),
                r.last_updated.strftime("%Y-%m-%d %H:%M"),
            )

        console.print(table)