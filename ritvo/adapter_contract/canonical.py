"""Canonical JSON and hashes (docs/SPEC.md section 4.9)."""

import hashlib
from typing import Any

import rfc8785


def canonical(value: Any) -> bytes:
    """RFC 8785 canonical JSON. NaN and infinity are refused."""
    return rfc8785.dumps(value)


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_ref(value: Any) -> str:
    """`sha256:` and the hex digest of the canonical JSON of a value."""
    return "sha256:" + sha256_hex(canonical(value))
