"""From a confirmed mapping to records, bands and the draft file (docs/SPEC.md section 8.4)."""

from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

import yaml

from ritvo.diagnostic.doa.mapping import Mapping
from ritvo.diagnostic.doa.matrix import Cell, Kind, Matrix, data_rows, read_cell
from ritvo.diagnostic.intake.reader import Person

DRAFT_VERSION = 1


@dataclass(frozen=True)
class Authority:
    """One role's authority for one decision. A limit of None means unlimited."""

    decision: str
    role: str
    limit: Decimal | None
    order: int


@dataclass(frozen=True)
class Band:
    up_to: Decimal | None
    ladder: tuple[str, ...]


@dataclass(frozen=True)
class Draft:
    data: dict[str, object]
    decisions: int
    roles: int
    tools: int
    problems: tuple[str, ...]


def draft_path(matrix: Path) -> Path:
    return matrix.with_name(f"{matrix.stem}.draft.yaml")


def _grant(cell: Cell, mapping: Mapping) -> tuple[bool, Decimal | None]:
    """Whether the cell grants authority, and its limit (None for unlimited)."""
    if cell.kind == Kind.LIMIT:
        return True, cell.limit
    if cell.kind == Kind.UNLIMITED:
        return True, None
    if cell.kind == Kind.CODE and cell.code is not None:
        return mapping.codes.get(cell.code) == "approves", None
    return False, None


def read_authorities(
    matrix: Matrix, mapping: Mapping
) -> tuple[list[Authority], list[str], list[str]]:
    """The records of every layout, the decisions in file order, and the problems."""
    problems: list[str] = []
    found: dict[tuple[str, str], Authority] = {}
    decisions: list[str] = []
    currencies: dict[str, int] = {}
    current: str | None = None
    order = 0
    for line, row in data_rows(matrix, mapping.header_line):
        name = row.get(mapping.decision_column, "")
        if name:
            current = name
        if current is None:
            problems.append(f"line {line}: no decision above this row; the row was skipped")
            continue
        if current not in decisions:
            decisions.append(current)
        if mapping.layout == "wide":
            cells = [
                (column, mapping.roles[column], row.get(column, ""))
                for column in mapping.level_columns
            ]
        else:
            level = row.get(mapping.level_column or "", "")
            if not level:
                continue
            cells = [
                (
                    mapping.limit_column or "",
                    mapping.roles[level],
                    row.get(mapping.limit_column or "", ""),
                )
            ]
        for column, role, value in cells:
            cell = read_cell(value, mapping.thousands)
            if cell.kind == Kind.INVALID:
                problems.append(f"line {line}, column {column}: not a limit; read as no authority")
                continue
            if cell.currency:
                currencies.setdefault(cell.currency, line)
            grants, limit = _grant(cell, mapping)
            if not grants:
                continue
            order += 1
            key = (current, role)
            earlier = found.get(key)
            if earlier is not None and earlier.limit != limit:
                problems.append(
                    f'line {line}: decision "{current}" gives {role} two different limits; '
                    "the smaller is used"
                )
                if earlier.limit is None or (limit is not None and limit < earlier.limit):
                    found[key] = Authority(current, role, limit, earlier.order)
                continue
            if earlier is None:
                found[key] = Authority(current, role, limit, order)
    expected = {mapping.currency} if mapping.currency else set()
    if set(currencies) - expected or len(currencies) > 1:
        problems.append(
            "the matrix mixes currencies ("
            + ", ".join(sorted(currencies))
            + f"); the draft uses {mapping.currency or 'none'}"
        )
    return list(found.values()), decisions, problems


def bands_for(authorities: list[Authority]) -> list[Band]:
    limited = sorted(
        (a for a in authorities if a.limit is not None), key=lambda a: (a.limit, a.order)
    )
    unlimited = [a.role for a in sorted(authorities, key=lambda a: a.order) if a.limit is None]
    bands = []
    for bound in sorted({a.limit for a in limited if a.limit is not None}):
        ladder = [a.role for a in limited if a.limit is not None and a.limit >= bound]
        bands.append(Band(bound, tuple(ladder + unlimited)))
    bands.append(Band(None, tuple(unlimited)))
    return bands


def _number(value: Decimal) -> int | float:
    return int(value) if value == value.to_integral_value() else float(value)


def _bands_data(bands: list[Band]) -> list[dict[str, object]]:
    data: list[dict[str, object]] = []
    for band in bands:
        entry: dict[str, object] = {}
        if band.up_to is not None:
            entry["up_to"] = _number(band.up_to)
        entry["ladder"] = list(band.ladder)
        data.append(entry)
    return data


def build_draft(matrix: Matrix, mapping: Mapping, people: tuple[Person, ...] | None) -> Draft:
    authorities, decisions, problems = read_authorities(matrix, mapping)
    bands: dict[str, list[Band]] = {}
    for decision in decisions:
        mine = [a for a in authorities if a.decision == decision]
        if not mine:
            problems.append(f'decision "{decision}": no role holds authority; no bands')
            continue
        bands[decision] = bands_for(mine)
        top = bands[decision][-1]
        if not top.ladder:
            bound = bands[decision][-2].up_to if len(bands[decision]) > 1 else None
            limit = _number(bound) if bound is not None else 0
            problems.append(f'decision "{decision}": no approver above {limit}')

    by_tool: dict[str, list[str]] = {}
    for decision in decisions:
        for link in mapping.links.get(decision, []):
            by_tool.setdefault(link.tool, []).append(decision)
    tools: list[dict[str, object]] = []
    for tool, linked in by_tool.items():
        if len(linked) > 1:
            problems.append(
                f"{tool} is linked to {len(linked)} decisions ("
                + ", ".join(f'"{d}"' for d in linked)
                + "); it gets no bands until one link is removed from the mapping file"
            )
            continue
        decision = linked[0]
        if decision not in bands:
            continue
        link = next(link for link in mapping.links[decision] if link.tool == tool)
        tools.append(
            {
                "id": tool,
                "decision": decision,
                "approvals": {
                    "parameter": link.parameter,
                    "currency": mapping.currency,
                    "bands": _bands_data(bands[decision]),
                },
            }
        )
    unlinked = [
        {"decision": decision, "bands": _bands_data(bands.get(decision, []))}
        for decision in decisions
        if not mapping.links.get(decision)
    ]

    role_ids = list(dict.fromkeys(mapping.roles.values()))
    holders: dict[str, list[str]] = {role: [] for role in role_ids}
    if people is not None:
        for person in people:
            for role in person.roles or ():
                if role in holders:
                    holders[role].append(person.person_id)
        problems += [
            f"role {role} is held by nobody in people.csv" for role in role_ids if not holders[role]
        ]

    data: dict[str, object] = {
        "doa_draft_version": DRAFT_VERSION,
        "source": {"file": matrix.path.name, "sha256": matrix.sha256},
        "roles": [{"id": role, "holders": holders[role]} for role in role_ids],
        "tools": tools,
        "unlinked": unlinked,
        "problems": problems,
    }
    return Draft(data, len(decisions), len(role_ids), len(tools), tuple(problems))


def write_draft(draft: Draft, path: Path) -> None:
    text = yaml.safe_dump(draft.data, sort_keys=False, allow_unicode=True)
    path.write_text(text, encoding="utf-8", newline="\n")
