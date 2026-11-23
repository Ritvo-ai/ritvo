"""Turn a contract message into a ledger event (docs/SPEC.md section 6.2)."""

from ritvo.adapter_contract.messages import (
    AskRequested,
    CallDenied,
    GrantUsed,
    InventoryReported,
    LedgerEntryMessage,
    LimitNear,
    Message,
    RequestExecuted,
    RequestExpired,
)
from ritvo.ledger.event import LedgerEvent

# Messages only the Governance OS may send.
OUTBOUND = ("policy.published", "decision.resolved")


def to_ledger_event(message: Message) -> LedgerEvent:
    """The entry for a message from an enforcement point. Parameters are never kept."""
    match message:
        case AskRequested():
            return LedgerEvent(
                event_id=message.event_id,
                timestamp=message.timestamp,
                type="escalation",
                principal=message.principal,
                boundary=message.boundary,
                tool=message.tool,
                action=message.action,
                parameter_fingerprint=message.request_fingerprint,
                policy_version=message.policy_version,
                decision="ask",
            )
        case CallDenied():
            return LedgerEvent(
                event_id=message.event_id,
                timestamp=message.timestamp,
                type="decision",
                principal=message.principal,
                boundary=message.boundary,
                tool=message.tool,
                action=message.action,
                policy_version=message.policy_version,
                binding_id=message.binding_id,
                decision="deny",
                outcome=message.reason,
            )
        case GrantUsed():
            return LedgerEvent(
                event_id=message.event_id,
                timestamp=message.timestamp,
                type="grant",
                parameter_fingerprint=message.request_fingerprint,
                approval_or_grant_ref=message.grant_id,
                outcome=message.outcome,
            )
        case RequestExecuted():
            return LedgerEvent(
                event_id=message.event_id,
                timestamp=message.timestamp,
                type="resolution",
                parameter_fingerprint=message.request_fingerprint,
                approval_or_grant_ref=message.grant_id,
                outcome=message.outcome,
                latency_ms=message.latency_ms,
            )
        case RequestExpired():
            return LedgerEvent(
                event_id=message.event_id,
                timestamp=message.timestamp,
                type="resolution",
                parameter_fingerprint=message.request_fingerprint,
                decision="expired",
                outcome=message.reason,
            )
        case InventoryReported():
            return LedgerEvent(
                event_id=message.event_id,
                timestamp=message.timestamp,
                type="admin",
                outcome="inventory.reported",
            )
        case LimitNear():
            return LedgerEvent(
                event_id=message.event_id,
                timestamp=message.timestamp,
                type="admin",
                principal=message.principal,
                tool=message.tool,
                outcome=f"limit.near {message.limit}",
            )
        case LedgerEntryMessage():
            return LedgerEvent(
                event_id=message.event_id,
                timestamp=message.timestamp,
                type="admin",
                outcome=f"ledger.entry {message.entry.event_id}",
            )
    raise ValueError(f"{message.type} is not a message an enforcement point sends")
