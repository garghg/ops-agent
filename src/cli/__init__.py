import typer

from src.cli.anomaly import app as anomaly_app
from src.cli.autonomy import app as autonomy_app
from src.cli.count import app as count_app
from src.cli.factors import app as factors_app
from src.cli.login import login, logout
from src.cli.metrics import app as metrics_app
from src.cli.proposals import app as proposals_app
from src.cli.shrinkage import app as shrinkage_app

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