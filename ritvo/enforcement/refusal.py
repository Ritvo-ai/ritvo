"""The bodies an enforcement point answers a refused call with (docs/SPEC.md section 5.8)."""

from datetime import UTC, datetime
from typing import Any

from ritvo.enforcement.evaluate import Result

DEFAULT_TTL_MINUTES = 15


def stamp(seconds: float) -> str:
    return datetime.fromtimestamp(seconds, UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def ask_body(result: Result, ttl_minutes: int | None, now: float) -> dict[str, Any]:
    fingerprint = result.fingerprint or ""
    ttl = ttl_minutes or DEFAULT_TTL_MINUTES
    return {
        "error": "authority_required",
        "escalation_id": "esc-" + fingerprint.removeprefix("sha256:")[:8],
        "request_fingerprint": fingerprint,
        "expires_at": stamp(now + ttl * 60),
        "retry": True,
    }


def refusal_body(result: Result, ttl_minutes: int | None, now: float) -> dict[str, Any]:
    """The body for an ask, a deny or a token that is refused."""
    if result.action == "ask":
        return ask_body(result, ttl_minutes, now)
    if result.action == "refuse":
        return {"error": "invalid_token", "reason": result.reason}
    return {"error": "denied", "reason": result.reason}
