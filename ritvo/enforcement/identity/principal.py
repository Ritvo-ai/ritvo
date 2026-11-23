"""Build the principal from validated claims (docs/SPEC.md section 5.10)."""

from collections.abc import Callable
from itertools import pairwise
from typing import Any

from pydantic import ValidationError

from ritvo.adapter_contract.principal import Actor, Boundary, Hop, Mode, Principal, Subject
from ritvo.enforcement.identity.tokens import TokenError, ValidatedToken


def dig(claims: dict[str, Any], path: str) -> Any:
    """The value at a dotted path, or None."""
    node: Any = claims
    for segment in path.split("."):
        if not isinstance(node, dict) or segment not in node:
            return None
        node = node[segment]
    return node


def _scopes(value: Any) -> list[str]:
    if isinstance(value, str):
        return value.split()
    if isinstance(value, list) and all(isinstance(item, str) for item in value):
        return list(value)
    return []


def widened(chain: list[Hop]) -> bool:
    """A hop has a scope that the hop before it does not."""
    return any(set(later.scopes) - set(earlier.scopes) for earlier, later in pairwise(chain))


def build_principal(
    token: ValidatedToken,
    boundary: Boundary,
    owner_of: Callable[[str], str | None],
) -> tuple[Principal, list[str]]:
    """The principal and its flags. An unusable `chain` claim refuses the token."""
    issuer, claims = token.issuer, token.claims
    cmap = issuer.claims_map
    subject_claim = dig(claims, cmap.subject)
    actor_claim = dig(claims, cmap.actor)
    if not isinstance(subject_claim, str) or not subject_claim:
        raise TokenError("malformed")
    platform = dig(claims, cmap.actor_platform) if cmap.actor_platform else None
    mode: Mode
    subject: Subject | None
    if isinstance(actor_claim, str) and actor_claim:
        mode, subject, actor_id = (
            "attended",
            Subject(id=subject_claim, issuer=issuer.name),
            actor_claim,
        )
    else:
        mode, subject, actor_id = "unattended", None, subject_claim
    own = Hop(
        boundary=boundary,
        audience=issuer.audience,
        scopes=_scopes(dig(claims, cmap.scopes)),
        token_ref=token.token_ref,
    )
    earlier: list[Hop] = []
    chain_claim = dig(claims, cmap.chain) if cmap.chain else None
    if chain_claim is not None:
        try:
            if not isinstance(chain_claim, list):
                raise ValueError
            earlier = [Hop.model_validate(hop) for hop in chain_claim]
        except (ValueError, ValidationError):
            raise TokenError("malformed") from None
    chain = [*earlier, own]
    owner = owner_of(actor_id)
    principal = Principal(
        mode=mode,
        subject=subject,
        actor=Actor(id=actor_id, platform=platform if isinstance(platform, str) else None),
        owner=owner,
        chain=chain,
    )
    flags = []
    if widened(chain):
        flags.append("scope_widened")
    if subject is None and owner is None:
        flags += ["no_owner", "no_accountable_human"]
    return principal, flags
