import typer
from rich.console import Console
from rich.table import Table
from sqlalchemy import select

from src.cli.context import get_tenant
from src.db.models import BacktestResult, ModelRegistry

app = typer.Typer()


@app.command()
def list():
    session, tenant = get_tenant()
    console = Console()

    registry = session.scalar(
        select(ModelRegistry).where(ModelRegistry.tenant_id == tenant.id)
    )

    if not registry:
        console.print("[yellow]No model registry entry yet. Default champion: Poisson GLM Model[/yellow]")
    else:
        console.print(f"[bold]Active Champion:[/bold] {registry.active_version}")
        console.print(f"[bold]Previous Version:[/bold] {registry.previous_version or 'None'}")
        console.print(f"[bold]Promoted At:[/bold] {registry.promoted_at.strftime('%Y-%m-%d %H:%M')}")
        if registry.backtest_evidence:
            ev = registry.backtest_evidence
            console.print(
                f"[bold]Evidence:[/bold] skill={ev.get('skills'):.4f}, "
                f"coverage={ev.get('coverage_pct'):.1f}%, "
                f"wape={ev.get('wape'):.4f}"
            )

    results = session.scalars(
        select(BacktestResult)
        .where(BacktestResult.tenant_id == tenant.id)
        .order_by(BacktestResult.ran_at.desc())
        .limit(10)
    ).all()

    if not results:
        console.print("\n[yellow]No backtest results yet.[/yellow]")
        return

    table = Table(title="Recent Backtests")
    table.add_column("Champion")
    table.add_column("Challenger")
    table.add_column("Skill", justify="right")
    table.add_column("WAPE", justify="right")
    table.add_column("Coverage %", justify="right")
    table.add_column("Passed")
    table.add_column("Ran At")

    for r in results:
        table.add_row(
            r.champion_version,
            r.challenger_version,
            f"{float(r.skill):.4f}" if r.skill is not None else "—",
            f"{float(r.wape):.4f}" if r.wape is not None else "—",
            f"{float(r.coverage_pct):.1f}" if r.coverage_pct is not None else "—",
            "[green]✓[/green]" if r.passed else "[red]✗[/red]",
            r.ran_at.strftime("%Y-%m-%d %H:%M"),
        )

    console.print(table)


@app.command(name="set")
def set_version(version: str):
    session, tenant = get_tenant()
    console = Console()

    registry = session.scalar(
        select(ModelRegistry).where(ModelRegistry.tenant_id == tenant.id)
    )

    if not registry:
        console.print("[red]No model registry entry exists. Nothing to set.[/red]")
        return

    registry.previous_version = registry.active_version
    registry.active_version = version
    registry.backtest_evidence = None
    session.commit()

    console.print(
        f"[green]✓ Set model: {registry.previous_version} → {version}[/green]"
    )