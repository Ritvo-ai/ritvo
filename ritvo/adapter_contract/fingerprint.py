"""Fingerprint, parameter hash and principal digest (docs/SPEC.md section 4.4)."""

from typing import Any

from ritvo.adapter_contract.canonical import sha256_ref
from ritvo.adapter_contract.principal import Principal


def accountable_human(principal: Principal) -> dict[str, str | None]:
    """The subject if there is one, otherwise the owner, otherwise nobody."""
    if principal.subject is not None:
        return {"id": principal.subject.id, "issuer": principal.subject.issuer}
    return {"id": principal.owner, "issuer": None}


def has_accountable_human(principal: Principal) -> bool:
    return accountable_human(principal)["id"] is not None


def parameter_hash(parameters: dict[str, Any] | None) -> str:
    return sha256_ref(parameters if parameters is not None else {})


def request_fingerprint(
    principal: Principal, tool: str, action: str, parameters: dict[str, Any] | None
) -> str:
    return sha256_ref(
        {
            "accountable_human": accountable_human(principal),
            "actor": principal.actor.id,
            "tool": tool,
            "action": action,
            "parameters": parameters if parameters is not None else {},
        }
    )


def principal_digest(principal: Principal) -> str:
    return sha256_ref(principal.model_dump(mode="json"))
