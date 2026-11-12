"""The diagnosis that `diagnosis.json` holds: facts, findings and omissions (SPEC 8.5)."""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictInt, StrictStr

DIAGNOSIS_VERSION = 1

Rule = Literal[
    "approver.none",
    "approver.self",
    "escalation.none",
    "expiry.none",
    "policy.one_person",
    "tier.high_allow",
    "credential.shared",
    "unattended.no_owner",
]
Level = Literal["gap", "needs_validation"]
Basis = Literal["documented", "asserted"]
Tier = Literal["low", "medium", "high"]
Mode = Literal["attended", "unattended", "both"]
Decision = Literal["allow", "ask"]
Sha256 = Annotated[StrictStr, Field(pattern=r"^[0-9a-f]{64}$")]


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


class Input(_Model):
    file: StrictStr
    sha256: Sha256


class Agent(_Model):
    id: StrictStr
    name: StrictStr | None
    owner: StrictStr | None
    owner_team: StrictStr | None
    mode: Mode | None
    tools: list[StrictStr] | None
    # The other agents that use the same credential. The credential's name is never kept.
    shares_credential_with: list[StrictStr]


class Band(_Model):
    up_to: StrictInt | float | None
    ladder: list[StrictStr]


class Approvals(_Model):
    parameter: StrictStr
    currency: StrictStr | None
    bands: list[Band]


def ladder_roles(approvals: "Approvals | None") -> list[str]:
    """The roles of every band, lowest band first, each once."""
    if approvals is None:
        return []
    return list(dict.fromkeys(role for band in approvals.bands for role in band.ladder))


class Tool(_Model):
    id: StrictStr
    system: StrictStr | None
    access: Literal["read", "write"] | None
    description: StrictStr | None
    tier: Tier | None
    owner_team: StrictStr | None
    approval_ttl_minutes: StrictInt | None
    approvals: Approvals | None


class Person(_Model):
    id: StrictStr
    team: StrictStr | None
    roles: list[StrictStr]
    deputy: StrictStr | None


class Binding(_Model):
    agent: StrictStr
    tool: StrictStr
    decision: Decision
    owner_team: StrictStr | None
    tier: Tier | None


class Finding(_Model):
    rule: Rule
    level: Level
    subject: StrictStr
    agents: list[StrictStr]
    tools: list[StrictStr]
    saw: StrictStr
    why: StrictStr
    next: StrictStr
    # The bases of the facts it rests on, strongest first. The last one is the weakest.
    bases: list[Basis]
    # For a finding that is not known: what would settle it.
    needs: list[StrictStr]


class NotDrafted(_Model):
    section: StrictStr
    reason: StrictStr


class Diagnosis(_Model):
    diagnosis_version: StrictInt
    generated_at: StrictStr
    tool: StrictStr
    inputs: list[Input]
    # None for a file that was not given.
    agents: list[Agent] | None
    tools: list[Tool] | None
    people: list[Person] | None
    doa_given: bool
    roles: dict[str, list[StrictStr]]
    bindings: list[Binding]
    findings: list[Finding]
    not_drafted: list[NotDrafted]
    notes: list[StrictStr]
