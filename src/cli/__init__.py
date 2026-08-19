import typer

from src.cli.anomaly import app as anomaly_app
from src.cli.app_config import app as config_app
from src.cli.autonomy import app as autonomy_app
from src.cli.catalog import app as catalog_app
from src.cli.count import app as count_app
from src.cli.employee import app as employee_app
from src.cli.factors import app as factors_app
from src.cli.inventory import app as inventory_app
from src.cli.login import login, logout
from src.cli.metrics import app as metrics_app
from src.cli.models import app as models_app
from src.cli.proposals import app as proposals_app
from src.cli.schedule import app as schedule_app
from src.cli.shrinkage import app as shrinkage_app
from src.cli.supplier import app as supplier_app
from src.cli.tenant import app as tenant_app

app = typer.Typer()
app.add_typer(count_app, name="count")
app.add_typer(shrinkage_app, name="shrinkage")
app.command()(login)
app.command()(logout)
app.add_typer(proposals_app, name="proposals")
app.add_typer(metrics_app, name="metrics")
app.add_typer(autonomy_app, name="autonomy")
app.add_typer(anomaly_app, name="anomaly")
app.add_typer(factors_app, name="factors")
app.add_typer(models_app, name="models")
app.add_typer(schedule_app, name="schedule")
app.add_typer(supplier_app, name="supplier")
app.add_typer(tenant_app, name="tenant")
app.add_typer(config_app, name="config")
app.add_typer(inventory_app, name="inventory")
app.add_typer(catalog_app, name="catalog")
app.add_typer(employee_app, name="employee")