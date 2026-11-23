import json
from pathlib import Path
from typing import Any

import pytest
from conftest import NOW, Stack
from fastapi.testclient import TestClient

from ritvo.adapter_contract.bundle import encode_signed_bundle, jwks_of, private_key
from ritvo.adapter_contract.canonical import canonical
from ritvo.adapter_contract.fingerprint import parameter_hash
from ritvo.adapter_contract.signing import sign
from ritvo.governance_os.service.app import create_app
from ritvo.ledger.store import Ledger

KEY = b"the-hmac-key-of-ep-1-not-real"
HASH = "sha256:" + "ab" * 32


def _principal() -> dict[str, Any]:
    return {
        "mode": "attended",
        "subject": {"id": "u-1", "issuer": "corp-idp"},
        "actor": {"id": "acme.research", "platform": None},
        "owner": "alice",
        "chain": [{"boundary": "mcp", "audience": "demo-mcp", "scopes": ["a"], "token_ref": HASH}],
    }


def _message(kind: str, **fields: Any) -> dict[str, Any]:
    base = {
        "contract_version": "0.2",
        "event_id": f"evt-{kind}",
        "timestamp": "2026-11-23T10:00:00Z",
        "source": "ep-1",
        "type": kind,
    }
    return {**base, **fields}


MESSAGES = {
    "ask.requested": _message(
        "ask.requested",
        request_fingerprint=HASH,
        principal=_principal(),
        boundary="mcp",
        tool="broker.order",
        action="POST /orders",
        parameter_hash=parameter_hash({"v": 1}),
        parameters={"v": 31415, "memo": "private-note"},
        body_ref=None,
        policy_version=1,
        trace_id=None,
        linked_to=None,
    ),
    "call.denied": _message(
        "call.denied",
        principal=_principal(),
        boundary="mcp",
        tool="t",
        action="a",
        parameters={"v": 31415},
        binding_id="b-1",
        policy_version=1,
        reason="no binding",
    ),
    "grant.used": _message(
        "grant.used", request_fingerprint=HASH, grant_id="g-1", outcome="accepted"
    ),
    "request.executed": _message(
        "request.executed", request_fingerprint=HASH, grant_id=None, outcome="success", latency_ms=5
    ),
    "request.expired": _message("request.expired", request_fingerprint=HASH, reason="ttl"),
    "inventory.reported": _message("inventory.reported", inventory={"agents": []}),
    "limit.near": _message(
        "limit.near", principal=_principal(), tool="t", limit="max_v", limit_value=10, observed=9
    ),
    "ledger.entry": _message(
        "ledger.entry",
        entry={
            "event_id": "evt-x",
            "timestamp": "2026-11-23T10:00:00Z",
            "type": "decision",
            "prev_hash": "0" * 64,
            "hash": "1" * 64,
        },
    ),
}


class Service:
    def __init__(self, stack: Stack, tmp_path: Path, with_bundle: bool = True) -> None:
        self.stack = stack
        self.ledger = Ledger(tmp_path / "service.db")
        self.bundle_path = tmp_path / "bundle.json"
        if with_bundle:
            self.bundle_path.write_bytes(encode_signed_bundle(stack.signed))
        jwks = jwks_of(private_key(stack.seed).public_key())
        self.jwks = jwks
        app = create_app(self.ledger, {"ep-1": KEY}, self.bundle_path, jwks, clock=lambda: NOW)
        self.client = TestClient(app)

    def post(
        self, message: dict[str, Any], key: bytes = KEY, key_id: str = "ep-1", now: float = NOW
    ) -> Any:
        body = canonical(message)
        headers = sign("POST", "/v1/events", body, key_id, key, now)
        return self.client.post("/v1/events", content=body, headers=headers)

    def get(self, path: str, signed: bool = True, extra: dict[str, str] | None = None) -> Any:
        headers = sign("GET", path, b"", "ep-1", KEY, NOW) if signed else {}
        return self.client.get(path, headers={**headers, **(extra or {})})


@pytest.fixture
def service(stack: Stack, tmp_path: Path) -> Service:
    return Service(stack, tmp_path)


# Events


@pytest.mark.parametrize("kind", list(MESSAGES))
def test_every_inbound_message_is_accepted_and_written(service: Service, kind: str) -> None:
    response = service.post(MESSAGES[kind])
    assert response.status_code == 200, response.text
    assert response.json() == {"accepted": True, "duplicate": False, "seq": 1}
    assert service.ledger.verify().ok


def test_message_types_map_to_ledger_types(service: Service) -> None:
    for kind in MESSAGES:
        assert service.post(MESSAGES[kind]).status_code == 200
    entries = {e.entry.event_id: e.entry for e in service.ledger.entries()}
    assert (
        entries["evt-ask.requested"].type == "escalation"
        and entries["evt-ask.requested"].decision == "ask"
    )
    assert entries["evt-ask.requested"].parameter_fingerprint == HASH
    assert (
        entries["evt-call.denied"].type == "decision"
        and entries["evt-call.denied"].decision == "deny"
    )
    assert entries["evt-grant.used"].type == "grant"
    assert entries["evt-request.executed"].type == "resolution"
    assert (entries["evt-request.expired"].type, entries["evt-request.expired"].decision) == (
        "resolution",
        "expired",
    )
    for kind in ("inventory.reported", "limit.near", "ledger.entry"):
        assert entries[f"evt-{kind}"].type == "admin"


def test_a_duplicate_event_changes_nothing(service: Service) -> None:
    first = service.post(MESSAGES["ask.requested"]).json()
    second = service.post(MESSAGES["ask.requested"]).json()
    assert second == {"accepted": True, "duplicate": True, "seq": first["seq"]}
    assert len(service.ledger.entries()) == 1


def test_parameters_are_not_kept(service: Service) -> None:
    service.post(MESSAGES["ask.requested"])
    service.post(MESSAGES["call.denied"])
    text = "".join(service.ledger.export())
    assert "31415" not in text and "private-note" not in text


def test_a_request_that_is_not_signed_correctly_is_refused(service: Service) -> None:
    body = canonical(MESSAGES["grant.used"])
    assert service.client.post("/v1/events", content=body).status_code == 401
    assert service.post(MESSAGES["grant.used"], key=b"another-key").status_code == 401
    assert service.post(MESSAGES["grant.used"], key_id="ep-9").status_code == 401
    assert service.post(MESSAGES["grant.used"], now=NOW - 400).status_code == 401
    tampered = sign("POST", "/v1/events", body, "ep-1", KEY, NOW)
    assert (
        service.client.post("/v1/events", content=body + b" ", headers=tampered).status_code == 401
    )
    assert service.ledger.entries() == []


def test_a_message_the_governance_os_sends_is_refused(service: Service) -> None:
    for kind, fields in (
        (
            "decision.resolved",
            {
                "request_fingerprint": HASH,
                "outcome": "reject",
                "approver": "bob",
                "rationale_ref": None,
                "grant": None,
            },
        ),
        (
            "policy.published",
            {
                "policy_version": 2,
                "bundle": {},
                "ask_trigger_rules": [],
                "author": "a",
                "approvers": [],
                "effective_date": "2026-11-24T00:00:00Z",
            },
        ),
    ):
        response = service.post(_message(kind, **fields))
        assert response.status_code == 400 and "Governance OS" in response.json()["error"]
    assert service.ledger.entries() == []


@pytest.mark.parametrize(
    "body",
    [
        b"not json",
        b"[]",
        b'{"contract_version": "0.1"}',
        b'{"contract_version": "0.2", "type": "x"}',
    ],
)
def test_a_body_that_is_not_a_valid_message_is_refused(service: Service, body: bytes) -> None:
    headers = sign("POST", "/v1/events", body, "ep-1", KEY, NOW)
    assert service.client.post("/v1/events", content=body, headers=headers).status_code == 400


# Bundles and keys


def test_the_bundle_is_served_with_an_etag(service: Service) -> None:
    response = service.get("/v1/bundles/latest")
    assert response.status_code == 200
    assert response.content == encode_signed_bundle(service.stack.signed)
    etag = response.headers["etag"]
    assert etag.startswith('"') and etag.endswith('"') and len(etag) == 66
    assert service.get("/v1/bundles/latest", extra={"If-None-Match": etag}).status_code == 304
    assert service.get("/v1/bundles/latest", extra={"If-None-Match": '"other"'}).status_code == 200


def test_the_bundle_needs_a_signed_request(service: Service) -> None:
    assert service.get("/v1/bundles/latest", signed=False).status_code == 401


def test_no_bundle_is_a_404(stack: Stack, tmp_path: Path) -> None:
    assert Service(stack, tmp_path, with_bundle=False).get("/v1/bundles/latest").status_code == 404


def test_the_public_keys_need_no_signature(service: Service) -> None:
    response = service.get("/v1/keys", signed=False)
    assert response.status_code == 200
    assert response.json() == json.loads(json.dumps(service.jwks))
    assert response.json()["keys"][0]["kty"] == "OKP"
