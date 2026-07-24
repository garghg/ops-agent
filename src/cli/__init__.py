import typer
from src.cli.count import app as count_app
from src.cli.shrinkage import app as shrinkage_app
from src.cli.login import login, logout

app = typer.Typer()
app.add_typer(count_app, name="count")
app.add_typer(shrinkage_app, name="shrinkage")
app.command()(login)
app.command()(logout)