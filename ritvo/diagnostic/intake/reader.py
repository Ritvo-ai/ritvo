"""Read and validate the intake files (docs/SPEC.md section 8.2)."""

import csv
import difflib
import io
import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path

from ritvo.diagnostic.intake.columns import (
    AGENTS,
    INTAKE_FILES,
    PEOPLE,
    TOOLS,
    UNKNOWN,
    Access,
    IntakeFile,
    Mode,
    Tier,
)

_ID = re.compile(r"[a-z0-9][a-z0-9._-]{0,63}")
_ID_RULE = "1 to 64 characters from a-z, 0-9, '.', '_' and '-', starting with a letter or digit"
_MINUTES = re.compile(r"[0-9]+")
_SPACES = re.compile(r"\s+")
_HEADER_SEPARATORS = re.compile(r"[\s-]+")
# Columns that hold a person_id. A space or '@' there means a full name or an email address,
# which is never turned into an ID.
_PERSON_COLUMNS = {"person_id", "owner", "deputy"}
CORRECTED = "corrected"


class IntakeError(Exception):
    """An intake file cannot be read or is not valid. Nothing is written."""

    def __init__(self, message: str, problems: tuple[str, ...] = (), fixes: int = 0) -> None:
        super().__init__(message)
        self.problems = problems or (message,)
        self.fixes = fixes


@dataclass(frozen=True)
class Agent:
    agent_id: str
    name: str | None
    platform: str | None
    built_by_team: str | None
    owner: str | None
    owner_team: str | None
    mode: Mode | None
    credential: str | None
    tools: tuple[str, ...] | None
    line: int


@dataclass(frozen=True)
class Tool:
    tool_id: str
    system: str | None
    access: Access | None
    description: str | None
    tier: Tier | None
    owner_team: str | None
    approval_ttl_minutes: int | None


@dataclass(frozen=True)
class Person:
    person_id: str
    team: str | None
    roles: tuple[str, ...] | None
    deputy: str | None


@dataclass(frozen=True)
class Intake:
    """None for a file that is absent; an empty cell is None, meaning not known."""

    agents: tuple[Agent, ...] | None
    tools: tuple[Tool, ...] | None
    people: tuple[Person, ...] | None


def _short(value: str) -> str:
    text = repr(value)
    return text if len(text) <= 40 else text[:37] + "..."


def _did_you_mean(value: str, options: Iterable[str]) -> str:
    """A hint naming the closest option, an ID or fixed value; never the value itself."""
    close = difflib.get_close_matches(value, sorted(options), n=1, cutoff=0.6)
    return f"; did you mean {_short(close[0])}?" if close else ""


@dataclass
class _Problems:
    """Problems in file order, and how many carry a mechanical fix."""

    messages: list[str] = field(default_factory=list)
    fixes: int = 0

    def append(self, message: str) -> None:
        self.messages.append(message)

    def __len__(self) -> int:
        return len(self.messages)


class _Row:
    def __init__(self, file: IntakeFile, line: int, cells: dict[str, str], problems: _Problems):
        self.file = file
        self.line = line
        self._cells = cells
        self._problems = problems
        # Column name to the corrected cell, for the corrected copy.
        self.fixes: dict[str, str] = {}

    def problem(self, column: str, message: str) -> None:
        self._problems.append(f"{self.file.name} line {self.line}, column {column}: {message}")

    def text(self, column: str) -> str | None:
        return self._cells.get(column, "").strip() or None

    def key(self, column: str) -> str | None:
        if self.text(column) is None:
            self.problem(column, "required")
            return None
        return self.id(column)

    def _fix_id(self, column: str, value: str) -> tuple[str | None, str]:
        """The mechanical fix for an invalid ID, or None with the reason there is none."""
        if column in _PERSON_COLUMNS:
            if "@" in value:
                return None, "; use a person_id, not an email address"
            if _SPACES.search(value):
                return None, "; use a person_id, not a full name"
        if "@" in value:
            return None, ""
        fixed = _SPACES.sub("_", value).lower()
        return (fixed, "") if _ID.fullmatch(fixed) else (None, "")

    def id(self, column: str) -> str | None:
        value = self.text(column)
        if value is None:
            return None
        if not _ID.fullmatch(value):
            fixed, reason = self._fix_id(column, value)
            if fixed is None:
                self.problem(column, f"not a valid ID ({_ID_RULE}){reason}")
                return None
            self.problem(column, f"not a valid ID ({_ID_RULE}); fix: {_short(fixed)}")
            self._problems.fixes += 1
            self.fixes[column] = fixed
            return fixed
        return value

    def ids(self, column: str) -> tuple[str, ...] | None:
        value = self.text(column)
        if value is None:
            return None
        items: list[str] = []
        fixed_items: list[str] = []
        fixed_any = False
        for item in (part.strip() for part in value.split(";")):
            if not item:
                continue
            if not _ID.fullmatch(item):
                fixed, reason = self._fix_id(column, item)
                if fixed is None:
                    self.problem(column, f"not a valid ID ({_ID_RULE}){reason}")
                    fixed_items.append(item)
                    continue
                self.problem(column, f"not a valid ID ({_ID_RULE}); fix: {_short(fixed)}")
                self._problems.fixes += 1
                fixed_any = True
                item = fixed
            fixed_items.append(item)
            if item in items:
                self.problem(column, f"{_short(item)} is listed twice")
            else:
                items.append(item)
        if fixed_any:
            self.fixes[column] = ";".join(fixed_items)
        return tuple(items) or None

    def choice[E: StrEnum](self, column: str, kind: type[E]) -> E | None:
        value = self.text(column)
        if value is None or value.lower() == UNKNOWN:
            return None
        try:
            return kind(value.lower())
        except ValueError:
            values = [*(member.value for member in kind), UNKNOWN]
            hint = _did_you_mean(value.lower(), values)
            self.problem(column, f"must be one of {', '.join(values)}, or empty{hint}")
            return None

    def minutes(self, column: str) -> int | None:
        value = self.text(column)
        if value is None:
            return None
        if not _MINUTES.fullmatch(value) or int(value) < 1:
            self.problem(column, "must be a whole number of minutes, at least 1")
            return None
        return int(value)


@dataclass
class _Parsed:
    """One file as read. `rows` is None when the file could not be parsed into rows."""

    file: IntakeFile
    raw: bytes
    header: list[str] | None = None
    header_fixed: bool = False
    # Every data row after the header, as cells, with its line number; blank rows included.
    lines: list[tuple[int, list[str]]] = field(default_factory=list)
    rows: list[_Row] | None = None


def _fix_column(name: str) -> str:
    return _HEADER_SEPARATORS.sub("_", name.strip().lower())


def _header(file: IntakeFile, header: list[str], problems: _Problems) -> list[str] | None:
    """The header to read rows with, or None when a problem has no mechanical fix."""
    valid = file.column_names
    valid_text = ", ".join(valid)
    effective: list[str] = []
    unfixable = False
    for number, name in enumerate(header, start=1):
        if name in valid:
            if name in effective:
                problems.append(
                    f"{file.name} line 1, column {number}: repeated column {name}; "
                    f"valid columns: {valid_text}"
                )
                unfixable = True
            effective.append(name)
            continue
        fixed = _fix_column(name)
        if fixed in valid and fixed not in header and fixed not in effective:
            problems.append(f"{file.name} line 1, column {number}: unknown column; fix: {fixed}")
            problems.fixes += 1
            effective.append(fixed)
            continue
        problems.append(
            f"{file.name} line 1, column {number}: unknown column; valid columns: "
            f"{valid_text}{_did_you_mean(fixed, valid)}"
        )
        unfixable = True
        effective.append(name)
    for column in file.columns:
        if column.required and column.name not in effective:
            problems.append(
                f"{file.name} line 1: missing required column {column.name}; "
                f"valid columns: {valid_text}"
            )
            unfixable = True
    return None if unfixable else effective


def _read(directory: Path, file: IntakeFile, problems: _Problems) -> _Parsed | None:
    path = directory / file.name
    if not path.exists():
        return None
    if not path.is_file():
        problems.append(f"{file.name} is not a file")
        return _Parsed(file, b"")
    raw = path.read_bytes()
    parsed = _Parsed(file, raw)
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        problems.append(f"{file.name} is not UTF-8 text")
        return parsed
    reader = csv.reader(io.StringIO(text, newline=""), strict=True)
    try:
        header = [name.strip() for name in next(reader)]
    except StopIteration:
        problems.append(f"{file.name} has no header row")
        return parsed
    except csv.Error:
        problems.append(f"{file.name} line 1: not valid CSV")
        return parsed

    effective = _header(file, header, problems)
    if effective is None:
        return parsed

    lines: list[tuple[int, list[str]]] = []
    while True:
        line = reader.line_num + 1
        try:
            cells = next(reader)
        except StopIteration:
            break
        except csv.Error:
            problems.append(f"{file.name} line {line}: not valid CSV")
            return parsed
        lines.append((line, cells))

    rows: list[_Row] = []
    for line, cells in lines:
        if not any(cell.strip() for cell in cells):
            continue
        if len(cells) > len(effective):
            problems.append(
                f"{file.name} line {line}: {len(cells)} cells but the header has "
                f"{len(effective)} columns"
            )
            continue
        rows.append(_Row(file, line, dict(zip(effective, cells, strict=False)), problems))
    parsed.header = effective
    parsed.header_fixed = effective != header
    parsed.lines = lines
    parsed.rows = rows
    return parsed


def _check_unique(rows: list[tuple[_Row, str | None]], column: str) -> set[str]:
    first: dict[str, int] = {}
    for row, value in rows:
        if value is None:
            continue
        if value in first:
            row.problem(column, f"duplicate ID {_short(value)}, first on line {first[value]}")
        else:
            first[value] = row.line
    return set(first)


def _load(directory: Path) -> tuple[Intake, _Problems, list[_Parsed]]:
    if not directory.is_dir():
        raise IntakeError(f"{directory} is not a directory")
    problems = _Problems()

    parsed = {
        file.name: result
        for file in INTAKE_FILES
        if (result := _read(directory, file, problems)) is not None
    }

    def rows_of(file: IntakeFile) -> list[_Row] | None:
        if file.name not in parsed:
            return None
        return parsed[file.name].rows or []

    agent_rows = rows_of(AGENTS)
    tool_rows = rows_of(TOOLS)
    person_rows = rows_of(PEOPLE)

    agents = [
        (
            row,
            row.key("agent_id"),
            Agent(
                agent_id=row.text("agent_id") or "",
                name=row.text("name"),
                platform=row.text("platform"),
                built_by_team=row.id("built_by_team"),
                owner=row.id("owner"),
                owner_team=row.id("owner_team"),
                mode=row.choice("mode", Mode),
                credential=row.text("credential"),
                tools=row.ids("tools"),
                line=row.line,
            ),
        )
        for row in agent_rows or []
    ]
    tools = [
        (
            row,
            row.key("tool_id"),
            Tool(
                tool_id=row.text("tool_id") or "",
                system=row.text("system"),
                access=row.choice("access", Access),
                description=row.text("description"),
                tier=row.choice("tier", Tier),
                owner_team=row.id("owner_team"),
                approval_ttl_minutes=row.minutes("approval_ttl_minutes"),
            ),
        )
        for row in tool_rows or []
    ]
    people = [
        (
            row,
            row.key("person_id"),
            Person(
                person_id=row.text("person_id") or "",
                team=row.id("team"),
                roles=row.ids("roles"),
                deputy=row.id("deputy"),
            ),
        )
        for row in person_rows or []
    ]

    _check_unique([(row, key) for row, key, _ in agents], "agent_id")
    tool_ids = _check_unique([(row, key) for row, key, _ in tools], "tool_id")
    person_ids = _check_unique([(row, key) for row, key, _ in people], "person_id")

    # References into an absent file are not checked.
    if person_rows is not None:
        for row, _, agent in agents:
            if agent.owner is not None and agent.owner not in person_ids:
                hint = _did_you_mean(agent.owner, person_ids)
                row.problem("owner", f"{_short(agent.owner)} is not in {PEOPLE.name}{hint}")
        for row, key, person in people:
            if person.deputy is None:
                continue
            if person.deputy == key:
                row.problem("deputy", "a person cannot be their own deputy")
            elif person.deputy not in person_ids:
                hint = _did_you_mean(person.deputy, person_ids)
                row.problem("deputy", f"{_short(person.deputy)} is not in {PEOPLE.name}{hint}")
    if tool_rows is not None:
        for row, _, agent in agents:
            for tool_id in agent.tools or ():
                if tool_id not in tool_ids:
                    hint = _did_you_mean(tool_id, tool_ids)
                    row.problem("tools", f"{_short(tool_id)} is not in {TOOLS.name}{hint}")

    intake = Intake(
        agents=None if agent_rows is None else tuple(agent for _, _, agent in agents),
        tools=None if tool_rows is None else tuple(tool for _, _, tool in tools),
        people=None if person_rows is None else tuple(person for _, _, person in people),
    )
    return intake, problems, list(parsed.values())


def load_intake(directory: Path) -> Intake:
    intake, problems, _ = _load(directory)
    if problems:
        raise IntakeError("; ".join(problems.messages), tuple(problems.messages), problems.fixes)
    return intake


@dataclass(frozen=True)
class Corrected:
    """The corrected copy: where it went, how many fixes it holds, and what is still wrong."""

    directory: Path
    files: tuple[Path, ...]
    fixes: int
    remaining: tuple[str, ...]


def _write(parsed: _Parsed, target: Path) -> None:
    if parsed.header is None or parsed.rows is None:
        # Not parsed into rows: copied byte for byte.
        target.write_bytes(parsed.raw)
        return
    fixes = {row.line: row.fixes for row in parsed.rows if row.fixes}
    width = len(parsed.header)
    out = io.StringIO(newline="")
    writer = csv.writer(out, lineterminator="\r\n")
    writer.writerow(parsed.header)
    for line, cells in parsed.lines:
        row_fixes = fixes.get(line)
        if row_fixes:
            cells = cells + [""] * (width - len(cells))
            for column, value in row_fixes.items():
                cells[parsed.header.index(column)] = value
        writer.writerow(cells)
    target.write_bytes(out.getvalue().encode("utf-8"))


def fix_intake(directory: Path) -> Corrected | None:
    """Write a corrected copy to <directory>/corrected/; None when nothing has a fix.

    Only mechanical fixes are applied. The original files are never changed.
    """
    _, problems, parsed = _load(directory)
    if not problems.fixes:
        return None
    target = directory / CORRECTED
    if target.exists():
        raise IntakeError(f"{target} already exists; nothing was written")
    target.mkdir()
    files = []
    for result in parsed:
        if not (directory / result.file.name).is_file():
            continue
        path = target / result.file.name
        _write(result, path)
        files.append(path)
    try:
        load_intake(target)
        remaining: tuple[str, ...] = ()
    except IntakeError as exc:
        remaining = exc.problems
    return Corrected(target, tuple(files), problems.fixes, remaining)
