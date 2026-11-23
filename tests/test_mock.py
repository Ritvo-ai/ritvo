from pathlib import Path
from typing import Any

import pytest
from conftest import NOW, Stack, make_stack
from fastapi.testclient import TestClient

from ritvo.adapter_contract.canonical import canonical
from ritvo.adapter_contract.messages import Message, encode
from ritvo.adapter_contract.signing import sign
from ritvo.governance_os.service.app import create_app
from ritvo.ledger.store import Ledger
from ritvo.mock_control_plane.gateway import MockGateway


def _gateway(
    stack: Stack, tmp_path: Path, mode: str = "enforce"
) -> tuple[MockGateway, Ledger, list[Message], list[dict[str, Any]]]:
    ledger = Ledger(tmp_path / "mock.db")
    sent: list[Message] = []
    calls: list[dict[str, Any]] = []

    def upstream(parameters: dict[str, Any]) -> dict[str, Any]:
        calls.append(parameters)
        return {"filled": True}

    gateway = MockGateway(
        stack.config,
        stack.evaluator(mode),
        ledger,
        {"broker.quote": upstream},
        send_event=sent.append,
        clock=lambda: NOW,
    )
    return gateway, ledger, sent, calls


def test_an_allowed_call_reaches_the_upstream(stack: Stack, tmp_path: Path) -> None:
    gateway, ledger, sent, calls = _gateway(stack, tmp_path)
    response = gateway.call(stack.idp.mint(), "GET", "/quotes/ACME", {"symbol": "ACME"})
    assert (response.status, response.body) == (200, {"result": {"filled": True}})
    assert calls == [{"symbol": "ACME"}] and sent == []
    assert ledger.verify().entries == 1


def test_an_ask_is_refused_with_the_standard_body_and_an_event(
    stack: Stack, tmp_path: Path
) -> None:
    gateway, ledger, sent, calls = _gateway(stack, tmp_path)
    response = gateway.call(stack.idp.mint(), "POST", "/orders/1", {"order_value": 100})
    assert response.status == 403
    body = response.body
    assert body["error"] == "authority_required" and body["retry"] is True
    assert body["escalation_id"].startswith("esc-") and body["request_fingerprint"].startswith(
        "sha256:"
    )
    assert body["expires_at"] == "2027-01-15T08:15:00Z"
    assert calls == []
    assert [m.type for m in sent] == ["ask.requested"]
    assert ledger.entries()[0].entry.decision == "ask"


def test_the_same_request_gets_the_same_escalation_id(stack: Stack, tmp_path: Path) -> None:
    gateway, *_ = _gateway(stack, tmp_path)
    first = gateway.call(stack.idp.mint(), "POST", "/orders/1", {"order_value": 100})
    again = gateway.call(stack.idp.mint(), "POST", "/orders/1", {"order_value": 100})
    other = gateway.call(stack.idp.mint(), "POST", "/orders/1", {"order_value": 101})
    assert first.body["escalation_id"] == again.body["escalation_id"] != other.body["escalation_id"]


def test_a_deny_is_refused_with_an_event(stack: Stack, tmp_path: Path) -> None:
    gateway, ledger, sent, _ = _gateway(stack, tmp_path)
    response = gateway.call(stack.idp.mint(act={"sub": "acme.reporting"}), "POST", "/orders/1", {})
    assert (response.status, response.body["error"], response.body["reason"]) == (
        403,
        "denied",
        "authority_gap",
    )
    assert [m.type for m in sent] == ["call.denied"]
    assert ledger.entries()[0].entry.decision == "deny"


def test_a_bad_token_is_a_401_and_nothing_is_written(stack: Stack, tmp_path: Path) -> None:
    gateway, ledger, sent, _ = _gateway(stack, tmp_path)
    response = gateway.call(stack.idp.mint(aud="elsewhere"), "GET", "/quotes/ACME", {})
    assert (response.status, response.body) == (401, {"error": "wrong_audience"})
    assert ledger.entries() == [] and sent == []


def test_an_unknown_route_is_a_404(stack: Stack, tmp_path: Path) -> None:
    gateway, *_ = _gateway(stack, tmp_path)
    assert gateway.call(stack.idp.mint(), "GET", "/nothing", {}).status == 404
    assert gateway.resolve_tool("DELETE", "/quotes/ACME") is None
    assert gateway.resolve_tool("GET", "/quotes/ACME") == "broker.quote"


def test_observe_mode_passes_an_ask_and_records_what_enforce_would_do(
    stack: Stack, tmp_path: Path
) -> None:
    gateway, ledger, sent, _ = _gateway(stack, tmp_path, "observe")
    response = gateway.call(stack.idp.mint(), "POST", "/orders/1", {"order_value": 100})
    assert response.status == 200 and sent == []
    entry = ledger.entries()[0].entry
    assert (entry.mode, entry.decision, entry.would_decide) == ("observe", "allow", "ask")


def test_the_mock_sends_its_events_to_the_service(stack: Stack, tmp_path: Path) -> None:
    key = b"the-hmac-key-of-ep-1-not-real"
    service_ledger = Ledger(tmp_path / "service.db")
    client = TestClient(
        create_app(service_ledger, {"mock-gateway": key}, None, {"keys": []}, clock=lambda: NOW)
    )
    statuses: list[int] = []

    def send(message: Message) -> None:
        body = encode(message)
        headers = sign("POST", "/v1/events", body, "mock-gateway", key, NOW)
        statuses.append(client.post("/v1/events", content=body, headers=headers).status_code)

    ledger = Ledger(tmp_path / "mock.db")
    gateway = MockGateway(
        stack.config, stack.evaluator(), ledger, {}, send_event=send, clock=lambda: NOW
    )
    gateway.call(stack.idp.mint(), "POST", "/orders/1", {"order_value": 100})
    gateway.call(stack.idp.mint(act={"sub": "acme.reporting"}), "POST", "/orders/1", {})
    assert statuses == [200, 200]
    types = [e.entry.type for e in service_ledger.entries()]
    assert types == ["escalation", "decision"]
    assert service_ledger.verify().ok and canonical({}) == b"{}"


def test_the_mock_stays_crude() -> None:
    """It holds no credentials and does no proxying: its source must not mention them."""
    source = (
        Path(__file__).resolve().parents[1] / "ritvo" / "mock_control_plane" / "gateway.py"
    ).read_text()
    code = "\n".join(
        line for line in source.splitlines() if not line.lstrip().startswith(("#", '"""'))
    )
    for word in ("httpx", "requests", "keyring", "secret://", "Secrets", "budget"):
        assert word not in code
    assert make_stack is not None and pytest is not None
