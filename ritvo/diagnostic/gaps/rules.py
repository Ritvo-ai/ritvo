"""Build the diagnosis and run the gap rules (docs/SPEC.md section 8.5)."""

from collections.abc import Callable
from dataclasses import dataclass

from ritvo import __version__
from ritvo.diagnostic.doa.draft import DoaDraft
from ritvo.diagnostic.gaps import model
from ritvo.diagnostic.intake.reader import Intake

AGENTS_CSV = "agents.csv"
TOOLS_CSV = "tools.csv"
PEOPLE_CSV = "people.csv"
DOA = "the DoA draft"

# The rules in the order they are reported.
RULES: tuple[model.Rule, ...] = (
    "approver.none",
    "approver.self",
    "escalation.none",
    "expiry.none",
    "policy.one_person",
    "tier.high_allow",
    "credential.shared",
    "unattended.no_owner",
)
TITLES: dict[str, str] = {
    "approver.none": "No named approver",
    "approver.self": "Owner can approve their own agent",
    "escalation.none": "No escalation path",
    "expiry.none": "No approval expiry",
    "policy.one_person": "Policy change with no second person",
    "tier.high_allow": "High tier set to allow",
    "credential.shared": "Shared credential",
    "unattended.no_owner": "Unattended agent with no owner",
}
_WHY: dict[str, str] = {
    "approver.none": "An ask with nobody named to answer it waits until it expires.",
    "approver.self": (
        "An agent's owner must not approve that agent's escalated action above the low tier."
    ),
    "escalation.none": "If the one person who can answer is away, the ask has nowhere to go.",
    "expiry.none": "An approval with no expiry can be used long after the situation has changed.",
    "policy.one_person": "One person can write a policy change and approve it unchecked.",
    "tier.high_allow": (
        "Agents can use this high tier tool today with no ask in front of it. "
        "The draft proposes an ask for the same pair."
    ),
    "credential.shared": (
        "Agents that share a credential are indistinguishable to the services they call."
    ),
    "unattended.no_owner": "Nobody is accountable when an unattended agent acts.",
}
_NEXT: dict[str, str] = {
    "approver.none": "Name who approves this tool, in the DoA and in people.csv.",
    "approver.self": "Put someone other than the agent's owner in the ladder for this tool.",
    "escalation.none": "Name a second approver or a deputy for the roles in the ladder.",
    "expiry.none": "Set approval_ttl_minutes for the tool in tools.csv.",
    "policy.one_person": "Name a different person for policy_author and escalation_reviewer.",
    "tier.high_allow": "Confirm the tier, and decide who approves this tool.",
    "credential.shared": "Give each agent its own credential.",
    "unattended.no_owner": "Name an owner for the agent, or stop running it unattended.",
}
_BASES: dict[str, list[model.Basis]] = {
    "approver.none": ["documented", "asserted"],
    "approver.self": ["documented", "asserted"],
    "escalation.none": ["documented", "asserted"],
}
_READS: dict[str, tuple[str, ...]] = {
    "approver.none": (AGENTS_CSV, TOOLS_CSV, PEOPLE_CSV, DOA),
    "approver.self": (AGENTS_CSV, TOOLS_CSV, PEOPLE_CSV, DOA),
    "escalation.none": (AGENTS_CSV, TOOLS_CSV, PEOPLE_CSV, DOA),
    "expiry.none": (AGENTS_CSV, TOOLS_CSV),
    "policy.one_person": (PEOPLE_CSV,),
    "tier.high_allow": (AGENTS_CSV, TOOLS_CSV),
    "credential.shared": (AGENTS_CSV,),
    "unattended.no_owner": (AGENTS_CSV,),
}
# What the draft proposes for each tier. A tier that is not known is never read as allow.
_DECISION: dict[str | None, model.Decision] = {
    "low": "allow",
    "medium": "ask",
    "high": "ask",
    None: "ask",
}
_NOT_DRAFTED = (
    ("enforcement", "intake files cannot say where Ritvo would run"),
    ("identity", "intake files cannot say which identity provider issues tokens"),
    ("secrets", "intake files cannot say where secrets are kept"),
    ("policy_sections", "intake files cannot say which teams sign off which part of the policy"),
    ("ladders", "intake files cannot say the escalation order by tier"),
    ("tools[].match", "intake files cannot say how a request maps to a tool"),
    ("cool_off_minutes", "intake files cannot say how long a cool-off should be"),
)


@dataclass(frozen=True)
class Inputs:
    intake: Intake
    doa: DoaDraft | None
    files: tuple[model.Input, ...]


def _approvals(doa: DoaDraft | None) -> dict[str, model.Approvals]:
    if doa is None:
        return {}
    return {
        tool.id: model.Approvals(
            parameter=tool.approvals.parameter,
            currency=tool.approvals.currency,
            bands=[
                model.Band(up_to=band.up_to, ladder=list(band.ladder))
                for band in tool.approvals.bands
            ],
        )
        for tool in doa.tools
    }


def _plural(count: int, word: str) -> str:
    return f"{count} {word}" if count == 1 else f"{count} {word}s"


class _Facts:
    def __init__(self, inputs: Inputs) -> None:
        intake = inputs.intake
        approvals = _approvals(inputs.doa)
        self.given = {
            AGENTS_CSV: intake.agents is not None,
            TOOLS_CSV: intake.tools is not None,
            PEOPLE_CSV: intake.people is not None,
            DOA: inputs.doa is not None,
        }
        credentials: dict[str, list[str]] = {}
        for entry in intake.agents or ():
            if entry.credential is not None:
                credentials.setdefault(entry.credential, []).append(entry.agent_id)
        self.credential_groups = [sorted(ids) for ids in credentials.values() if len(ids) > 1]
        self.agents = (
            None
            if intake.agents is None
            else [
                model.Agent(
                    id=agent.agent_id,
                    name=agent.name,
                    owner=agent.owner,
                    owner_team=agent.owner_team,
                    mode=agent.mode.value if agent.mode else None,
                    tools=list(agent.tools) if agent.tools is not None else None,
                    shares_credential_with=sorted(
                        other
                        for group in self.credential_groups
                        if agent.agent_id in group
                        for other in group
                        if other != agent.agent_id
                    ),
                )
                for agent in intake.agents
            ]
        )
        self.tools = (
            None
            if intake.tools is None
            else [
                model.Tool(
                    id=tool.tool_id,
                    system=tool.system,
                    access=tool.access.value if tool.access else None,
                    description=tool.description,
                    tier=tool.tier.value if tool.tier else None,
                    owner_team=tool.owner_team,
                    approval_ttl_minutes=tool.approval_ttl_minutes,
                    approvals=approvals.get(tool.tool_id),
                )
                for tool in intake.tools
            ]
        )
        self.people = (
            None
            if intake.people is None
            else [
                model.Person(
                    id=person.person_id,
                    team=person.team,
                    roles=list(person.roles or ()),
                    deputy=person.deputy,
                )
                for person in intake.people
            ]
        )
        self.tool_by_id = {tool.id: tool for tool in self.tools or ()}
        self.person_by_id = {person.id: person for person in self.people or ()}
        holders: dict[str, set[str]] = {}
        for person in self.people or ():
            for role in person.roles:
                holders.setdefault(role, set()).add(person.id)
        self.holders = {role: sorted(ids) for role, ids in sorted(holders.items())}
        self.uses: dict[str, list[str]] = {}
        for agent in self.agents or ():
            for tool_id in agent.tools or ():
                self.uses.setdefault(tool_id, []).append(agent.id)
        self.doa_roles = [role.id for role in inputs.doa.roles] if inputs.doa is not None else []
        self._rated: list[model.Tool] | None = None

    def ladder_holders(self, tool: model.Tool) -> list[str]:
        ids: set[str] = set()
        for role in model.ladder_roles(tool.approvals):
            ids.update(self.holders.get(role, ()))
        return sorted(ids)

    def rated(self) -> list[model.Tool]:
        """Tools at the medium or high tier that some agent can use."""
        if self._rated is None:
            self._rated = [
                tool
                for tool in self.tools or ()
                if tool.tier in ("medium", "high") and self.uses.get(tool.id)
            ]
        return self._rated


def _finding(
    rule: model.Rule,
    level: model.Level,
    subject: str,
    saw: str,
    *,
    agents: list[str] | None = None,
    tools: list[str] | None = None,
    needs: list[str] | None = None,
    bases: list[model.Basis] | None = None,
) -> model.Finding:
    return model.Finding(
        rule=rule,
        level=level,
        subject=subject,
        agents=sorted(agents or []),
        tools=sorted(tools or []),
        saw=saw,
        why=_WHY[rule],
        next=_NEXT[rule],
        bases=_BASES.get(rule, ["asserted"]) if bases is None else bases,
        needs=needs or [],
    )


def _approver_none(facts: _Facts) -> list[model.Finding]:
    found = []
    for tool in facts.rated():
        users = facts.uses[tool.id]
        if tool.approvals is None:
            saw = "The DoA draft does not link this tool to a decision."
        elif any(not band.ladder for band in tool.approvals.bands):
            saw = "A band of this tool's DoA ladder has no approver, so some requests have none."
        elif not facts.ladder_holders(tool):
            saw = "No role in this tool's ladder has a holder in people.csv."
        else:
            continue
        found.append(_finding("approver.none", "gap", tool.id, saw, agents=users, tools=[tool.id]))
    return found


def _approver_self(facts: _Facts) -> list[model.Finding]:
    found = []
    for agent in facts.agents or ():
        mine = [tool for tool in facts.rated() if agent.id in facts.uses[tool.id]]
        if not mine:
            continue
        if agent.owner is None:
            found.append(
                _finding(
                    "approver.self",
                    "needs_validation",
                    agent.id,
                    "The agent has no owner in agents.csv, so this cannot be checked.",
                    agents=[agent.id],
                    tools=[tool.id for tool in mine],
                    needs=["owner in agents.csv"],
                )
            )
            continue
        hit = [tool.id for tool in mine if agent.owner in _answerers(facts, tool)]
        if hit:
            found.append(
                _finding(
                    "approver.self",
                    "gap",
                    agent.id,
                    f"The owner, {agent.owner}, holds a role in the ladder, or is the deputy of "
                    f"someone who does, for {_plural(len(hit), 'tool')} the agent can use.",
                    agents=[agent.id],
                    tools=hit,
                )
            )
    return found


def _answerers(facts: _Facts, tool: model.Tool) -> set[str]:
    holders = facts.ladder_holders(tool)
    people = set(holders)
    for holder in holders:
        deputy = facts.person_by_id[holder].deputy
        if deputy is not None:
            people.add(deputy)
    return people


def _escalation_none(facts: _Facts) -> list[model.Finding]:
    found = []
    for tool in facts.rated():
        if not facts.ladder_holders(tool):
            continue  # approver.none reports a tool with nobody to ask
        people = _answerers(facts, tool)
        if len(people) < 2:
            found.append(
                _finding(
                    "escalation.none",
                    "gap",
                    tool.id,
                    "Only one person, counting deputies, could answer an ask for this tool.",
                    agents=facts.uses[tool.id],
                    tools=[tool.id],
                )
            )
    return found


def _expiry_none(facts: _Facts) -> list[model.Finding]:
    return [
        _finding(
            "expiry.none",
            "needs_validation",
            tool.id,
            "approval_ttl_minutes is empty for this tool, so it is not known whether an "
            "approval expires.",
            agents=facts.uses[tool.id],
            tools=[tool.id],
            needs=["approval_ttl_minutes in tools.csv"],
        )
        for tool in facts.rated()
        if tool.approval_ttl_minutes is None
    ]


def _policy_one_person(facts: _Facts) -> list[model.Finding]:
    author = facts.holders.get("policy_author", [])
    reviewer = facts.holders.get("escalation_reviewer", [])
    found = []
    for role, holders in (("policy_author", author), ("escalation_reviewer", reviewer)):
        if not holders:
            found.append(
                _finding("policy.one_person", "gap", role, f"Nobody holds {role} in people.csv.")
            )
    if author and author == reviewer and len(author) == 1:
        found.append(
            _finding(
                "policy.one_person",
                "gap",
                "policy_author",
                f"{author[0]} is the only holder of both policy_author and escalation_reviewer.",
            )
        )
    return found


def _tier_high_allow(facts: _Facts) -> list[model.Finding]:
    found = []
    for tool in facts.tools or ():
        users = facts.uses.get(tool.id)
        if not users:
            continue
        if tool.tier == "high":
            found.append(
                _finding(
                    "tier.high_allow",
                    "gap",
                    tool.id,
                    f"{_plural(len(users), 'agent')} can use this high tier tool today "
                    "with no ask in front of it.",
                    agents=users,
                    tools=[tool.id],
                )
            )
        elif tool.tier is None:
            found.append(
                _finding(
                    "tier.high_allow",
                    "needs_validation",
                    tool.id,
                    "The tier is not known, so it is not known whether this tool needs an ask.",
                    agents=users,
                    tools=[tool.id],
                    needs=["tier in tools.csv"],
                )
            )
    return found


def _credential_shared(facts: _Facts) -> list[model.Finding]:
    return [
        _finding(
            "credential.shared",
            "gap",
            "+".join(group),
            f"{_plural(len(group), 'agent')} use the same credential. Its name is not shown.",
            agents=group,
        )
        for group in sorted(facts.credential_groups)
    ]


def _unattended_no_owner(facts: _Facts) -> list[model.Finding]:
    found = []
    for agent in facts.agents or ():
        if agent.owner is not None:
            continue
        if agent.mode in ("unattended", "both"):
            found.append(
                _finding(
                    "unattended.no_owner",
                    "gap",
                    agent.id,
                    f"The agent runs {agent.mode} and has no owner.",
                    agents=[agent.id],
                )
            )
        elif agent.mode is None:
            found.append(
                _finding(
                    "unattended.no_owner",
                    "needs_validation",
                    agent.id,
                    "The agent has no owner and its mode is not known.",
                    agents=[agent.id],
                    needs=["mode in agents.csv"],
                )
            )
    return found


_CHECKS: dict[str, Callable[[_Facts], list[model.Finding]]] = {
    "approver.none": _approver_none,
    "approver.self": _approver_self,
    "escalation.none": _escalation_none,
    "expiry.none": _expiry_none,
    "policy.one_person": _policy_one_person,
    "tier.high_allow": _tier_high_allow,
    "credential.shared": _credential_shared,
    "unattended.no_owner": _unattended_no_owner,
}


def _findings(facts: _Facts) -> list[model.Finding]:
    found: list[model.Finding] = []
    for rule in RULES:
        missing = [name for name in _READS[rule] if not facts.given[name]]
        if missing:
            found.append(
                _finding(
                    rule,
                    "needs_validation",
                    "*",
                    f"Not evaluated: this rule reads {', '.join(_READS[rule])}, and "
                    f"{', '.join(missing)} {'was' if len(missing) == 1 else 'were'} not given.",
                    needs=[f"give {name}" for name in missing],
                    bases=[],
                )
            )
            continue
        results = _CHECKS[rule](facts)
        found += sorted(results, key=lambda f: (f.level != "gap", f.subject))
    return found


def _bindings(facts: _Facts) -> list[model.Binding]:
    return [
        model.Binding(
            agent=agent.id,
            tool=tool_id,
            decision=_DECISION[tool.tier if tool else None],
            owner_team=tool.owner_team if tool else None,
            tier=tool.tier if tool else None,
        )
        for agent in facts.agents or ()
        for tool_id in agent.tools or ()
        for tool in [facts.tool_by_id.get(tool_id)]
    ]


def _roles(facts: _Facts) -> dict[str, list[str]]:
    names = sorted({*facts.holders, *facts.doa_roles})
    return {role: facts.holders.get(role, []) for role in names}


def _not_drafted(facts: _Facts) -> list[model.NotDrafted]:
    omitted = [model.NotDrafted(section=name, reason=reason) for name, reason in _NOT_DRAFTED]
    for name, section in (
        (AGENTS_CSV, "agents and bindings"),
        (TOOLS_CSV, "tools"),
    ):
        if not facts.given[name]:
            omitted.append(model.NotDrafted(section=section, reason=f"{name} was not given"))
    if not facts.given[PEOPLE_CSV] and not facts.given[DOA]:
        omitted.append(
            model.NotDrafted(section="roles", reason="neither people.csv nor a DoA draft was given")
        )
    if not facts.given[DOA]:
        omitted.append(
            model.NotDrafted(section="tools[].approvals", reason="no DoA draft was given")
        )
    return omitted


def _notes(facts: _Facts, doa: DoaDraft | None) -> list[str]:
    if doa is None or facts.tools is None:
        return []
    known = set(facts.tool_by_id)
    return [
        f"The DoA draft links {tool.id}, which is not in tools.csv; it is left out."
        for tool in doa.tools
        if tool.id not in known
    ]


def diagnose(inputs: Inputs, generated_at: str) -> model.Diagnosis:
    facts = _Facts(inputs)
    return model.Diagnosis(
        diagnosis_version=model.DIAGNOSIS_VERSION,
        generated_at=generated_at,
        tool=f"ritvo-diagnose {__version__}",
        inputs=list(inputs.files),
        agents=facts.agents,
        tools=facts.tools,
        people=facts.people,
        doa_given=inputs.doa is not None,
        roles=_roles(facts),
        bindings=_bindings(facts),
        findings=_findings(facts),
        not_drafted=_not_drafted(facts),
        notes=_notes(facts, inputs.doa),
    )
