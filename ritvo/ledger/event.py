"""A ledger event and entry (docs/SPEC.md section 4.7)."""

from typing import Annotated, Literal

from pydantic import Field, StrictInt, StrictStr

from ritvo.adapter_contract.principal import (
    Boundary,
    Count,
    EventId,
    Model,
    Principal,
    Sha256Ref,
    Timestamp,
)

EventType = Literal[
    "decision", "escalation", "resolution", "grant", "policy_change", "key", "admin"
]
Decision = Literal["allow", "deny", "ask", "approved", "rejected", "expired"]
LedgerMode = Literal["observe", "enforce"]
Flag = Annotated[StrictStr, Field(pattern=r"^[a-z0-9][a-z0-9._-]{0,63}$")]
Hash = Annotated[StrictStr, Field(pattern=r"^[0-9a-f]{64}$")]


class LedgerEvent(Model):
    """What a writer supplies. Every field is stored; `None` where it does not apply."""

    event_id: EventId
    timestamp: Timestamp
    type: EventType
    principal: Principal | None = None
    boundary: Boundary | None = None
    tool: StrictStr | None = None
    action: StrictStr | None = None
    parameter_fingerprint: Sha256Ref | None = None
    policy_version: StrictInt | None = None
    binding_id: StrictStr | None = None
    mode: LedgerMode | None = None
    decision: Decision | None = None
    would_decide: Decision | None = None
    triggering_rule: StrictStr | None = None
    flags: list[Flag] = Field(default_factory=list)
    approver: StrictStr | None = None
    approval_or_grant_ref: StrictStr | None = None
    outcome: StrictStr | None = None
    latency_ms: Count | None = None


class LedgerEntry(LedgerEvent):
    """An event as stored: chained to the one before it."""

    prev_hash: Hash
    hash: Hash
