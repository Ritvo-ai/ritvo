"""Read and validate the intake files (docs/SPEC.md section 8.2)."""

import csv
import io
import re
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from ritvo.diagnostic.intake.columns import (
    AGENTS,
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


class IntakeError(Exception):
    """An intake file cannot be read or is not valid. Nothing is written."""


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


class _Row:
    def __init__(self, file: IntakeFile, line: int, cells: dict[str, str], problems: list[str]):
        self.file = file
        self.line = line
        self._cells = cells
        self._problems = problems

    def problem(self, column: str, message: str) -> None:
        self._problems.append(f"{self.file.name} line {self.line}, column {column}: {message}")

    def text(self, column: str) -> str | None:
        return self._cells.get(column, "").strip() or None

    def key(self, column: str) -> str | None:
        if self.text(column) is None:
            self.problem(column, "required")
            return None
        return self.id(column)

    def id(self, column: str) -> str | None:
        value = self.text(column)
        if value is None:
            return None
        if not _ID.fullmatch(value):
            self.problem(column, f"not a valid ID ({_ID_RULE})")
            return None
        return value

    def ids(self, column: str) -> tuple[str, ...] | None:
        value = self.text(column)
        if value is None:
            return None
        items: list[str] = []
        for item in (part.strip() for part in value.split(";")):
            if not item:
                continue
            if not _ID.fullmatch(item):
                self.problem(column, f"not a valid ID ({_ID_RULE})")
            elif item in items:
                self.problem(column, f"{_short(item)} is listed twice")
            else:
                items.append(item)
        return tuple(items) or None

    def choice[E: StrEnum](self, column: str, kind: type[E]) -> E | None:
        value = self.text(column)
        if value is None or value.lower() == UNKNOWN:
            return None
        try:
            return kind(value.lower())
        except ValueError:
            options = ", ".join([*(member.value for member in kind), UNKNOWN])
            self.problem(column, f"must be one of {options}, or empty")
            return None

    def minutes(self, column: str) -> int | None:
        value = self.text(column)
        if value is None:
            return None
        if not _MINUTES.fullmatch(value) or int(value) < 1:
            self.problem(column, "must be a whole number of minutes, at least 1")
            return None
        return int(value)


def _read(directory: Path, file: IntakeFile, problems: list[str]) -> list[_Row] | None:
    path = directory / file.name
    if not path.exists():
        return None
    if not path.is_file():
        problems.append(f"{file.name} is not a file")
        return []
    try:
        text = path.read_bytes().decode("utf-8-sig")
    except UnicodeDecodeError:
        problems.append(f"{file.name} is not UTF-8 text")
        return []
    reader = csv.reader(io.StringIO(text, newline=""), strict=True)
    try:
        header = [name.strip() for name in next(reader)]
    except StopIteration:
        problems.append(f"{file.name} has no header row")
        return []
    except csv.Error:
        problems.append(f"{file.name} line 1: not valid CSV")
        return []

    valid = file.column_names
    valid_text = ", ".join(valid)
    header_problems = len(problems)
    for number, name in enumerate(header, start=1):
        if name not in valid:
            problems.append(
                f"{file.name} line 1, column {number}: unknown column; valid columns: {valid_text}"
            )
        elif header.index(name) < number - 1:
            problems.append(
                f"{file.name} line 1, column {number}: repeated column {name}; "
                f"valid columns: {valid_text}"
            )
    for column in file.columns:
        if column.required and column.name not in header:
            problems.append(
                f"{file.name} line 1: missing required column {column.name}; "
                f"valid columns: {valid_text}"
            )
    if len(problems) > header_problems:
        return []

    rows: list[_Row] = []
    while True:
        line = reader.line_num + 1
        try:
            cells = next(reader)
        except StopIteration:
            break
        except csv.Error:
            problems.append(f"{file.name} line {line}: not valid CSV")
            break
        if not any(cell.strip() for cell in cells):
            continue
        if len(cells) > len(header):
            problems.append(
                f"{file.name} line {line}: {len(cells)} cells but the header has "
                f"{len(header)} columns"
            )
            continue
        rows.append(_Row(file, line, dict(zip(header, cells, strict=False)), problems))
    return rows


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


def load_intake(directory: Path) -> Intake:
    if not directory.is_dir():
        raise IntakeError(f"{directory} is not a directory")
    problems: list[str] = []

    agent_rows = _read(directory, AGENTS, problems)
    tool_rows = _read(directory, TOOLS, problems)
    person_rows = _read(directory, PEOPLE, problems)

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
                row.problem("owner", f"{_short(agent.owner)} is not in {PEOPLE.name}")
        for row, key, person in people:
            if person.deputy is None:
                continue
            if person.deputy == key:
                row.problem("deputy", "a person cannot be their own deputy")
            elif person.deputy not in person_ids:
                row.problem("deputy", f"{_short(person.deputy)} is not in {PEOPLE.name}")
    if tool_rows is not None:
        for row, _, agent in agents:
            for tool_id in agent.tools or ():
                if tool_id not in tool_ids:
                    row.problem("tools", f"{_short(tool_id)} is not in {TOOLS.name}")

    if problems:
        raise IntakeError("; ".join(problems))
    return Intake(
        agents=None if agent_rows is None else tuple(agent for _, _, agent in agents),
        tools=None if tool_rows is None else tuple(tool for _, _, tool in tools),
        people=None if person_rows is None else tuple(person for _, _, person in people),
    )
