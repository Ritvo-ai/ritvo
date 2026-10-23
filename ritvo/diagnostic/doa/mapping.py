"""The mapping file: how a matrix is read, as confirmed by a person (docs/SPEC.md section 8.4).

The questions are asked through `Ask`, so the command line and a later interface ask the same
questions and write the same file.
"""

import difflib
import re
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Annotated, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, StrictStr, ValidationError

from ritvo.diagnostic.doa.matrix import (
    DELIMITERS,
    DoaError,
    Kind,
    Layout,
    Matrix,
    column_keys,
    data_rows,
    propose,
    read_cell,
    read_matrix,
    role_id,
)
from ritvo.diagnostic.intake.reader import Tool

MAPPING_VERSION = 1
_ID = re.compile(r"[a-z0-9][a-z0-9._-]{0,63}")
_CURRENCY = re.compile(r"[A-Z]{3}")

Id = Annotated[StrictStr, Field(pattern=rf"^{_ID.pattern}$")]

# A question: its text, its default, and a check that returns an error message or None.
Ask = Callable[[str, str, Callable[[str], str | None]], str]


class Unanswered(DoaError):
    """A question the mapping file does not answer, under --no-input."""


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


class Link(_Model):
    tool: Id
    parameter: Id


class Mapping(_Model):
    doa_mapping_version: Literal[1]
    matrix_sha256: Annotated[StrictStr, Field(pattern=r"^[0-9a-f]{64}$")]
    delimiter: Literal[",", ";", "tab"]
    header_line: Annotated[int, Field(ge=1)]
    header: list[StrictStr]
    layout: Literal["wide", "long"]
    decision_column: StrictStr
    level_columns: list[StrictStr]
    level_column: StrictStr | None
    limit_column: StrictStr | None
    thousands: Literal[",", "."]
    currency: Annotated[StrictStr, Field(pattern=r"^[A-Z]{3}$")] | None
    roles: dict[StrictStr, Id]
    codes: dict[StrictStr, Literal["approves", "none"]]
    links: dict[StrictStr, list[Link]]


def mapping_path(matrix: Path) -> Path:
    return matrix.with_name(f"{matrix.stem}.mapping.yaml")


def load_mapping(path: Path) -> Mapping | None:
    if not path.exists():
        return None
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        return Mapping.model_validate(data)
    except (yaml.YAMLError, UnicodeDecodeError):
        raise DoaError(f"{path.name} is not a YAML file") from None
    except ValidationError as exc:
        where = ", ".join(
            ".".join(str(part) for part in error["loc"]) or "the file"
            for error in exc.errors(include_url=False, include_input=False)
        )
        raise DoaError(f"{path.name}: not a valid mapping file ({where})") from None


def save_mapping(mapping: Mapping, path: Path) -> None:
    text = yaml.safe_dump(mapping.model_dump(mode="json"), sort_keys=False, allow_unicode=True)
    path.write_text(text, encoding="utf-8", newline="\n")


def no_input(question: str, default: str, check: Callable[[str], str | None]) -> str:
    raise Unanswered(f"not answered by the mapping file: {question}")


def _one_of(options: Iterable[str]) -> Callable[[str], str | None]:
    allowed = [str(option) for option in options]

    def check(answer: str) -> str | None:
        return None if answer in allowed else f"answer one of: {', '.join(allowed)}"

    return check


def _valid_id(answer: str) -> str | None:
    return None if _ID.fullmatch(answer) else "not a valid ID"


def _list(answer: str) -> list[str]:
    return [part.strip() for part in answer.split(";") if part.strip()]


def _levels(rows: list[tuple[int, dict[str, str]]], level_column: str) -> list[str]:
    names: list[str] = []
    for _, row in rows:
        name = row.get(level_column, "")
        if name and name not in names:
            names.append(name)
    return names


def _decisions(rows: list[tuple[int, dict[str, str]]], decision_column: str) -> list[str]:
    names: list[str] = []
    for _, row in rows:
        name = row.get(decision_column, "")
        if name and name not in names:
            names.append(name)
    return names


def _codes(rows: list[tuple[int, dict[str, str]]], columns: list[str], thousands: str) -> list[str]:
    codes: set[str] = set()
    for _, row in rows:
        for column in columns:
            cell = read_cell(row.get(column, ""), thousands)
            if cell.kind == Kind.CODE and cell.code:
                codes.add(cell.code)
    return sorted(codes)


def _closest_tool(decision: str, tools: tuple[Tool, ...]) -> str | None:
    best: tuple[float, str] | None = None
    text = decision.lower()
    for tool in tools:
        candidates = [re.sub(r"[._]", " ", tool.tool_id)]
        if tool.description:
            candidates.append(tool.description.lower())
        ratio = max(difflib.SequenceMatcher(None, text, c).ratio() for c in candidates)
        if ratio >= 0.6 and (best is None or ratio > best[0]):
            best = (ratio, tool.tool_id)
    return best[1] if best else None


def confirm_mapping(
    path: Path, previous: Mapping | None, tools: tuple[Tool, ...] | None, ask: Ask
) -> tuple[Matrix, Mapping]:
    """Ask every question the previous mapping does not answer; the matrix and full mapping.

    A previous mapping is reused when its header row still holds the same header.
    """
    if previous is not None:
        matrix, delimiter = read_matrix(path, previous.delimiter)
        header = matrix.row(previous.header_line)
        if header is None or list(header) != previous.header:
            previous = None
    if previous is None:
        matrix, delimiter = read_matrix(path)
        answer = ask("Delimiter (',', ';' or 'tab')", delimiter, _one_of(DELIMITERS))
        if answer != delimiter:
            matrix, delimiter = read_matrix(path, answer)

    level_columns: list[str] = []
    thousands: str
    codes: dict[str, Literal["approves", "none"]] = {}
    if previous is not None:
        header_line = previous.header_line
        layout = Layout(previous.layout)
        decision_column = previous.decision_column
        level_columns = previous.level_columns
        level_column = previous.level_column
        limit_column = previous.limit_column
        thousands = previous.thousands
        currency = previous.currency
        roles = dict(previous.roles)
        codes = dict(previous.codes)
        links = {decision: list(items) for decision, items in previous.links.items()}
    else:
        proposal = propose(matrix)
        lines = [str(line) for line, _ in matrix.rows]
        header_line = int(
            ask("Header row (line number)", str(proposal.header_line), _one_of(lines))
        )
        proposal = propose(matrix, header_line)
        header = matrix.row(header_line) or ()
        keys = column_keys(header)
        layout = Layout(ask("Layout (wide or long)", proposal.layout.value, _one_of(Layout)))
        decision_column = ask("Decision column", proposal.decision_column, _one_of(keys))
        level_column = limit_column = None
        if layout == Layout.WIDE:

            def check_levels(answer: str) -> str | None:
                chosen = _list(answer)
                if not chosen:
                    return "name at least one column"
                unknown = [name for name in chosen if name not in keys or name == decision_column]
                return f"not a level column: {', '.join(unknown)}" if unknown else None

            default = "; ".join(proposal.level_columns)
            level_columns = _list(ask("Level columns, separated by ';'", default, check_levels))
        else:
            level_column = ask("Level column", proposal.level_column or keys[0], _one_of(keys))
            limit_column = ask("Limit column", proposal.limit_column or keys[0], _one_of(keys))
        thousands = ask("Thousands separator (',' or '.')", proposal.thousands, _one_of(",."))
        answer = ask(
            "Currency (ISO 4217 code, or none)",
            proposal.currency or "none",
            lambda value: (
                None
                if value == "none" or _CURRENCY.fullmatch(value)
                else "a three-letter code such as EUR, or none"
            ),
        )
        currency = None if answer == "none" else answer
        roles = {}
        links = {}

    rows = data_rows(matrix, header_line)
    level_names = level_columns if layout == Layout.WIDE else _levels(rows, level_column or "")
    for name in level_names:
        if name not in roles:
            roles[name] = ask(f"Role ID for level {name!r}", role_id(name) or "role", _valid_id)
    amount_columns = level_columns if layout == Layout.WIDE else [limit_column or ""]
    for code in _codes(rows, amount_columns, thousands):
        if code not in codes:
            default = "approves" if code == "A" else "none"
            meaning = ask(
                f"Code {code!r} means (approves or none)", default, _one_of(["approves", "none"])
            )
            codes[code] = "approves" if meaning == "approves" else "none"
    known = {tool.tool_id for tool in tools} if tools is not None else None
    for decision in _decisions(rows, decision_column):
        if decision in links:
            continue

        def check_tools(answer: str) -> str | None:
            if answer == "none":
                return None
            chosen = _list(answer)
            invalid = [tool for tool in chosen if not _ID.fullmatch(tool)]
            if invalid:
                return "not a valid tool ID"
            if known is not None and any(tool not in known for tool in chosen):
                return "not in tools.csv"
            return None

        proposed = _closest_tool(decision, tools) if tools else None
        answer = ask(
            f"Tools for decision {decision!r} (IDs separated by ';', or none)",
            proposed or "none",
            check_tools,
        )
        chosen = [] if answer == "none" else _list(answer)
        links[decision] = [
            Link(
                tool=tool,
                parameter=ask(f"Parameter the limit applies to, for {tool}", "amount", _valid_id),
            )
            for tool in chosen
        ]
    mapping = Mapping.model_validate(
        {
            "doa_mapping_version": MAPPING_VERSION,
            "matrix_sha256": matrix.sha256,
            "delimiter": delimiter,
            "header_line": header_line,
            "header": list(matrix.row(header_line) or ()),
            "layout": layout.value,
            "decision_column": decision_column,
            "level_columns": level_columns,
            "level_column": level_column,
            "limit_column": limit_column,
            "thousands": thousands,
            "currency": currency,
            "roles": roles,
            "codes": codes,
            "links": links,
        }
    )
    return matrix, mapping
