from rich.prompt import Prompt
from sqlalchemy import select
import typer
from rich.console import Console
from src.db.models.core import Tenant
from src.db.session import SessionLocal
from src.cli.context import save_context, clear_context

app = typer.Typer()
console = Console()


@app.command()
def login(
    tenant_name: str = typer.Option(..., prompt="Tenant name"),
    tenant_address: str = typer.Option(..., prompt="Tenant address"),
    tenant_city: str = typer.Option(..., prompt="Tenant city"),
):
    with SessionLocal() as session:
        matches = session.scalars(
            select(Tenant).where(
                Tenant.name.ilike(f"%{tenant_name}%"),
                Tenant.address.ilike(f"%{tenant_address}%"),
                Tenant.location.ilike(f"%{tenant_city}%"),
            )
        ).all()

        if not matches:
            console.print(
                f"[red]No tenant matching '{tenant_name}' at '{tenant_address}, {tenant_city}'[/red]"
            )
            return

        if len(matches) == 1:
            match = matches[0]
        else:
            console.print(f"\n[yellow]Found {len(matches)} matches:[/yellow]")
            for i, t in enumerate(matches, 1):
                console.print(f"  {i}. {t.name} ({t.address}, {t.location})")
            choice = Prompt.ask("Pick a number", default="1")
            match = matches[int(choice) - 1]

        save_context(str(match.id))
        console.print(f"[green]✓ Logged in as {match.name} ({match.address}, {match.location})[/green]")

@app.command()
def logout():
    clear_context()
    console.print("[green]✓ Logged out[/green]")
