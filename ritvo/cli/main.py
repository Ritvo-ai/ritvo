"""The `ritvo` command line."""

from pathlib import Path
from typing import Annotated

import typer

from ritvo import __version__
from ritvo.diagnostic.review.questions import LEVEL_LABELS
from ritvo.diagnostic.review.report import write_report
from ritvo.diagnostic.review.scoring import ReviewError, load_answers, score

app = typer.Typer(no_args_is_help=True, add_completion=False)


def _show_version(value: bool) -> None:
    if value:
        typer.echo(f"ritvo {__version__}")
        raise typer.Exit()


@app.callback()
def main(
    version: Annotated[
        bool,
        typer.Option(
            "--version", callback=_show_version, is_eager=True, help="Show the version and exit."
        ),
    ] = False,
) -> None:
    """Ritvo: who may approve what when an agent asks, and the evidence."""


@app.command()
def review(
    answers: Annotated[
        Path,
        typer.Argument(exists=True, dir_okay=False, readable=True, help="The answers file (YAML)."),
    ],
    out: Annotated[
        Path | None,
        typer.Option(
            "--out",
            dir_okay=False,
            help="Where to write the result. Default: review.html beside the answers file.",
        ),
    ] = None,
) -> None:
    """Score an authority gap review and write a one-page result. Reads files only."""
    try:
        result = score(load_answers(answers))
    except ReviewError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(1) from None
    target = out if out is not None else answers.parent / "review.html"
    write_report(result, target)
    width = max(len(area.area.title) for area in result.areas)
    for area in result.areas:
        detail = f" ({area.answered} of {len(area.questions)} answered)" if area.incomplete else ""
        typer.echo(f"{area.area.title:<{width}}  {LEVEL_LABELS[area.level]}{detail}")
    typer.echo(f"Wrote {target}")
