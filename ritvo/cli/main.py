"""The `ritvo` command line."""

import os
from collections.abc import Callable
from pathlib import Path
from typing import Annotated, NoReturn

import typer

from ritvo import __version__
from ritvo.connectors.registry.common import RegistryError
from ritvo.diagnostic.doa.draft import build_draft, draft_path, write_draft
from ritvo.diagnostic.doa.mapping import (
    confirm_mapping,
    load_mapping,
    mapping_path,
    no_input,
    save_mapping,
)
from ritvo.diagnostic.doa.matrix import DoaError, column_keys
from ritvo.diagnostic.intake.columns import AGENTS, PEOPLE, TOOLS
from ritvo.diagnostic.intake.reader import CORRECTED, Intake, IntakeError, fix_intake, load_intake
from ritvo.diagnostic.intake.templates import write_templates
from ritvo.diagnostic.review.questions import LEVEL_LABELS
from ritvo.diagnostic.review.report import write_report
from ritvo.diagnostic.review.scoring import (
    OLD_REVIEW_VERSION,
    ReviewError,
    load_answers,
    score,
)
from ritvo.inspector.code import CodeError, add_agents, is_inside, scan_code
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


def _summary(intake: Intake) -> list[str]:
    lines = []
    if intake.agents is None:
        lines.append(f"{AGENTS.name}  not given")
    else:
        no_owner = sum(1 for agent in intake.agents if agent.owner is None)
        lines.append(f"{AGENTS.name}  {len(intake.agents)} agents, {no_owner} with no owner")
    if intake.tools is None:
        lines.append(f"{TOOLS.name}  not given; tool references not checked")
    else:
        no_tier = sum(1 for tool in intake.tools if tool.tier is None)
        lines.append(f"{TOOLS.name}  {len(intake.tools)} tools, {no_tier} with no tier")
    if intake.people is None:
        lines.append(f"{PEOPLE.name}  not given; owner and deputy references not checked")
    else:
        lines.append(f"{PEOPLE.name}  {len(intake.people)} people")
    return lines


@app.command()
def diagnose(
    intake: Annotated[
        Path | None,
        typer.Argument(
            file_okay=False,
            help="A folder of intake files (agents.csv, tools.csv, people.csv) to check.",
        ),
    ] = None,
    templates: Annotated[
        Path | None,
        typer.Option(
            "--templates",
            file_okay=False,
            help="Write the blank intake templates (agents.csv, tools.csv, people.csv) here.",
        ),
    ] = None,
    fix: Annotated[
        bool,
        typer.Option(
            "--fix",
            help=f"Write a copy with the mechanical fixes applied to <folder>/{CORRECTED}/. "
            "The original files are never changed.",
        ),
    ] = False,
) -> None:
    """Diagnose authority gaps from intake files. Reads files only."""
    if (intake is None) == (templates is None):
        _fail("give an intake folder, or --templates <dir>")
    if fix and intake is None:
        _fail("--fix needs an intake folder")
    try:
        if templates is not None:
            for path in write_templates(templates):
                typer.echo(f"Wrote {path}")
        elif intake is not None:
            _diagnose_intake(intake, fix)
    except IntakeError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(1) from None


def _problems(problems: tuple[str, ...]) -> None:
    for problem in problems:
        typer.echo(f"error: {problem}", err=True)


def _diagnose_intake(intake: Path, fix: bool) -> None:
    if not any((intake / file.name).is_file() for file in (AGENTS, TOOLS, PEOPLE)):
        raise IntakeError(
            f"no intake files in {intake}; write blank ones with ritvo diagnose --templates <dir>"
        )
    try:
        result = load_intake(intake)
    except IntakeError as exc:
        if fix:
            _write_corrected(intake, exc)
            return
        _problems(exc.problems)
        if exc.fixes:
            typer.echo(
                f"{exc.fixes} of {len(exc.problems)} problems have a mechanical fix; run again "
                f"with --fix to write a corrected copy to {intake / CORRECTED}",
                err=True,
            )
        typer.echo("Nothing was written.", err=True)
        raise typer.Exit(1) from None
    for line in _summary(result):
        typer.echo(line)
    if fix:
        typer.echo("Nothing to fix.")
    typer.echo("Nothing was written. The draft ritvo.yaml and the gap report are not built yet.")


def _write_corrected(intake: Path, error: IntakeError) -> None:
    corrected = fix_intake(intake)
    if corrected is None:
        _problems(error.problems)
        typer.echo("No problem has a mechanical fix. Nothing was written.", err=True)
        raise typer.Exit(1)
    for path in corrected.files:
        typer.echo(f"Wrote {path}")
    typer.echo(f"{corrected.fixes} fixes applied; the original files were not changed.")
    if corrected.remaining:
        _problems(corrected.remaining)
        typer.echo(
            f"{len(corrected.remaining)} problems remain in {corrected.directory}.", err=True
        )
        raise typer.Exit(1)
    for line in _summary(load_intake(corrected.directory)):
        typer.echo(line)


def _prompt(question: str, default: str, check: Callable[[str], str | None]) -> str:
    while True:
        answer = str(typer.prompt(question, default=default)).strip()
        problem = check(answer)
        if problem is None:
            return answer
        typer.echo(f"  {problem}", err=True)


@app.command("import-doa")
def import_doa(
    matrix: Annotated[
        Path,
        typer.Argument(
            dir_okay=False, help="The delegation-of-authority sheet, exported as CSV UTF-8."
        ),
    ],
    intake: Annotated[
        Path | None,
        typer.Option(
            "--intake",
            exists=True,
            file_okay=False,
            help="An intake folder: tools.csv to link decisions to tools, people.csv for roles.",
        ),
    ] = None,
    unattended: Annotated[
        bool,
        typer.Option("--no-input", help="Ask nothing; use the mapping file as it is."),
    ] = False,
) -> None:
    """Draft approval bands and roles from a DoA matrix. Reads files only."""
    mapping_file = mapping_path(matrix)
    draft_file = draft_path(matrix)
    try:
        tools = people = None
        if intake is not None:
            try:
                files = load_intake(intake)
            except IntakeError as exc:
                raise DoaError(f"{exc}; check it with ritvo diagnose {intake}") from None
            tools, people = files.tools, files.people
        previous = load_mapping(mapping_file)
        if unattended and previous is None:
            raise DoaError(
                f"--no-input needs {mapping_file.name}; run once without --no-input to write it"
            )
        source, mapping = confirm_mapping(
            matrix, previous, tools, no_input if unattended else _prompt
        )
        draft = build_draft(source, mapping, people)
    except DoaError as exc:
        _fail(str(exc))
    header = source.row(mapping.header_line) or ()
    used = {mapping.decision_column, *mapping.level_columns}
    used |= {column for column in (mapping.level_column, mapping.limit_column) if column}
    ignored = [key for key in column_keys(header) if key not in used]
    save_mapping(mapping, mapping_file)
    write_draft(draft, draft_file)
    if ignored:
        typer.echo(f"Ignored columns: {', '.join(ignored)}")
    for problem in draft.problems:
        typer.echo(f"problem: {problem}", err=True)
    typer.echo(
        f"{draft.decisions} decisions, {draft.roles} roles, {draft.tools} tools linked, "
        f"{len(draft.problems)} problems"
    )
    typer.echo(f"Wrote {mapping_file}")
    typer.echo(f"Wrote {draft_file}")


_NO_OWNER = "agent.no_owner"
_SHARED = "identity.shared_key_name"
_UNOWNED_TOOL = "tool.destructive_unowned"
_FINDING_TEXT = {
    _NO_OWNER: "agents that need an owner",
    _UNOWNED_TOOL: "high tier tools reached by an agent with no owner, or by no known agent",
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
    code: Annotated[
        list[Path] | None,
        typer.Option(
            "--code",
            metavar="<dir>",
            file_okay=False,
            help="A source folder to scan for agents and environment variable names "
            "(Python; parsed, never run). Repeatable.",
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
    if (registry or reads_configs or code) and inventory is not None:
        _fail("--inventory cannot be combined with --registry, --code, --defaults or --config")
    if inventory is not None and owners is None:
        _fail("--inventory needs --owners")
    if project is not None and not defaults:
        _fail("--project needs --defaults")
    if not registry and not reads_configs and not code and inventory is None:
        _fail(
            "give at least one --registry <platform>:<file>, --code <dir>, --defaults or "
            "--config <client>:<file>, or --inventory <file> with --owners <agents.csv>"
        )
    if code and is_inside(out, code):
        _fail(f"--out {out} is inside a --code folder; write the inventory outside it")
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
            base = add_servers(base, configs)
            warnings += tuple(configs.warnings)
        scanned = scan_code(code) if code else None
        if scanned is not None:
            base = add_agents(base, scanned)
        if base is not None:
            result = base
        if overlay is not None:
            applied = apply_overlay(result, overlay)
            result = applied.inventory
            warnings += applied.warnings
    except (RegistryError, ConfigError, CodeError, OverlayError, InventoryError) as exc:
        _fail(str(exc))
    for key in unresolved:
        typer.echo(f"warning: {key}: not checked, APPDATA is not set", err=True)
    for warning in warnings:
        typer.echo(f"warning: {warning}", err=True)
    write_inventory(result, out)
    width = max((len(file.platform) for file in files), default=0)
    for file in files:
        typer.echo(f"{file.platform:<{width}}  {file.agents} agents  {file.path}")
    if scanned is not None:
        for root, read, found, tools in scanned.results:
            typer.echo(f"code  {read} Python files, {found} agents, {tools} tools  {root}")
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
    for rule in (_NO_OWNER, _SHARED, _UNOWNED_TOOL):
        count = sum(1 for finding in result.findings if finding.rule == rule)
        typer.echo(f"{rule}: {count} ({_FINDING_TEXT[rule]})")
    typer.echo(f"Wrote {out}")
