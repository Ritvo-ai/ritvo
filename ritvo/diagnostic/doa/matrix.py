"""Read a DoA matrix exported as CSV, and propose how to read it (docs/SPEC.md section 8.4)."""

import csv
import hashlib
import io
import re
import statistics
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from pathlib import Path

DELIMITERS = {",": ",", ";": ";", "tab": "\t"}

UNLIMITED_WORDS = {"unlimited", "no limit", "any", "full"}
NO_AUTHORITY_WORDS = {"", "-", "\u2014", "\u2013", "n/a", "none", "0"}  # em and en dash
_CODE = re.compile(r"[A-Za-z]{1,2}")
_SYMBOLS = {"€": "EUR", "$": "USD", "£": "GBP", "¥": "JPY", "₹": "INR"}
_CURRENCY_CODE = re.compile(r"\b[A-Z]{3}\b")
_SUFFIXES = {"k": Decimal(1_000), "m": Decimal(1_000_000), "bn": Decimal(1_000_000_000)}
_NUMBER = re.compile(r"(?P<number>[0-9][0-9.,]*)\s*(?P<suffix>k|m|bn)?", re.IGNORECASE)

_DECISION_NAMES = (
    "decision",
    "action",
    "activity",
    "transaction",
    "category",
    "matter",
    "item",
    "description",
)
_LEVEL_NAMES = ("approver", "approved by", "authority", "level", "role", "signatory", "delegate")
_LIMIT_NAMES = ("limit", "threshold", "up to", "amount", "value", "max")


class DoaError(Exception):
    """The matrix or its mapping cannot be used. Nothing is written."""


class Layout(StrEnum):
    WIDE = "wide"
    LONG = "long"


class Kind(StrEnum):
    LIMIT = "limit"
    UNLIMITED = "unlimited"
    NONE = "none"
    CODE = "code"
    INVALID = "invalid"


@dataclass(frozen=True)
class Cell:
    kind: Kind
    limit: Decimal | None = None
    code: str | None = None
    currency: str | None = None


@dataclass(frozen=True)
class Matrix:
    """The file as rows of cells, each row with its line number."""

    path: Path
    sha256: str
    delimiter: str
    rows: tuple[tuple[int, tuple[str, ...]], ...]

    def row(self, line: int) -> tuple[str, ...] | None:
        for number, cells in self.rows:
            if number == line:
                return cells
        return None


def _parse(text: str, delimiter: str) -> list[tuple[int, tuple[str, ...]]]:
    reader = csv.reader(io.StringIO(text, newline=""), delimiter=delimiter, strict=True)
    rows = []
    while True:
        line = reader.line_num + 1
        try:
            cells = next(reader)
        except StopIteration:
            break
        except csv.Error:
            raise DoaError(f"line {line}: not valid CSV") from None
        rows.append((line, tuple(cell.strip() for cell in cells)))
    return rows


def _width(rows: list[tuple[int, tuple[str, ...]]]) -> float:
    widths = [sum(1 for cell in cells if cell) for _, cells in rows]
    filled = [width for width in widths if width]
    return statistics.median(filled) if filled else 0


def read_matrix(path: Path, delimiter: str | None = None) -> tuple[Matrix, str]:
    """The matrix and the delimiter name. With no delimiter given, the one detected."""
    if not path.is_file():
        raise DoaError(f"{path}: no such file")
    raw = path.read_bytes()
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise DoaError(
            f"{path}: not UTF-8; export the sheet again as CSV UTF-8 (Comma delimited)"
        ) from None
    sha256 = hashlib.sha256(raw).hexdigest()
    if delimiter is None:
        scored = []
        for name, value in DELIMITERS.items():
            try:
                scored.append((_width(_parse(text, value)), name))
            except DoaError:
                continue
        if not scored:
            raise DoaError(f"{path}: not valid CSV")
        # Ties go to the order of DELIMITERS: comma first.
        delimiter = max(scored, key=lambda item: item[0])[1]
    rows = [(line, cells) for line, cells in _parse(text, DELIMITERS[delimiter]) if any(cells)]
    if not rows:
        raise DoaError(f"{path}: the file has no rows")
    return Matrix(path, sha256, DELIMITERS[delimiter], tuple(rows)), delimiter


def column_keys(header: tuple[str, ...]) -> list[str]:
    """A name for each column: its header, or `column <n>` when blank or repeated."""
    keys = []
    for number, name in enumerate(header, start=1):
        keys.append(name if name and header.index(name) == number - 1 else f"column {number}")
    return keys


def read_cell(value: str, thousands: str) -> Cell:
    text = value.strip()
    lowered = text.lower()
    if lowered in UNLIMITED_WORDS:
        return Cell(Kind.UNLIMITED)
    if lowered in NO_AUTHORITY_WORDS:
        return Cell(Kind.NONE)
    if _CODE.fullmatch(text) and lowered not in _SUFFIXES:
        return Cell(Kind.CODE, code=text.upper())
    currency = None
    for symbol, code in _SYMBOLS.items():
        if symbol in text:
            currency = code
            text = text.replace(symbol, "")
    match = _CURRENCY_CODE.search(text)
    if match:
        currency = match.group(0)
        text = text.replace(match.group(0), "")
    number = _NUMBER.fullmatch(text.strip())
    if number is None:
        return Cell(Kind.INVALID)
    decimal = "," if thousands == "." else "."
    digits = number.group("number")
    whole, _, fraction = digits.partition(decimal)
    groups = whole.split(thousands)
    if decimal in fraction or thousands in fraction:
        return Cell(Kind.INVALID)
    if len(groups) > 1 and (
        not 1 <= len(groups[0]) <= 3 or any(len(group) != 3 for group in groups[1:])
    ):
        return Cell(Kind.INVALID)
    try:
        amount = Decimal("".join(groups) + (f".{fraction}" if fraction else ""))
    except InvalidOperation:
        return Cell(Kind.INVALID)
    suffix = number.group("suffix")
    if suffix:
        amount *= _SUFFIXES[suffix.lower()]
    if amount == 0:
        return Cell(Kind.NONE)
    return Cell(Kind.LIMIT, limit=amount, currency=currency)


def _matches(name: str, words: tuple[str, ...]) -> bool:
    lowered = name.lower()
    return any(word in lowered for word in words)


@dataclass(frozen=True)
class Proposal:
    """How the command proposes to read the file. Every field is confirmed by a person."""

    header_line: int
    layout: Layout
    decision_column: str
    level_columns: tuple[str, ...]
    level_column: str | None
    limit_column: str | None
    ignored: tuple[str, ...]
    thousands: str
    currency: str | None
    codes: tuple[str, ...]


def propose_header(matrix: Matrix) -> int:
    widths = [sum(1 for cell in cells if cell) for _, cells in matrix.rows]
    widest = max(widths)
    for (line, _), width in zip(matrix.rows, widths, strict=True):
        if width >= max(2, (widest + 1) // 2):
            return line
    return matrix.rows[0][0]


def data_rows(matrix: Matrix, header_line: int) -> list[tuple[int, dict[str, str]]]:
    header = matrix.row(header_line)
    if header is None:
        raise DoaError(f"line {header_line} is not a row of the file")
    keys = column_keys(header)
    rows = []
    for line, cells in matrix.rows:
        if line > header_line:
            rows.append((line, dict(zip(keys, cells, strict=False))))
    return rows


def _thousands(values: list[str]) -> str:
    comma = sum(1 for value in values if re.search(r"\d,\d{3}(?!\d)", value))
    dot = sum(1 for value in values if re.search(r"\d\.\d{3}(?!\d)", value))
    return "." if dot > comma else ","


def _readable(value: str) -> bool:
    return read_cell(value, ",").kind != Kind.INVALID or read_cell(value, ".").kind != Kind.INVALID


def propose(matrix: Matrix, header_line: int | None = None) -> Proposal:
    line = header_line if header_line is not None else propose_header(matrix)
    header = matrix.row(line)
    if header is None:
        raise DoaError(f"line {line} is not a row of the file")
    keys = column_keys(header)
    rows = [cells for _, cells in data_rows(matrix, line)]

    def values(key: str) -> list[str]:
        return [row[key] for row in rows if row.get(key)]

    level_column = next((key for key in keys if _matches(key, _LEVEL_NAMES)), None)
    limit_column = next(
        (key for key in keys if key != level_column and _matches(key, _LIMIT_NAMES)), None
    )
    layout = Layout.LONG if level_column and limit_column else Layout.WIDE
    decision = next(
        (
            key
            for key in keys
            if key not in (level_column, limit_column) and _matches(key, _DECISION_NAMES)
        ),
        None,
    )
    if decision is None:
        decision = next(
            (key for key in keys if values(key) and not all(_readable(v) for v in values(key))),
            keys[0],
        )
    if layout == Layout.LONG:
        levels: tuple[str, ...] = ()
        used = {decision, level_column, limit_column}
        amounts = values(limit_column) if limit_column else []
    else:
        level_column = limit_column = None
        levels = tuple(
            key
            for key in keys
            if key != decision
            and values(key)
            and sum(1 for value in values(key) if _readable(value)) * 2 >= len(values(key))
        )
        used = {decision, *levels}
        amounts = [value for key in levels for value in values(key)]
    thousands = _thousands(amounts)
    cells = [read_cell(value, thousands) for value in amounts]
    currencies = sorted({cell.currency for cell in cells if cell.currency})
    codes = tuple(sorted({cell.code for cell in cells if cell.code}))
    return Proposal(
        header_line=line,
        layout=layout,
        decision_column=decision,
        level_columns=levels,
        level_column=level_column,
        limit_column=limit_column,
        ignored=tuple(key for key in keys if key not in used),
        thousands=thousands,
        currency=currencies[0] if currencies else None,
        codes=codes,
    )


def role_id(level: str) -> str:
    """Lower case; each run of other characters becomes `_`; edges cleaned. May be empty."""
    value = re.sub(r"[^a-z0-9._-]+", "_", level.lower())
    return re.sub(r"^[^a-z0-9]+", "", value).rstrip("_")[:64]
