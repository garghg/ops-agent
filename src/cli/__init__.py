import typer
from src.cli.count import app as count_app

app = typer.Typer()
app.add_typer(count_app, name="count")