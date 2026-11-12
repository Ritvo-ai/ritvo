"""Draft an answers file from an inventory (docs/SPEC.md section 8.3)."""

import hashlib
from dataclasses import dataclass
from pathlib import Path

import yaml

from ritvo.inspector.inventory import Inventory, load_inventory

UNKNOWN = "unknown"


@dataclass(frozen=True)
class DraftedAnswer:
    question: str
    answer: str
    note: str


def draft_answers(inventory: Inventory) -> tuple[DraftedAnswer, ...]:
    """The three questions an inventory can answer. Every other question is left to a person."""
    agents = inventory.agents
    total = len(agents)

    inventory_answer = UNKNOWN if total == 0 else "partial"
    inventory_note = (
        "No agents found; this does not show there are none."
        if total == 0
        else f"{total} agents found; whether any were missed is not known."
    )

    owned = sum(1 for agent in agents if agent.owner is not None)
    owner_answer = "all" if total and owned == total else UNKNOWN
    owner_note = (
        "No agents found." if total == 0 else f"{owned} of {total} agents have an owner recorded."
    )

    reached = {agent_id for tool in inventory.tools for agent_id in tool.reached_by}
    with_reach = sum(1 for agent in agents if agent.id in reached)
    if total and with_reach == total:
        reach_answer = "all"
    elif with_reach:
        reach_answer = "some"
    else:
        reach_answer = UNKNOWN
    high = sum(1 for tool in inventory.tools if tool.proposed_tier == "high")
    reach_note = (
        "No agents found."
        if total == 0
        else f"{with_reach} of {total} agents reach a tool found by the scan; "
        f"{high} tools have a proposed high tier."
    )

    return (
        DraftedAnswer("agents.inventory", inventory_answer, inventory_note),
        DraftedAnswer("agents.owner", owner_answer, owner_note),
        DraftedAnswer("agents.reach", reach_answer, reach_note),
    )


def render_draft(inventory_path: Path, drafted: tuple[DraftedAnswer, ...]) -> str:
    digest = hashlib.sha256(inventory_path.read_bytes()).hexdigest()
    evidence = f"{inventory_path.name} sha256:{digest}"
    data = {
        "review_version": 1,
        "draft": True,
        "answers": {item.question: item.answer for item in drafted},
        "basis": {item.question: {"source": "derived", "evidence": evidence} for item in drafted},
        "notes": {item.question: item.note for item in drafted},
    }
    return yaml.safe_dump(data, sort_keys=False, allow_unicode=True)


def draft_from_file(inventory_path: Path) -> tuple[tuple[DraftedAnswer, ...], str]:
    """Read an inventory and return the drafted answers and the text of the draft file."""
    drafted = draft_answers(load_inventory(inventory_path))
    return drafted, render_draft(inventory_path, drafted)
