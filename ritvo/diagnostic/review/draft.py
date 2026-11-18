"""Draft an answers file from an inventory (docs/SPEC.md section 8.3)."""

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path

from ritvo.diagnostic.review.answers_file import render_answers
from ritvo.diagnostic.review.scoring import (
    OLD_REVIEW_VERSION,
    Answers,
    Basis,
    ReviewError,
    load_answers,
)
from ritvo.inspector.inventory import Inventory, load_inventory

UNKNOWN = "unknown"
_DIGEST = re.compile(r"question sha256:([0-9a-f]{64})$")


@dataclass(frozen=True)
class DraftedAnswer:
    question: str
    answer: str
    note: str
    digest: str


@dataclass(frozen=True)
class DraftLine:
    """One of the three questions as it is in the draft file."""

    question: str
    answer: str
    status: str  # new, carried or changed
    previous: str | None


@dataclass(frozen=True)
class Draft:
    text: str
    lines: tuple[DraftLine, ...]
    other_carried: int


def _digest(value: object) -> str:
    text = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


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

    agent_ids = sorted(agent.id for agent in agents)
    return (
        DraftedAnswer("agents.inventory", inventory_answer, inventory_note, _digest(agent_ids)),
        DraftedAnswer(
            "agents.owner",
            owner_answer,
            owner_note,
            _digest(sorted([agent.id, agent.owner] for agent in agents)),
        ),
        DraftedAnswer(
            "agents.reach",
            reach_answer,
            reach_note,
            _digest(
                {
                    "agents": agent_ids,
                    "tools": sorted(
                        [tool.id, tool.proposed_tier, sorted(tool.reached_by)]
                        for tool in inventory.tools
                    ),
                }
            ),
        ),
    )


def _previous_digest(basis: Basis) -> str | None:
    match = _DIGEST.search(basis.evidence or "")
    return match.group(1) if match else None


def draft_from_file(inventory_path: Path, previous_path: Path | None = None) -> Draft:
    """Read an inventory, and a previous answers file if given, and draft the answers file."""
    drafted = draft_answers(load_inventory(inventory_path))
    previous: Answers | None = None
    if previous_path is not None:
        previous = load_answers(previous_path, allow_draft=True)
        if previous.version == OLD_REVIEW_VERSION:
            raise ReviewError(
                f"{previous_path.name} is review_version {OLD_REVIEW_VERSION}; "
                "run ritvo review on it and write review_version 1 first"
            )
    file_digest = hashlib.sha256(inventory_path.read_bytes()).hexdigest()

    answers: dict[str, str] = dict(previous.answers) if previous else {}
    basis: dict[str, Basis] = dict(previous.basis) if previous else {}
    notes: dict[str, str] = dict(previous.notes) if previous else {}
    lines = []
    for item in drafted:
        before = answers.get(item.question)
        before_basis = basis.get(item.question)
        if before is not None:
            settled = (
                before_basis is None
                or before_basis.source != "derived"
                or _previous_digest(before_basis) == item.digest
            )
            if settled:
                lines.append(DraftLine(item.question, before, "carried", None))
                continue
        evidence = f"{inventory_path.name} sha256:{file_digest} question sha256:{item.digest}"
        answers[item.question] = item.answer
        basis[item.question] = Basis(source="derived", evidence=evidence)
        notes[item.question] = item.note
        lines.append(
            DraftLine(item.question, item.answer, "new" if before is None else "changed", before)
        )
    asked = {item.question for item in drafted}
    other = sum(1 for qid in (previous.answers if previous else {}) if qid not in asked)
    text = render_answers(
        previous.organisation if previous else None, answers, basis, notes, draft=True
    )
    return Draft(text=text, lines=tuple(lines), other_carried=other)
