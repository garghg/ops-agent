import typer
from rich.console import Console
from rich.table import Table
from sqlalchemy import select

from src.cli.context import get_tenant
from src.db.models import Category, CorrectionFactor
from src.schemas.learning import FactorKind

app = typer.Typer()

@app.command()
def report():
    session, tenant = get_tenant()
    console = Console()

    factors = session.scalars(
        select(CorrectionFactor)
        .where(CorrectionFactor.tenant_id == tenant.id)
        .where(CorrectionFactor.kind == FactorKind.SHRINKAGE)
    ).all()

    if not factors:
        console.print("[yellow]No shrinkage data yet. Run at least two counts.[/yellow]")
        return

    category_ids = [f.scope_key for f in factors]
    categories = session.scalars(
        select(Category)
        .where(Category.id.in_(category_ids))
        .where(Category.tenant_id == tenant.id)
    ).all()
    cat_map = {str(c.id): c.name for c in categories}

    table = Table(title=f"Shrinkage Rates — {tenant.name}")
    table.add_column("Category")
    table.add_column("Rate", justify="right")
    table.add_column("Samples", justify="right")
    table.add_column("Last Updated")

    for f in factors:
        table.add_row(
            cat_map.get(f.scope_key, f.scope_key),
            f"{float(f.value) * 100:.2f}%",
            str(f.evidence_count),
            f.updated_at.strftime("%Y-%m-%d %H:%M"),
        )

    console.print(table)