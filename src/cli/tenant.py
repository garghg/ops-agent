import typer
from rich.console import Console
from rich.prompt import Prompt
from sqlalchemy import select

from src.cli.context import get_tenant, save_context
from src.db.models import Template, TenantConfig
from src.db.session import SessionLocal
from src.schemas.template import TemplateConfig
from src.schemas.tenant import SHOP_TYPE_SLUGS, ShopType
from src.services.tenant_service import create_tenant

app = typer.Typer()
console = Console()


def _resolve_template(session, shop_type: ShopType):
    slug = SHOP_TYPE_SLUGS[shop_type]
    template = session.scalar(select(Template).where(Template.slug == slug))

    if not template:
        template = Template(
            slug=slug,
            version=1,
            body=TemplateConfig().model_dump(),
        )
        session.add(template)
        session.flush()

    return template


@app.command()
def create():
    shop_types = [t for t in ShopType]

    console.print("\n[bold]Available shop types:[/bold]")
    for i, t in enumerate(shop_types, 1):
        console.print(f"  [{i}] {t.value.replace('_', ' ').title()}")

    while True:
        try:
            idx = int(Prompt.ask("\nShop type"))
            if 1 <= idx <= len(shop_types):
                break
        except ValueError:
            pass
        console.print("[red]Invalid selection.[/red]")

    shop_type = shop_types[idx - 1]

    name = Prompt.ask("Shop name")
    location = Prompt.ask("City")
    address = Prompt.ask("Street address")

    owner_email = Prompt.ask("Owner email (for daily summaries and actions)")
    while not owner_email:
        console.print("[red]Email is required.[/red]")
        owner_email = Prompt.ask("Owner email (for daily summaries and actions)")

    with SessionLocal() as session:
        template = _resolve_template(session, shop_type)

        tenant = create_tenant(
            name=name,
            location=location,
            address=address,
            shop_type=shop_type,
            session=session,
            template_id=template.id,
            owner_email=owner_email,
        )
        session.flush()

        session.add(TenantConfig(tenant_id=tenant.id))
        session.commit()

        save_context(str(tenant.id))
        console.print(f"\n[green]✓ Created '{tenant.name}'[/green]")
        console.print(f"  {tenant.address}, {tenant.location}")
        console.print(f"  Timezone: {tenant.timezone}")
        console.print(f"  Coordinates: {tenant.latitude}, {tenant.longitude}")
        console.print(f"\n[green]Logged in as {tenant.name}.[/green]")


@app.command()
def info():
    _, tenant = get_tenant()

    console.print(f"\n[bold]{tenant.name}[/bold]")
    console.print(f"  {tenant.address}, {tenant.location}")
    console.print(f"  Timezone: {tenant.timezone}")
    console.print(f"  Coordinates: {tenant.latitude}, {tenant.longitude}")
    console.print(f"  Type: {tenant.shop_type.replace('_', ' ').title()}")
    console.print(f"  Owner email: {tenant.owner_email}")
    console.print(f"  Created: {tenant.created_at.strftime('%Y-%m-%d %H:%M')}")