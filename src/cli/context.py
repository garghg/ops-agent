import json
from pathlib import Path
from sqlalchemy import select
from src.db.models.core import Tenant
from src.db.session import SessionLocal

CONTEXT_FILE = Path.home() / ".ops_context"


def save_context(tenant_id: str):
    CONTEXT_FILE.write_text(json.dumps({"tenant_id": tenant_id}))


def clear_context():
    if CONTEXT_FILE.exists():
        CONTEXT_FILE.unlink()


def get_tenant():
    import typer

    if not CONTEXT_FILE.exists():
        typer.echo("Not logged in. Run: ops login")
        raise typer.Exit(1)

    data = json.loads(CONTEXT_FILE.read_text())
    session = SessionLocal()
    tenant = session.scalar(
        select(Tenant).where(Tenant.id == data["tenant_id"])
    )
    if not tenant:
        typer.echo("Tenant not found. Run: ops login")
        clear_context()
        raise typer.Exit(1)

    return session, tenant