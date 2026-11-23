"""The contract messages an enforcement point sends (docs/SPEC.md section 6.1)."""

from typing import Any

from ritvo.adapter_contract.fingerprint import parameter_hash
from ritvo.adapter_contract.messages import AskRequested, CallDenied
from ritvo.adapter_contract.principal import Boundary
from ritvo.enforcement.evaluate import Result


def ask_requested(
    result: Result,
    tool: str,
    action: str,
    parameters: dict[str, Any] | None,
    boundary: Boundary,
    source: str,
    event_id: str,
    timestamp: str,
    linked_to: str | None = None,
) -> AskRequested:
    assert result.principal is not None and result.fingerprint is not None  # noqa: S101
    return AskRequested(
        contract_version="0.2",
        type="ask.requested",
        event_id=event_id,
        timestamp=timestamp,
        source=source,
        request_fingerprint=result.fingerprint,
        principal=result.principal,
        boundary=boundary,
        tool=tool,
        action=action,
        parameter_hash=parameter_hash(parameters),
        parameters=parameters,
        body_ref=None,
        policy_version=result.policy_version,
        trace_id=None,
        linked_to=linked_to,
    )


def call_denied(
    result: Result,
    tool: str,
    action: str,
    parameters: dict[str, Any] | None,
    boundary: Boundary,
    source: str,
    event_id: str,
    timestamp: str,
) -> CallDenied:
    assert result.principal is not None  # noqa: S101
    return CallDenied(
        contract_version="0.2",
        type="call.denied",
        event_id=event_id,
        timestamp=timestamp,
        source=source,
        principal=result.principal,
        boundary=boundary,
        tool=tool,
        action=action,
        parameters=parameters,
        binding_id=result.binding_id,
        policy_version=result.policy_version,
        reason=result.reason[:500],
    )
