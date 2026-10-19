"""The `ritvo` command line."""

from pathlib import Path
from typing import Annotated

import typer

from ritvo import __version__
from ritvo.connectors.registry.common import RegistryError
from ritvo.diagnostic.intake.reader import IntakeError
from ritvo.diagnostic.intake.templates import write_templates
from ritvo.diagnostic.review.questions import LEVEL_LABELS
from ritvo.diagnostic.review.report import write_report
from ritvo.diagnostic.review.scoring import ReviewError, load_answers, score
from ritvo.inspector.inventory import write_inventory
from ritvo.inspector.registry import EXPORTS, inspect_registry, parse_source

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


@app.command()
def diagnose(
    templates: Annotated[
        Path,
        typer.Option(
            "--templates",
            file_okay=False,
            help="Write the blank intake templates (agents.csv, tools.csv, people.csv) here.",
        ),
    ],
) -> None:
    """Diagnose authority gaps from intake files. Reads files only."""
    try:
        written = write_templates(templates)
    except IntakeError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(1) from None
    for path in written:
        typer.echo(f"Wrote {path}")


_NO_OWNER = "agent.no_owner"
_SHARED = "identity.shared_key_name"
_FINDING_TEXT = {
    _NO_OWNER: "agents that need an owner",
    _SHARED: "credentials used by more than one agent",
}

_EXPORTS_HELP = "\n\n".join(
    f"{platform}: {note}.\n\n" + "\n\n".join(f"    {command}" for command in commands)
    for platform, (note, commands) in EXPORTS.items()
)


@app.command(
    epilog=f"Export each file with the platform's own tool first:\n\n{_EXPORTS_HELP}",
)
def inspect(
    registry: Annotated[
        list[str],
        typer.Option(
            "--registry",
            metavar="<platform>:<file>",
            help="A platform's agent export to read: agentcore or vertex. Repeatable.",
        ),
    ],
    out: Annotated[
        Path,
        typer.Option("--out", dir_okay=False, help="Where to write the inventory."),
    ] = Path("inventory.json"),
) -> None:
    """Write inventory.json from files only. Read-only and local; never calls a platform."""
    try:
        sources = [parse_source(value) for value in registry]
        run = inspect_registry(sources, out)
    except RegistryError as exc:
        typer.echo(f"error: {exc}", err=True)
        typer.echo("Nothing was written.", err=True)
        raise typer.Exit(1) from None
    for warning in run.warnings:
        typer.echo(f"warning: {warning}", err=True)
    write_inventory(run.inventory, out)
    width = max(len(result.platform) for result in run.files)
    for result in run.files:
        typer.echo(f"{result.platform:<{width}}  {result.agents} agents  {result.path}")
    for rule in (_NO_OWNER, _SHARED):
        count = sum(1 for finding in run.inventory.findings if finding.rule == rule)
        typer.echo(f"{rule}: {count} ({_FINDING_TEXT[rule]})")
    typer.echo(f"Wrote {out}")
