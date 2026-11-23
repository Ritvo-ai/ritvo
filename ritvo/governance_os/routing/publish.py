"""Compile and sign a bundle with the key in the secrets store (docs/SPEC.md section 4.6)."""

from datetime import datetime

from ritvo.adapter_contract.bundle import (
    SIGNING_KEY_PATH,
    SignedBundle,
    sign_bundle,
)
from ritvo.config.schema import Config
from ritvo.governance_os.routing.compile import compile_bundle
from ritvo.secrets.access import Secrets


def publish_bundle(
    config: Config, secrets: Secrets, policy_version: int, issued_at: datetime | None = None
) -> SignedBundle:
    """The signed bundle for a complete config. A signing key that cannot be read stops it."""
    seed = secrets.get(SIGNING_KEY_PATH)
    return sign_bundle(compile_bundle(config, policy_version, issued_at), seed)
