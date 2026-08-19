from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal

import typer
from rich.console import Console
from rich.prompt import Prompt
from rich.table import Table
from sqlalchemy import func, select

from src.cli.context import get_tenant
from src.db.models import DailyActual, ForecastMetric, Supplier
from src.schemas.models import ModelVersion
from src.services.forecast_service import backtest as bt
from src.services.ordering_service import rollup

app = typer.Typer()


@app.command()
def forecast():
    session, tenant = get_tenant()
    console = Console()
    start = Prompt.ask("Start Date (inclusive)") or None
    end = Prompt.ask("End Date (inclusive)") or None
    start_date = (
        date.fromisoformat(start) if start else date.today() - timedelta(days=28)  # noqa: DTZ011
    )
    end_date = date.fromisoformat(end) if end else date.today()  # noqa: DTZ011

    metrics = session.scalars(
        select(ForecastMetric)
        .where(ForecastMetric.tenant_id == tenant.id)
        .where(ForecastMetric.target_date >= start_date)
        .where(ForecastMetric.target_date <= end_date)
    ).all()

    if not metrics:
        console.print("[yellow]No forecast metrics found for this range.[/yellow]")
        return

    actuals = session.scalars(
        select(DailyActual)
        .where(DailyActual.tenant_id == tenant.id)
        .where(DailyActual.actual_date >= start_date)
        .where(DailyActual.actual_date <= end_date)
    ).all()

    if not actuals:
        console.print("[yellow]No records found for this range.[/yellow]")
        return

    actuals_by_key = {(a.series, a.actual_date): a for a in actuals}
    metrics_by_group = defaultdict(list)
    for metric in metrics:
        metrics_by_group[metric.model_version].append(metric)

    aggregate_metrics = {}
    for model, metric_lst in metrics_by_group.items():
        total_mae = Decimal(0)
        total_bias = Decimal(0)
        total_actual = Decimal(0)
        count = 0
        coverage_hits = 0
        coverage_total = 0

        for m in metric_lst:
            actual = actuals_by_key.get((m.series, m.target_date))
            if not actual:
                continue
            total_mae += m.mae
            total_bias += m.bias
            total_actual += actual.value
            count += 1
            if m.coverage is not None:
                coverage_total += 1
                if m.coverage:
                    coverage_hits += 1

        avg_mae = total_mae / count if count else Decimal(0)
        wape = total_mae / total_actual if total_actual else Decimal(0)
        avg_bias = total_bias / count if count else Decimal(0)
        coverage_rate = coverage_hits / coverage_total if coverage_total else None
        aggregate_metrics[model] = {
            "avg_mae": avg_mae,
            "wape": wape,
            "avg_bias": avg_bias,
            "coverage_rate": coverage_rate,
        }

    naive_wape = aggregate_metrics.get(
        ModelVersion.SEASONAL_NAIVE.value, {"wape": Decimal(0)}
    )["wape"]
    for k in aggregate_metrics:  # noqa: PLC0206
        model_wape = aggregate_metrics.get(k, {"wape": Decimal(0)})["wape"]
        if naive_wape > 0:
            skill = 1 - (model_wape / naive_wape)
        else:
            skill = None
        aggregate_metrics[k]["skill"] = skill

    table = Table(title=f"Forecast Metrics — {start_date} to {end_date}")
    table.add_column("Model")
    table.add_column("Avg MAE", justify="right")
    table.add_column("WAPE", justify="right")
    table.add_column("Avg Bias", justify="right")
    table.add_column("Coverage", justify="right")
    table.add_column("Skill", justify="right")

    for model, m in aggregate_metrics.items():
        bias_val = m["avg_bias"]
        bias_sign = "+" if bias_val >= 0 else ""

        table.add_row(
            model,
            f"{m['avg_mae']:.2f}",
            f"{m['wape'] * 100:.1f}%",
            f"{bias_sign}{bias_val:.2f}",
            f"{m['coverage_rate'] * 100:.1f}%"
            if m["coverage_rate"] is not None
            else "n/a",
            f"{m['skill'] * 100:.1f}%" if m["skill"] is not None else "n/a",
        )

    console.print()
    console.print(table)


@app.command()
def backtest():
    session, tenant = get_tenant()
    earliest = session.scalar(
        select(func.min(DailyActual.actual_date)).where(
            DailyActual.tenant_id == tenant.id
        )
    )
    latest = session.scalar(
        select(func.max(DailyActual.actual_date)).where(
            DailyActual.tenant_id == tenant.id
        )
    )
    bt(session, str(tenant.id), str(earliest), str(latest), [ModelVersion.POISSON_GLM.value])


@app.command()
def autonomy():
    session, tenant = get_tenant()
    console = Console()

    suppliers = session.scalars(
        select(Supplier)
        .where(Supplier.tenant_id == tenant.id)
        .where(Supplier.is_active == True)
    ).all()

    suppliers = [s for s in suppliers if not s.delivery_days]

    if not suppliers:
        console.print("[yellow]No on-demand suppliers found.[/yellow]")
        return

    table = Table(title=f"Autonomy Metrics — {tenant.name}")
    table.add_column("Supplier")
    table.add_column("Proposals", justify="right")
    table.add_column("Span (days)", justify="right")
    table.add_column("Approval Rate", justify="right")
    table.add_column("Edit Median", justify="right")
    table.add_column("Max Edit", justify="right")
    table.add_column("Reject Streak", justify="right")
    table.add_column("Critical Failures", justify="right")

    for supplier in suppliers:
        stats = rollup(session, str(tenant.id), str(supplier.id))
        if not stats:
            table.add_row(supplier.name, *["-"] * 7)
            continue

        table.add_row(
            supplier.name,
            str(stats["proposal_count"]),
            str(stats["span_days"]),
            f"{stats['approval_rate'] * 100:.0f}%"
            if stats["approval_rate"] is not None
            else "—",
            f"{stats['edit_median'] * 100:.1f}%"
            if stats["edit_median"] is not None
            else "—",
            f"{stats['max_edit'] * 100:.1f}%" if stats["max_edit"] is not None else "—",
            str(stats["consecutive_rejects"]),
            str(stats["critical_failures"]),
        )

    console.print()
    console.print(table)
