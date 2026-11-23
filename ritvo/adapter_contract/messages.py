"""The messages of the adapter contract v0.2 (docs/SPEC.md section 6.1)."""

import json
from typing import Annotated, Any, Literal, Self

from pydantic import Field, StrictInt, StrictStr, TypeAdapter, ValidationError, model_validator

from ritvo.adapter_contract.canonical import canonical
from ritvo.adapter_contract.principal import (
    Boundary,
    Count,
    EventId,
    Model,
    Principal,
    Sha256Ref,
    Source,
    Timestamp,
)
from ritvo.ledger.event import LedgerEntry

CONTRACT_VERSION = "0.2"

Parameters = dict[str, Any]
Reason = Annotated[StrictStr, Field(max_length=500)]


class ContractError(Exception):
    """A message cannot be read or is not valid."""


class _Message(Model):
    contract_version: Literal["0.2"]
    event_id: EventId
    timestamp: Timestamp
    source: Source


class AskRequested(_Message):
    type: Literal["ask.requested"]
    request_fingerprint: Sha256Ref
    principal: Principal
    boundary: Boundary
    tool: StrictStr
    action: StrictStr
    parameter_hash: Sha256Ref
    parameters: Parameters | None
    body_ref: StrictStr | None
    policy_version: StrictInt | None
    trace_id: StrictStr | None
    linked_to: Sha256Ref | None


class CallDenied(_Message):
    type: Literal["call.denied"]
    principal: Principal
    boundary: Boundary
    tool: StrictStr
    action: StrictStr
    parameters: Parameters | None
    binding_id: StrictStr | None
    policy_version: StrictInt | None
    reason: Reason


class GrantUsed(_Message):
    type: Literal["grant.used"]
    request_fingerprint: Sha256Ref
    grant_id: StrictStr
    outcome: Literal["accepted", "refused"]


class RequestExecuted(_Message):
    type: Literal["request.executed"]
    request_fingerprint: Sha256Ref
    grant_id: StrictStr | None
    outcome: Literal["success", "error"]
    latency_ms: Count | None


class RequestExpired(_Message):
    type: Literal["request.expired"]
    request_fingerprint: Sha256Ref
    reason: Reason


class InventoryReported(_Message):
    type: Literal["inventory.reported"]
    inventory: dict[str, Any]


class LimitNear(_Message):
    type: Literal["limit.near"]
    principal: Principal
    tool: StrictStr
    limit: StrictStr
    limit_value: int | float
    observed: int | float


class PolicyPublished(_Message):
    type: Literal["policy.published"]
    policy_version: StrictInt
    bundle: dict[str, Any]
    ask_trigger_rules: list[dict[str, Any]]
    author: StrictStr
    approvers: list[StrictStr]
    effective_date: Timestamp


class DecisionResolved(_Message):
    type: Literal["decision.resolved"]
    request_fingerprint: Sha256Ref
    outcome: Literal["approve", "reject", "expire"]
    approver: StrictStr | None
    rationale_ref: StrictStr | None
    grant: StrictStr | None

    @model_validator(mode="after")
    def _grant_only_for_approval(self) -> Self:
        if self.outcome == "approve" and self.grant is None:
            raise ValueError("an approval carries a grant")
        if self.outcome != "approve" and self.grant is not None:
            raise ValueError("only an approval carries a grant")
        return self


class LedgerEntryMessage(_Message):
    type: Literal["ledger.entry"]
    entry: LedgerEntry


Message = Annotated[
    AskRequested
    | CallDenied
    | GrantUsed
    | RequestExecuted
    | RequestExpired
    | InventoryReported
    | LimitNear
    | PolicyPublished
    | DecisionResolved
    | LedgerEntryMessage,
    Field(discriminator="type"),
]

_ADAPTER: TypeAdapter[Message] = TypeAdapter(Message)


def _refuse_constant(name: str) -> Any:
    raise ValueError(f"{name} is not valid JSON")


def encode(message: _Message) -> bytes:
    """The bytes to send: canonical JSON of the message."""
    return canonical(message.model_dump(mode="json"))


def decode(data: bytes) -> Message:
    """Read a message. A message of another contract version is refused, naming this one."""
    try:
        parsed = json.loads(data.decode("utf-8"), parse_constant=_refuse_constant)
    except (UnicodeDecodeError, ValueError):
        raise ContractError("the message is not UTF-8 JSON") from None
    if not isinstance(parsed, dict):
        raise ContractError("the message must be one JSON object")
    version = parsed.get("contract_version")
    if version != CONTRACT_VERSION:
        raise ContractError(f"unsupported contract_version; this reads {CONTRACT_VERSION}")
    try:
        return _ADAPTER.validate_python(parsed)
    except ValidationError as exc:
        problems = [
            f"{'.'.join(str(part) for part in error['loc'])}: {error['msg']}"
            for error in exc.errors(include_url=False, include_input=False, include_context=False)
        ]
        raise ContractError("; ".join(problems)) from None
