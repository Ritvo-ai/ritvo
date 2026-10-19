"""The owner overlay: owners from an `agents.csv` file (docs/SPEC.md section 7.4)."""

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from ritvo import __version__
from ritvo.diagnostic.intake.columns import AGENTS, INTAKE_FILES
from ritvo.diagnostic.intake.reader import Agent as IntakeAgent
from ritvo.diagnostic.intake.reader import IntakeError, load_intake
from ritvo.inspector.inventory import Agent, Inventory, OwnerOverlay
from ritvo.inspector.registry import no_owner_findings

FIELDS: tuple[Literal["owner", "owner_team"], ...] = ("owner", "owner_team")


class OverlayError(Exception):
    """The overlay file cannot be used. Nothing is written."""


@dataclass(frozen=True)
class OverlayFile:
    path: Path
    rows: tuple[IntakeAgent, ...]
    files_read: tuple[Path, ...]


@dataclass(frozen=True)
class OverlayRun:
    inventory: Inventory
    fields_set: int
    replaced: int
    warnings: tuple[str, ...]


def read_overlay(path: Path) -> OverlayFile:
    """Read the file with the intake reader of section 8.2, with any intake files beside it."""
    if path.name != AGENTS.name:
        raise OverlayError(f"{path}: the owner overlay file must be named {AGENTS.name}")
    if not path.is_file():
        raise OverlayError(f"{path}: no such file")
    try:
        intake = load_intake(path.parent)
    except IntakeError as exc:
        raise OverlayError(str(exc)) from None
    files_read = tuple(
        path.parent / file.name for file in INTAKE_FILES if (path.parent / file.name).is_file()
    )
    return OverlayFile(path=path, rows=intake.agents or (), files_read=files_read)


def apply_overlay(
    inventory: Inventory, overlay: OverlayFile, now: datetime | None = None
) -> OverlayRun:
    agents = {agent.id: agent for agent in inventory.agents}
    entries: list[OwnerOverlay] = []
    warnings: list[str] = []
    replaced = 0
    for row in overlay.rows:
        agent = agents.get(row.agent_id)
        if agent is None:
            warnings.append(
                f"{overlay.path}: line {row.line}: agent_id {row.agent_id!r} matches no agent "
                "in the inventory; the row was not used"
            )
            continue
        update: dict[str, str] = {}
        for field in FIELDS:
            value = getattr(row, field)
            previous: str | None = getattr(agent, field)
            # An empty cell never changes a field, and an equal value is not a change.
            if value is None or value == previous:
                continue
            update[field] = value
            entries.append(
                OwnerOverlay(agent=agent.id, field=field, previous=previous, source_row=row.line)
            )
            if previous is not None:
                replaced += 1
        if update:
            agents[agent.id] = Agent.model_validate(agent.model_dump() | update)

    merged = list(agents.values())
    manifest = inventory.manifest
    result = Inventory.model_validate(
        inventory.model_dump()
        | {
            "generated_at": (now or datetime.now(UTC))
            .astimezone(UTC)
            .strftime("%Y-%m-%dT%H:%M:%SZ"),
            "tool": f"ritvo-inspect {__version__}",
            "manifest": manifest.model_dump()
            | {
                "paths": [*manifest.paths, *(str(path) for path in overlay.files_read)],
                "files_read": manifest.files_read + len(overlay.files_read),
                "owner_overlays": [
                    *(entry.model_dump() for entry in manifest.owner_overlays),
                    *(entry.model_dump() for entry in entries),
                ],
            },
            "agents": [agent.model_dump() for agent in merged],
            # agent.no_owner is worked out again; other findings are kept as they were.
            "findings": [
                *(finding.model_dump() for finding in no_owner_findings(merged)),
                *(
                    finding.model_dump()
                    for finding in inventory.findings
                    if finding.rule != "agent.no_owner"
                ),
            ],
        }
    )
    return OverlayRun(
        inventory=result, fields_set=len(entries), replaced=replaced, warnings=tuple(warnings)
    )
