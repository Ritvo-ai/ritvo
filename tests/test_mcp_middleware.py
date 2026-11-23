import inspect
import json
import time
from pathlib import Path
from typing import Any

import httpx
from conftest import ISS, Stack, make_stack, write_enforcement_files
from mcp import Client
from mcp.server.auth.provider import AccessToken, TokenVerifier
from mcp.server.auth.settings import AuthSettings
from mcp.server.context import ServerMiddleware
from mcp.server.mcpserver import MCPServer
from pydantic import AnyHttpUrl
from starlette.testclient import TestClient

from ritvo.adapter_contract.bundle import encode_signed_bundle
from ritvo.enforcement.mcp_middleware import protect, token_verifier
from ritvo.governance_os.routing.publish import publish_bundle

HEADERS = {
    "Accept": "application/json, text/event-stream",
    "MCP-Protocol-Version": "2026-07-28",
    "Mcp-Method": "tools/call",
}
META = {
    "io.modelcontextprotocol/protocolVersion": "2026-07-28",
    "io.modelcontextprotocol/clientInfo": {"name": "t", "version": "1"},
    "io.modelcontextprotocol/clientCapabilities": {},
}


class AcceptAnything(TokenVerifier):
    """A server's own verifier that checks nothing, so Ritvo's own validation is what is tested."""

    async def verify_token(self, token: str) -> AccessToken | None:
        return AccessToken(token=token, client_id="c", scopes=[])


class Setup:
    def __init__(self, stack: Stack, tmp_path: Path, mode: str, own_verifier: bool = False) -> None:
        self.stack, self.dir = stack, tmp_path
        self.ran: list[str] = []
        self.config_path = write_enforcement_files(stack, tmp_path)
        self.server = self._server(own_verifier)
        self.protection = protect(self.server, self.config_path, secrets=stack.secrets)
        assert mode == self.protection.runtime.config.mode

    def _server(self, own_verifier: bool) -> MCPServer:
        verifier: TokenVerifier = (
            AcceptAnything()
            if own_verifier
            else token_verifier(self.config_path, self.stack.secrets)
        )
        server = MCPServer(
            "demo",
            token_verifier=verifier,
            auth=AuthSettings(
                issuer_url=AnyHttpUrl(ISS),
                resource_server_url=AnyHttpUrl("http://localhost:8000/mcp"),
                validate_token_resource=False,
            ),
        )

        def tool(name: str) -> None:
            @server.tool(name=name)
            def run(order_value: int | None = None, depth: int | None = None) -> str:
                self.ran.append(name)
                return f"{name} ran"

        for name in ("broker.quote", "broker.order", "ledger.adjust"):
            tool(name)
        return server

    def call(self, token: str | None, tool: str, arguments: dict[str, Any] | None = None) -> Any:
        body = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": tool, "arguments": arguments or {}, "_meta": META},
        }
        headers = {**HEADERS, "Mcp-Name": tool}
        if token is not None:
            headers["Authorization"] = f"Bearer {token}"
        with TestClient(
            self.server.streamable_http_app(), base_url="http://localhost:8000"
        ) as client:
            return client.post("/mcp", json=body, headers=headers)

    def ledger(self) -> list[Any]:
        return [entry.entry for entry in self.protection.runtime.ledger.entries()]


def _setup(
    tmp_path: Path, mode: str = "enforce", own_verifier: bool = False
) -> tuple[Setup, Stack]:
    def edit(data: dict[str, Any]) -> None:
        data["mode"] = mode
        for agent in data["agents"]:
            agent["owner"] = agent["owner"] or "dan"

    stack = make_stack(tmp_path, edit=edit)
    return Setup(stack, tmp_path, mode, own_verifier), stack


def _result(response: Any) -> dict[str, Any]:
    assert response.status_code == 200, response.text
    result: dict[str, Any] = response.json()["result"]
    return result


# The outcomes over HTTP


def test_an_allowed_call_runs(tmp_path: Path) -> None:
    setup, stack = _setup(tmp_path)
    result = _result(setup.call(stack.idp.mint(), "broker.quote"))
    assert result["isError"] is False and setup.ran == ["broker.quote"]
    entry = setup.ledger()[0]
    assert (entry.decision, entry.would_decide, entry.mode) == ("allow", "allow", "enforce")


def test_an_ask_is_refused_with_the_standard_body_and_the_tool_does_not_run(tmp_path: Path) -> None:
    setup, stack = _setup(tmp_path)
    result = _result(setup.call(stack.idp.mint(), "broker.order", {"order_value": 100}))
    assert result["isError"] is True and setup.ran == []
    body = result["structuredContent"]
    assert body["error"] == "authority_required" and body["retry"] is True
    assert body["escalation_id"].startswith("esc-") and body["request_fingerprint"].startswith(
        "sha256:"
    )
    assert json.loads(result["content"][0]["text"]) == body
    assert setup.ledger()[0].would_decide == "ask"


def test_the_same_request_has_the_same_escalation_id(tmp_path: Path) -> None:
    setup, stack = _setup(tmp_path)
    ids = {
        _result(setup.call(stack.idp.mint(), "broker.order", {"order_value": v}))[
            "structuredContent"
        ]["escalation_id"]
        for v in (1, 1, 2)
    }
    assert len(ids) == 2


def test_a_deny_is_refused(tmp_path: Path) -> None:
    setup, stack = _setup(tmp_path)
    token = stack.idp.mint(act={"sub": "acme.reporting"})
    result = _result(setup.call(token, "broker.order"))
    assert result["structuredContent"] == {"error": "denied", "reason": "authority_gap"}
    assert setup.ran == []


def test_observe_passes_an_ask_and_records_what_enforce_would_do(tmp_path: Path) -> None:
    setup, stack = _setup(tmp_path, mode="observe")
    result = _result(setup.call(stack.idp.mint(), "broker.order", {"order_value": 100}))
    assert result["isError"] is False and setup.ran == ["broker.order"]
    entry = setup.ledger()[0]
    assert (entry.mode, entry.decision, entry.would_decide) == ("observe", "allow", "ask")


def test_the_servers_own_verifier_does_not_replace_ritvos_validation(tmp_path: Path) -> None:
    setup, stack = _setup(tmp_path, own_verifier=True)
    result = _result(setup.call(stack.idp.mint(aud="other-mcp"), "broker.quote"))
    assert result["structuredContent"] == {"error": "invalid_token", "reason": "wrong_audience"}
    assert setup.ran == [] and setup.ledger() == []


def test_ritvos_verifier_stops_a_bad_token_at_the_door(tmp_path: Path) -> None:
    setup, stack = _setup(tmp_path)
    assert setup.call(stack.idp.mint(aud="other-mcp"), "broker.quote").status_code == 401
    assert setup.call(None, "broker.quote").status_code == 401
    assert setup.ran == []


def test_the_token_is_never_written_to_the_ledger(tmp_path: Path) -> None:
    setup, stack = _setup(tmp_path)
    token = stack.idp.mint()
    setup.call(token, "broker.order", {"order_value": 31415})
    text = "".join(setup.protection.runtime.ledger.export())
    assert token not in text and "31415" not in text


# No token: a server that has none yet


def _client_result(setup: Setup, tool: str) -> Any:
    import anyio

    async def go() -> Any:
        async with Client(setup.server) as client:
            return await client.call_tool(tool, {})

    return anyio.run(go)


def test_no_token_passes_in_observe_mode_and_is_logged(tmp_path: Path) -> None:
    setup, _ = _setup(tmp_path, mode="observe")
    result = _client_result(setup, "broker.order")
    assert not result.is_error and setup.ran == ["broker.order"]
    entry = setup.ledger()[0]
    assert entry.principal is None and "no_token" in entry.flags and entry.mode == "observe"


def test_no_token_is_refused_in_enforce_mode(tmp_path: Path) -> None:
    setup, _ = _setup(tmp_path)
    result = _client_result(setup, "broker.quote")
    assert result.is_error and result.structured_content == {
        "error": "invalid_token",
        "reason": "missing_token",
    }
    assert setup.ran == [] and setup.ledger() == []


def test_other_methods_pass(tmp_path: Path) -> None:
    import anyio

    setup, _ = _setup(tmp_path)

    async def go() -> list[str]:
        async with Client(setup.server) as client:
            return [tool.name for tool in (await client.list_tools()).tools]

    assert "broker.quote" in anyio.run(go)
    assert setup.ledger() == []


# Adopting and removing it


def test_protect_adds_one_function_and_remove_takes_it_out(tmp_path: Path) -> None:
    stack = make_stack(tmp_path)
    config_path = write_enforcement_files(stack, tmp_path)
    server = MCPServer("demo")
    before = list(server.middleware)
    protection = protect(server, config_path, secrets=stack.secrets)
    assert len(server.middleware) == len(before) + 1 and server.middleware[:-1] == before
    protection.remove()
    assert server.middleware == before
    protection.remove()  # a second call does nothing


def test_after_remove_calls_are_not_evaluated(tmp_path: Path) -> None:
    setup, stack = _setup(tmp_path)
    setup.call(stack.idp.mint(), "broker.quote")
    assert len(setup.ledger()) == 1
    ledger_path = tmp_path / "ritvo-ledger.db"
    setup.protection.remove()
    result = _result(setup.call(stack.idp.mint(), "broker.order", {"order_value": 1}))
    assert result["isError"] is False and "broker.order" in setup.ran
    from ritvo.ledger.store import Ledger

    assert len(Ledger(ledger_path).entries()) == 1


def test_the_sdk_middleware_signature_is_what_we_built_for() -> None:
    """The SDK marks its middleware list provisional. If this fails, review the adapter."""
    parameters = list(inspect.signature(ServerMiddleware.__call__).parameters)
    assert parameters == ["self", "ctx", "call_next"]
    assert isinstance(MCPServer("x").middleware, list)


# The decision service


def _service(handler: Any, setup: Setup) -> list[httpx.Request]:
    seen: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        response: httpx.Response = handler(request)
        return response

    runtime = setup.protection.runtime
    runtime.service = httpx.Client(
        transport=httpx.MockTransport(respond), base_url="http://localhost"
    )
    runtime.service_key_id, runtime.service_key = "ep-1", b"a-key"
    return seen


def _wait(seen: list[httpx.Request], count: int) -> None:
    for _ in range(100):
        if len(seen) >= count:
            return
        time.sleep(0.02)


def test_an_ask_and_a_deny_are_sent_to_the_service(tmp_path: Path) -> None:
    setup, stack = _setup(tmp_path)
    seen = _service(lambda request: httpx.Response(200, json={"accepted": True}), setup)
    setup.call(stack.idp.mint(), "broker.order", {"order_value": 1})
    setup.call(stack.idp.mint(act={"sub": "acme.reporting"}), "broker.order")
    _wait(seen, 2)
    kinds = sorted(json.loads(request.content)["type"] for request in seen)
    assert kinds == ["ask.requested", "call.denied"]
    assert all(
        request.url.path == "/v1/events" and "ritvo-signature" in request.headers
        for request in seen
    )


def test_a_service_that_does_not_answer_changes_nothing(tmp_path: Path) -> None:
    setup, stack = _setup(tmp_path)

    def down(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down")

    seen = _service(down, setup)
    result = _result(setup.call(stack.idp.mint(), "broker.order", {"order_value": 1}))
    _wait(seen, 1)
    assert result["structuredContent"]["error"] == "authority_required"


def test_observe_sends_no_events(tmp_path: Path) -> None:
    setup, stack = _setup(tmp_path, mode="observe")
    seen = _service(lambda request: httpx.Response(200), setup)
    setup.call(stack.idp.mint(), "broker.order", {"order_value": 1})
    time.sleep(0.2)
    assert seen == []


def test_refresh_installs_a_newer_bundle_and_refuses_an_older_one(tmp_path: Path) -> None:
    setup, stack = _setup(tmp_path)
    newer = publish_bundle(stack.config, stack.secrets, 5)
    older = publish_bundle(stack.config, stack.secrets, 0)
    bodies = [encode_signed_bundle(newer), encode_signed_bundle(older)]
    _service(
        lambda request: httpx.Response(200, content=bodies.pop(0), headers={"etag": '"x"'}), setup
    )
    assert setup.protection.refresh() is True
    assert setup.protection.runtime.store.bundle.policy_version == 5  # type: ignore[union-attr]
    assert setup.protection.refresh() is False
    assert setup.protection.runtime.store.bundle.policy_version == 5  # type: ignore[union-attr]
