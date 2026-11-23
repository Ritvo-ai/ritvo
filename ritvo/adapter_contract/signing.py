"""Request signing and verification (docs/SPEC.md sections 6.1 and 6.2)."""

import hmac
import time
from collections.abc import Mapping

from ritvo.adapter_contract.canonical import sha256_hex

HEADER_KEY_ID = "Ritvo-Key-Id"
HEADER_TIMESTAMP = "Ritvo-Timestamp"
HEADER_SIGNATURE = "Ritvo-Signature"
MAX_SKEW_SECONDS = 300


class SignatureError(Exception):
    """A request is not signed, is signed wrongly, or is too far from the receiver's clock."""


def _signature(method: str, path: str, timestamp: str, body: bytes, key: bytes) -> str:
    message = "\n".join((method.upper(), path, timestamp, sha256_hex(body))).encode("utf-8")
    return hmac.new(key, message, "sha256").hexdigest()


def sign(
    method: str, path: str, body: bytes, key_id: str, key: bytes, now: float | None = None
) -> dict[str, str]:
    """The three headers for a request. `now` is whole Unix seconds."""
    timestamp = str(int(time.time() if now is None else now))
    return {
        HEADER_KEY_ID: key_id,
        HEADER_TIMESTAMP: timestamp,
        HEADER_SIGNATURE: _signature(method, path, timestamp, body, key),
    }


def _header(headers: Mapping[str, str], name: str) -> str | None:
    wanted = name.lower()
    for key, value in headers.items():
        if key.lower() == wanted:
            return value
    return None


def verify(
    method: str,
    path: str,
    body: bytes,
    headers: Mapping[str, str],
    keys: Mapping[str, bytes],
    now: float | None = None,
) -> str:
    """Return the key ID of a request that is correctly signed and on time, else raise."""
    key_id = _header(headers, HEADER_KEY_ID)
    timestamp = _header(headers, HEADER_TIMESTAMP)
    signature = _header(headers, HEADER_SIGNATURE)
    if key_id is None or timestamp is None or signature is None:
        raise SignatureError("the request is not signed")
    key = keys.get(key_id)
    if key is None:
        raise SignatureError("unknown key")
    try:
        sent = int(timestamp)
    except ValueError:
        raise SignatureError("the timestamp is not whole seconds") from None
    clock = time.time() if now is None else now
    if abs(clock - sent) > MAX_SKEW_SECONDS:
        raise SignatureError("the timestamp is more than 5 minutes from this clock")
    expected = _signature(method, path, timestamp, body, key)
    if not hmac.compare_digest(expected.encode("ascii"), signature.encode("utf-8", "replace")):
        raise SignatureError("the signature does not match")
    return key_id
