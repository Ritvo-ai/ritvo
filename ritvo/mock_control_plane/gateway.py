"""The mock gateway: identify the caller, evaluate, refuse or pass, write the ledger.

It does no provider proxying, spend enforcement or credential custody, and must not be given any.
"""

import fnmatch
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from ritvo.adapter_contract.messages import Message
from ritvo.config.schema import Config
from ritvo.enforcement.evaluate import Evaluator, ledger_event
from ritvo.enforcement.events import ask_requested, call_denied
from ritvo.enforcement.refusal import DEFAULT_TTL_MINUTES, ask_body, stamp
from ritvo.ledger.store import Ledger

Upstream = Callable[[dict[str, Any]], Any]


@dataclass(frozen=True)
class Response:
    status: int
    body: dict[str, Any]


class MockGateway:
    def __init__(
        self,
        config: Config,
        evaluator: Evaluator,
        ledger: Ledger,
        upstreams: dict[str, Upstream],
        send_event: Callable[[Message], None] | None = None,
        source: str = "mock-gateway",
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._config = config
        self._evaluator = evaluator
        self._ledger = ledger
        self._upstreams = upstreams
        self._send = send_event
        self._source = source
        self._clock = clock

    def resolve_tool(self, method: str, path: str) -> str | None:
        """The tool whose `match` has this method and a path pattern that fits."""
        for tool in self._config.tools:
            match = tool.match
            if match and match.method == method and fnmatch.fnmatch(path, match.path):
                return tool.id
        return None

    def call(self, token: str, method: str, path: str, parameters: dict[str, Any]) -> Response:
        tool = self.resolve_tool(method, path)
        if tool is None:
            return Response(404, {"error": "no_route"})
        action = f"{method} {path}"
        boundary = self._evaluator.boundary
        now = self._clock()
        event_id = f"evt-{uuid.uuid4().hex}"
        result = self._evaluator.evaluate(token, tool, action, parameters)
        if result.principal is not None:
            self._ledger.append(ledger_event(result, tool, action, boundary, event_id, stamp(now)))
        if result.action == "refuse":
            return Response(401, {"error": result.reason})
        if result.action == "ask":
            if self._send is not None:
                self._send(
                    ask_requested(
                        result,
                        tool,
                        action,
                        parameters,
                        boundary,
                        self._source,
                        f"ask-{event_id}",
                        stamp(now),
                    )
                )
            return Response(403, ask_body(result, self._ttl(tool), now))
        if result.action == "deny":
            if self._send is not None:
                self._send(
                    call_denied(
                        result,
                        tool,
                        action,
                        parameters,
                        boundary,
                        self._source,
                        f"deny-{event_id}",
                        stamp(now),
                    )
                )
            return Response(403, {"error": "denied", "reason": result.reason})
        upstream = self._upstreams.get(tool)
        return Response(200, {"result": upstream(parameters) if upstream else {"ok": True}})

    def _ttl(self, tool: str) -> int:
        bundle = self._evaluator.store.bundle
        entry = next((t for t in bundle.tools if t.id == tool), None) if bundle else None
        return entry.approval_ttl_minutes or DEFAULT_TTL_MINUTES if entry else DEFAULT_TTL_MINUTES
