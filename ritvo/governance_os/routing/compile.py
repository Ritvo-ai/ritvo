"""Compile a complete ritvo.yaml into a bundle (docs/SPEC.md section 4.6)."""

import math
import re
from datetime import UTC, datetime

from ritvo.adapter_contract.bundle import (
    AskTriggerRule,
    Bundle,
    BundleBinding,
    BundleTool,
    CedarSets,
    FailModes,
)
from ritvo.config.schema import Binding, Config

_PARAMETER = re.compile(r"[a-z_][a-z0-9_]*")
DEFAULT_MAX_AGE_MINUTES = 60


class CompileError(Exception):
    """The file cannot be compiled. Every problem is listed; nothing is published."""

    def __init__(self, problems: list[str]) -> None:
        super().__init__("; ".join(problems))
        self.problems = problems


def _limit_conditions(binding: Binding) -> list[str]:
    """One Cedar condition for each limit: `max_<name>` bounds the parameter `<name>`."""
    conditions = []
    for key, value in binding.limits.items():
        name = key.removeprefix("max_")
        if not key.startswith("max_") or not _PARAMETER.fullmatch(name):
            raise ValueError(f"limit {key!r} is not named max_<parameter>")
        bound = math.floor(value)
        conditions.append(f"context.params has {name} && context.params.{name} <= {bound}")
    return conditions


def _policy(binding_id: str, binding: Binding, clause: str = "") -> str:
    return (
        f'@id("{binding_id}")\n'
        f'permit(principal == Agent::"{binding.agent}", action == Action::"call", '
        f'resource == Tool::"{binding.tool}")'
        f"{clause};\n"
    )


def _entities(config: Config) -> list[dict[str, object]]:
    teams = sorted({agent.owner_team for agent in config.agents if agent.owner_team})
    entities: list[dict[str, object]] = []
    for agent in config.agents:
        parents = [{"type": "Team", "id": agent.owner_team}] if agent.owner_team else []
        entities.append({"uid": {"type": "Agent", "id": agent.id}, "attrs": {}, "parents": parents})
    entities.extend(
        {"uid": {"type": "Team", "id": team}, "attrs": {}, "parents": []} for team in teams
    )
    entities.extend(
        {"uid": {"type": "Tool", "id": tool.id}, "attrs": {"tier": tool.tier}, "parents": []}
        for tool in config.tools
    )
    return entities


def compile_bundle(
    config: Config,
    policy_version: int,
    issued_at: datetime | None = None,
    max_age_minutes: int = DEFAULT_MAX_AGE_MINUTES,
) -> Bundle:
    problems: list[str] = []
    for index, tool in enumerate(config.tools):
        if tool.tier is None:
            problems.append(f"tools.{index}.tier: required to compile a bundle")
    sets: dict[str, list[str]] = {"deny": [], "over_limit": [], "ask": [], "allow": []}
    bindings: list[BundleBinding] = []
    for number, binding in enumerate(config.bindings, start=1):
        binding_id = f"b-{number}"
        try:
            conditions = _limit_conditions(binding)
        except ValueError as exc:
            problems.append(f"bindings.{number - 1}.limits: {exc}")
            continue
        if binding.decision == "deny":
            if conditions:
                problems.append(f"bindings.{number - 1}.limits: a deny binding has no limits")
                continue
            sets["deny"].append(_policy(binding_id, binding))
        else:
            when = f" when {{ {' && '.join(f'({c})' for c in conditions)} }}" if conditions else ""
            sets[binding.decision].append(_policy(binding_id, binding, when))
            if conditions:
                unless = f" unless {{ {' && '.join(f'({c})' for c in conditions)} }}"
                sets["over_limit"].append(_policy(binding_id, binding, unless))
        bindings.append(
            BundleBinding(
                id=binding_id,
                agent=binding.agent,
                tool=binding.tool,
                decision=binding.decision,
                limits=dict(binding.limits),
            )
        )
    if problems:
        raise CompileError(problems)
    when_issued = (issued_at or datetime.now(UTC)).astimezone(UTC)
    return Bundle(
        policy_version=policy_version,
        issued_at=when_issued.strftime("%Y-%m-%dT%H:%M:%SZ"),
        cedar=CedarSets(**{name: "".join(texts) for name, texts in sets.items()}),
        entities=_entities(config),
        tools=[
            BundleTool(id=tool.id, tier=tool.tier, approval_ttl_minutes=tool.approval_ttl_minutes)
            for tool in config.tools
            if tool.tier is not None
        ],
        bindings=bindings,
        ask_trigger_rules=[
            AskTriggerRule(id="tier", enabled=True),
            AskTriggerRule(id="novelty", enabled=False),
        ],
        fail_modes=FailModes(
            low="open" if config.fail_open_low else "closed", medium="closed", high="closed"
        ),
        max_age_minutes=max_age_minutes,
    )
