"""The signed policy bundle and its Ed25519 signature (docs/SPEC.md section 4.6)."""

import base64
import hashlib
import json
from collections.abc import Mapping
from typing import Annotated, Any, Literal

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from pydantic import Field, StrictInt, StrictStr, ValidationError

from ritvo.adapter_contract.canonical import canonical
from ritvo.adapter_contract.principal import Model, Timestamp

SIGNING_KEY_PATH = "ritvo/bundle-signing-key"
Tier = Literal["low", "medium", "high"]
Number = StrictInt | float
Minutes = Annotated[StrictInt, Field(ge=1)]


class BundleError(Exception):
    """A bundle cannot be read, does not verify, or is older than the one held."""


class BundleTool(Model):
    id: StrictStr
    tier: Tier
    approval_ttl_minutes: Minutes | None = None


class BundleBinding(Model):
    id: StrictStr
    agent: StrictStr
    tool: StrictStr
    decision: Literal["allow", "ask", "deny"]
    limits: dict[StrictStr, Number] = Field(default_factory=dict)


class AskTriggerRule(Model):
    id: StrictStr
    enabled: bool


class FailModes(Model):
    low: Literal["open", "closed"]
    medium: Literal["open", "closed"]
    high: Literal["open", "closed"]


class CedarSets(Model):
    deny: StrictStr
    over_limit: StrictStr
    ask: StrictStr
    allow: StrictStr


class Bundle(Model):
    policy_version: StrictInt
    issued_at: Timestamp
    cedar: CedarSets
    entities: list[dict[str, Any]]
    tools: list[BundleTool]
    bindings: list[BundleBinding]
    ask_trigger_rules: list[AskTriggerRule]
    fail_modes: FailModes
    max_age_minutes: Minutes


class Signature(Model):
    key_id: StrictStr
    alg: Literal["EdDSA"]
    value: StrictStr


class SignedBundle(Model):
    bundle: Bundle
    signature: Signature


def b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def unb64url(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def new_seed() -> str:
    """A new signing key as the base64url text of its 32-byte seed."""
    return b64url(Ed25519PrivateKey.generate().private_bytes_raw())


def private_key(seed: str) -> Ed25519PrivateKey:
    try:
        return Ed25519PrivateKey.from_private_bytes(unb64url(seed))
    except ValueError:
        raise BundleError("the signing key is not a 32-byte seed in base64url") from None


def key_id_of(public: Ed25519PublicKey) -> str:
    """`gos-` and the first 8 hex characters of the SHA-256 of the public key."""
    raw = public.public_bytes(Encoding.Raw, PublicFormat.Raw)
    return "gos-" + hashlib.sha256(raw).hexdigest()[:8]


def sign_bundle(bundle: Bundle, seed: str) -> SignedBundle:
    key = private_key(seed)
    value = b64url(key.sign(canonical(bundle.model_dump(mode="json"))))
    return SignedBundle(
        bundle=bundle,
        signature=Signature(key_id=key_id_of(key.public_key()), alg="EdDSA", value=value),
    )


def public_jwk(public: Ed25519PublicKey) -> dict[str, str]:
    raw = public.public_bytes(Encoding.Raw, PublicFormat.Raw)
    return {"kty": "OKP", "crv": "Ed25519", "kid": key_id_of(public), "x": b64url(raw)}


def jwks_of(*keys: Ed25519PublicKey) -> dict[str, list[dict[str, str]]]:
    return {"keys": [public_jwk(key) for key in keys]}


def trusted_keys(jwks: Mapping[str, Any]) -> dict[str, Ed25519PublicKey]:
    """The Ed25519 keys of a JWK set, by `kid`. Anything else in the set is ignored."""
    found: dict[str, Ed25519PublicKey] = {}
    for jwk in jwks.get("keys", []):
        if isinstance(jwk, dict) and jwk.get("kty") == "OKP" and jwk.get("crv") == "Ed25519":
            try:
                found[str(jwk["kid"])] = Ed25519PublicKey.from_public_bytes(unb64url(jwk["x"]))
            except (KeyError, ValueError):
                continue
    return found


def verify_bundle(signed: SignedBundle, keys: Mapping[str, Ed25519PublicKey]) -> Bundle:
    """The bundle if its signature verifies with a trusted key, else an error."""
    key = keys.get(signed.signature.key_id)
    if key is None:
        raise BundleError("the bundle is signed with a key that is not trusted")
    try:
        key.verify(
            unb64url(signed.signature.value), canonical(signed.bundle.model_dump(mode="json"))
        )
    except (InvalidSignature, ValueError):
        raise BundleError("the bundle's signature does not verify") from None
    return signed.bundle


def read_signed_bundle(data: bytes) -> SignedBundle:
    try:
        return SignedBundle.model_validate(json.loads(data.decode("utf-8")))
    except (UnicodeDecodeError, ValueError, ValidationError):
        raise BundleError("the bundle is not a valid signed bundle") from None


def encode_signed_bundle(signed: SignedBundle) -> bytes:
    return canonical(signed.model_dump(mode="json"))
