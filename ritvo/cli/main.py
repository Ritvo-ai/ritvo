"""The `ritvo` command line."""

import os
from pathlib import Path
from typing import Annotated, NoReturn

import typer

from ritvo import __version__
from ritvo.connectors.registry.common import RegistryError
from ritvo.diagnostic.intake.reader import IntakeError
from ritvo.diagnostic.intake.templates import write_templates
from ritvo.diagnostic.review.questions import LEVEL_LABELS
from ritvo.diagnostic.review.report import write_report
from ritvo.diagnostic.review.scoring import (
    OLD_REVIEW_VERSION,
    ReviewError,
    load_answers,
    score,
)
from ritvo.inspector.config import (
    CLIENTS,
    ConfigError,
    Location,
    add_servers,
    config_location,
    default_locations,
    parse_config,
    read_configs,
    system_name,
)
from ritvo.inspector.inventory import InventoryError, load_inventory, write_inventory
from ritvo.inspector.owners import OverlayError, apply_overlay, read_overlay
from ritvo.inspector.registry import EXPORTS, FileResult, inspect_registry, parse_source

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
    if result.answers_version == OLD_REVIEW_VERSION:
        typer.echo(
            f"note: {answers} was read as review_version {OLD_REVIEW_VERSION}. Unknown answers "
            "no longer count as a gap, so grades may improve. The file was not changed.",
            err=True,
        )
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


def _fail(message: str) -> NoReturn:
    typer.echo(f"error: {message}", err=True)
    typer.echo("Nothing was written.", err=True)
    raise typer.Exit(1)


def _same_file(a: Path, b: Path) -> bool:
    if a.exists() and b.exists():
        return a.samefile(b)
    return a.resolve() == b.resolve()


@app.command(
    epilog=f"Export each file with the platform's own tool first:\n\n{_EXPORTS_HELP}",
)
def inspect(
    registry: Annotated[
        list[str] | None,
        typer.Option(
            "--registry",
            metavar="<platform>:<file>",
            help="A platform's agent export to read: agentcore or vertex. Repeatable.",
        ),
    ] = None,
    defaults: Annotated[
        bool,
        typer.Option(
            "--defaults",
            help="Read the MCP config of each client from its well-known locations (SPEC 7.1).",
        ),
    ] = False,
    config: Annotated[
        list[str] | None,
        typer.Option(
            "--config",
            metavar="<client>:<file>",
            help=f"An MCP config file to read: {', '.join(CLIENTS)}. Repeatable.",
        ),
    ] = None,
    project: Annotated[
        Path | None,
        typer.Option(
            "--project",
            exists=True,
            file_okay=False,
            help="The project folder for project config files. Default: the current folder.",
        ),
    ] = None,
    owners: Annotated[
        Path | None,
        typer.Option(
            "--owners",
            metavar="<agents.csv>",
            dir_okay=False,
            help="Set owner and owner_team from an intake agents.csv (SPEC 7.4).",
        ),
    ] = None,
    inventory: Annotated[
        Path | None,
        typer.Option(
            "--inventory",
            dir_okay=False,
            help="An existing inventory to apply --owners to. It is never changed.",
        ),
    ] = None,
    out: Annotated[
        Path,
        typer.Option("--out", dir_okay=False, help="Where to write the inventory."),
    ] = Path("inventory.json"),
) -> None:
    """Write inventory.json from files only. Read-only and local; never calls a platform."""
    reads_configs = defaults or bool(config)
    if (registry or reads_configs) and inventory is not None:
        _fail("--inventory cannot be combined with --registry, --defaults or --config")
    if inventory is not None and owners is None:
        _fail("--inventory needs --owners")
    if project is not None and not defaults:
        _fail("--project needs --defaults")
    if not registry and not reads_configs and inventory is None:
        _fail(
            "give at least one --registry <platform>:<file>, --defaults or "
            "--config <client>:<file>, or --inventory <file> with --owners <agents.csv>"
        )
    if inventory is not None and _same_file(inventory, out):
        _fail(f"--out {out} is the file named by --inventory; choose another path")
    try:
        overlay = read_overlay(owners) if owners is not None else None
        if overlay is not None and any(_same_file(path, out) for path in overlay.files_read):
            _fail(f"--out {out} is one of the intake files; choose another path")
        locations: list[Location] = []
        unresolved: list[str] = []
        if defaults:
            system = system_name()
            # SPEC 7.1: HOME or USERPROFILE (through pathlib) and APPDATA, and no other variable.
            appdata = os.environ.get("APPDATA") if system == "windows" else None
            locations, unresolved = default_locations(
                system,
                Path.home(),
                Path(appdata) if appdata else None,
                (project or Path.cwd()).resolve(),
            )
        named: dict[Path, str] = {}
        for value in config or []:
            client, config_file = parse_config(value)
            resolved = Path(config_file).resolve()
            if resolved in named:
                raise ConfigError(f"--config names {config_file} twice")
            named[resolved] = config_file
            locations.append(config_location(client, config_file))
        configs = read_configs(locations) if reads_configs else None
        if configs is not None and any(_same_file(Path(path), out) for path in configs.paths):
            _fail(f"--out {out} is one of the config files read; choose another path")
        files: tuple[FileResult, ...] = ()
        warnings: tuple[str, ...] = ()
        base = None
        if registry:
            run = inspect_registry([parse_source(value) for value in registry], out)
            base, files, warnings = run.inventory, run.files, run.warnings
        elif inventory is not None:
            base = load_inventory(inventory)
        if configs is not None:
            result = add_servers(base, configs)
            warnings += tuple(configs.warnings)
        elif base is not None:
            result = base
        if overlay is not None:
            applied = apply_overlay(result, overlay)
            result = applied.inventory
            warnings += applied.warnings
    except (RegistryError, ConfigError, OverlayError, InventoryError) as exc:
        _fail(str(exc))
    for key in unresolved:
        typer.echo(f"warning: {key}: not checked, APPDATA is not set", err=True)
    for warning in warnings:
        typer.echo(f"warning: {warning}", err=True)
    write_inventory(result, out)
    width = max((len(file.platform) for file in files), default=0)
    for file in files:
        typer.echo(f"{file.platform:<{width}}  {file.agents} agents  {file.path}")
    if configs is not None:
        width = max((len(location.key) for location in configs.results), default=0)
        for location in configs.results:
            detail = (
                f"{location.servers} servers" if location.status == "found" else location.status
            )
            typer.echo(f"{location.key:<{width}}  {detail}  {location.path}")
    if overlay is not None:
        typer.echo(
            f"owners: {applied.fields_set} fields set, {applied.replaced} replaced a value  "
            f"{overlay.path}"
        )
    for rule in (_NO_OWNER, _SHARED):
        count = sum(1 for finding in result.findings if finding.rule == rule)
        typer.echo(f"{rule}: {count} ({_FINDING_TEXT[rule]})")
    typer.echo(f"Wrote {out}")
