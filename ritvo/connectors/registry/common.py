"""The normalised record every registry reader returns, and file handling they share."""

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

from ritvo.inspector.inventory import ID_PATTERN


class RegistryError(Exception):
    """An export cannot be read or is not valid. Nothing is written."""


@dataclass(frozen=True)
class RegistryAgent:
    """One agent from an export. None means not known."""

    id: str
    name: str | None
    platform: str
    platform_ref: str
    framework: str | None
    owner: str | None
    owner_team: str | None
    credential: str | None


@dataclass(frozen=True)
class ExportRead:
    """The agents in one file, each with its 1-based position, and any warnings."""

    sha256: str
    agents: tuple[tuple[int, RegistryAgent], ...]
    warnings: tuple[str, ...]


def is_id(value: str) -> bool:
    return ID_PATTERN.fullmatch(value) is not None


def load_json(path: Path, commands: str) -> tuple[object, str]:
    """Return the parsed file and the SHA-256 of its bytes. Errors never echo content."""
    if not path.is_file():
        raise RegistryError(f"{path}: no such file; {commands}")
    raw = path.read_bytes()
    try:
        data = json.loads(raw.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise RegistryError(f"{path}: not a UTF-8 JSON file; {commands}") from None
    return data, hashlib.sha256(raw).hexdigest()


def text(record: dict[str, object], key: str, path: Path, position: int) -> str | None:
    """A string field, or None when absent, null or empty. Never echoes the value."""
    value = record.get(key)
    if value is None or value == "":
        return None
    if not isinstance(value, str):
        raise RegistryError(f"{path}: agent {position}: {key} is not a string")
    return value


def required(record: dict[str, object], key: str, path: Path, position: int) -> str:
    value = text(record, key, path, position)
    if value is None:
        raise RegistryError(f"{path}: agent {position}: {key} is missing")
    return value


def records(value: object, key: str, path: Path, commands: str) -> list[dict[str, object]]:
    if not isinstance(value, list):
        raise RegistryError(f"{path}: {key} is not a list; {commands}")
    result = []
    for position, item in enumerate(value, start=1):
        if not isinstance(item, dict):
            raise RegistryError(f"{path}: agent {position} is not a JSON object")
        result.append(item)
    return result
