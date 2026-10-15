"""The intake files, their columns and fixed values (docs/SPEC.md section 8.2)."""

from dataclasses import dataclass
from enum import StrEnum


class Mode(StrEnum):
    ATTENDED = "attended"
    UNATTENDED = "unattended"
    BOTH = "both"


class Access(StrEnum):
    READ = "read"
    WRITE = "write"


class Tier(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


# Means the same as an empty cell in a column with fixed values.
UNKNOWN = "unknown"


@dataclass(frozen=True)
class Column:
    name: str
    required: bool = False


@dataclass(frozen=True)
class IntakeFile:
    name: str
    columns: tuple[Column, ...]

    @property
    def column_names(self) -> tuple[str, ...]:
        return tuple(column.name for column in self.columns)


AGENTS = IntakeFile(
    "agents.csv",
    (
        Column("agent_id", required=True),
        Column("name"),
        Column("platform"),
        Column("built_by_team"),
        Column("owner"),
        Column("owner_team"),
        Column("mode"),
        Column("credential"),
        Column("tools"),
    ),
)

TOOLS = IntakeFile(
    "tools.csv",
    (
        Column("tool_id", required=True),
        Column("system"),
        Column("access"),
        Column("description"),
        Column("tier"),
        Column("owner_team"),
        Column("approval_ttl_minutes"),
    ),
)

PEOPLE = IntakeFile(
    "people.csv",
    (
        Column("person_id", required=True),
        Column("team"),
        Column("roles"),
        Column("deputy"),
    ),
)

INTAKE_FILES = (AGENTS, TOOLS, PEOPLE)
