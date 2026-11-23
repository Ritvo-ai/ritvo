"""The MCP middleware for the official Python SDK (docs/SPEC.md section 5.12).

`protect(server, "ritvo.yaml")` is the whole of adopting it, and `.remove()` the whole of
undoing it. It needs the `mcp` extra: `pip install ritvo[mcp]`.
"""

import asyncio
import json
import logging
import time
import uuid
from pathlib import Path
from typing import Any

import anyio
from mcp.server.auth.middleware.auth_context import get_access_token
from mcp.server.auth.provider import AccessToken, TokenVerifier
from mcp.types import CallToolResult, TextContent

from ritvo.enforcement.evaluate import Result, ledger_event
from ritvo.enforcement.events import ask_requested, call_denied
from ritvo.enforcement.identity.tokens import TokenError
from ritvo.enforcement.refusal import refusal_body, stamp
from ritvo.enforcement.runtime import Runtime, build_runtime
from ritvo.secrets.access import Secrets

log = logging.getLogger("ritvo.enforcement")
ACTION = "tools/call"


class Protection:
    """What `protect` returns: the handle that undoes it."""

    def __init__(self, server: Any, runtime: Runtime, middleware: Any) -> None:
        self._server = server
        self.runtime = runtime
        self._middleware = middleware
        self._removed = False

    def refresh(self) -> bool:
        """Fetch the bundle from the decision service now."""
        return self.runtime.refresh(force=True)

    def remove(self) -> None:
        """Take the middleware out again and close what it opened. A second call does nothing."""
        if self._removed:
            return
        self._removed = True
        middleware = self._server.middleware
        if self._middleware in middleware:
            middleware.remove(self._middleware)
        self.runtime.close()


def _tool_ttl(runtime: Runtime, tool: str) -> int | None:
    bundle = runtime.store.bundle
    entry = next((t for t in bundle.tools if t.id == tool), None) if bundle else None
    return entry.approval_ttl_minutes if entry else None


def _error(body: dict[str, Any]) -> CallToolResult:
    return CallToolResult(
        content=[TextContent(type="text", text=json.dumps(body))],
        is_error=True,
        structured_content=body,
    )


def protect(server: Any, config_path: str | Path, secrets: Secrets | None = None) -> Protection:
    """Add Ritvo to an MCP server's middleware. Nothing else about the server changes."""
    runtime = build_runtime(Path(config_path), secrets)
    boundary = runtime.evaluator.boundary
    source = runtime.config.enforcement[0].id

    async def ritvo_middleware(ctx: Any, call_next: Any) -> Any:
        if ctx.method != "tools/call":
            return await call_next(ctx)
        params = ctx.params or {}
        tool = params.get("name")
        arguments = params.get("arguments") or {}
        if not isinstance(tool, str) or not isinstance(arguments, dict):
            return await call_next(ctx)
        access = get_access_token()
        token = access.token if access is not None else None
        if runtime.store.is_stale():
            await anyio.to_thread.run_sync(runtime.refresh)
        now = time.time()
        result = runtime.evaluator.evaluate(token, tool, ACTION, arguments)
        _record(runtime, result, tool, boundary, now)
        if result.action == "allow":
            return await call_next(ctx)
        _report(runtime, result, tool, arguments, boundary, source, now)
        return _error(refusal_body(result, _tool_ttl(runtime, tool), now))

    server.middleware.append(ritvo_middleware)
    return Protection(server, runtime, ritvo_middleware)


def _record(runtime: Runtime, result: Result, tool: str, boundary: Any, now: float) -> None:
    """One ledger entry for a call with a principal, or an observed call that had no token."""
    if result.principal is None and result.reason != "no_token":
        return
    event_id = f"evt-{uuid.uuid4().hex}"
    runtime.ledger.append(ledger_event(result, tool, ACTION, boundary, event_id, stamp(now)))


def _report(
    runtime: Runtime,
    result: Result,
    tool: str,
    arguments: dict[str, Any],
    boundary: Any,
    source: str,
    now: float,
) -> None:
    """Send the event for an ask or a deny. It is never waited on and never changes the outcome."""
    if runtime.service is None or result.principal is None:
        return
    event_id = f"evt-{uuid.uuid4().hex}"
    if result.action == "ask":
        message: Any = ask_requested(
            result, tool, ACTION, arguments, boundary, source, event_id, stamp(now)
        )
    elif result.action == "deny":
        message = call_denied(
            result, tool, ACTION, arguments, boundary, source, event_id, stamp(now)
        )
    else:
        return
    asyncio.get_running_loop().create_task(_send(runtime, message))


async def _send(runtime: Runtime, message: Any) -> None:
    try:
        await anyio.to_thread.run_sync(runtime.send, message)
    except Exception:
        log.warning("an event could not be sent")


class _RitvoVerifier(TokenVerifier):
    def __init__(self, runtime: Runtime) -> None:
        self._runtime = runtime

    async def verify_token(self, token: str) -> AccessToken | None:
        try:
            validated = self._runtime.validator.validate(token)
        except TokenError:
            return None
        claims = validated.claims
        subject = claims.get(validated.issuer.claims_map.subject.split(".")[0])
        scope = claims.get(validated.issuer.claims_map.scopes.split(".")[0], "")
        scopes = scope.split() if isinstance(scope, str) else list(scope)
        return AccessToken(
            token=token,
            client_id=str(subject),
            scopes=[str(item) for item in scopes],
            expires_at=int(claims["exp"]),
            resource=validated.issuer.audience,
            subject=str(subject) if subject is not None else None,
            claims=claims,
        )


def token_verifier(config_path: str | Path, secrets: Secrets | None = None) -> TokenVerifier:
    """A token verifier for a server that has none, built from the same ritvo.yaml."""
    return _RitvoVerifier(build_runtime(Path(config_path), secrets))
