import json
from copy import deepcopy
from typing import Any

import pytest
from pydantic import ValidationError

from ritvo.adapter_contract.canonical import canonical, sha256_ref
from ritvo.adapter_contract.fingerprint import (
    accountable_human,
    has_accountable_human,
    parameter_hash,
    principal_digest,
    request_fingerprint,
)
from ritvo.adapter_contract.messages import (
    AskRequested,
    ContractError,
    DecisionResolved,
    decode,
    encode,
)
from ritvo.adapter_contract.principal import Principal
from ritvo.adapter_contract.signing import (
    HEADER_SIGNATURE,
    MAX_SKEW_SECONDS,
    SignatureError,
    sign,
    verify,
)

HASH = "sha256:" + "ab" * 32
NOW = 1_800_000_000.0
KEYS = {"ep-1": b"a-test-key-not-a-real-one"}


def _principal(subject: str | None = "u-1", owner: str | None = "alice") -> Principal:
    data: dict[str, Any] = {
        "mode": "attended" if subject else "unattended",
        "subject": {"id": subject, "issuer": "corp-idp"} if subject else None,
        "actor": {"id": "acme.research", "platform": "agentcore"},
        "owner": owner,
        "chain": [
            {
                "boundary": "edge",
                "audience": "agent-platform",
                "scopes": ["orders:read"],
                "token_ref": "sha256:" + "9b" * 32,
            }
        ],
    }
    return Principal.model_validate(data)


# Canonical JSON


def test_canonical_json_is_rfc_8785() -> None:
    assert (
        canonical({"b": 2, "a": [1.5, "é", None, True]})
        == '{"a":[1.5,"é",null,true],"b":2}'.encode()
    )
    assert canonical({"a": 1, "b": 2}) == canonical({"b": 2, "a": 1})
    assert sha256_ref({"a": 1}).startswith("sha256:")


def test_canonical_json_refuses_nan() -> None:
    with pytest.raises(ValueError):
        canonical({"a": float("nan")})


# Principal


def test_a_token_cannot_be_a_token_ref() -> None:
    jwt = "eyJhbGciOiJFZERTQSJ9.eyJzdWIiOiJ1LTEifQ.c2ln"
    data = _principal().model_dump(mode="json")
    data["chain"][0]["token_ref"] = jwt
    with pytest.raises(ValidationError):
        Principal.model_validate(data)


def test_subject_follows_mode() -> None:
    data = _principal().model_dump(mode="json")
    data["subject"] = None
    with pytest.raises(ValidationError):
        Principal.model_validate(data)
    data = _principal(subject=None).model_dump(mode="json")
    data["subject"] = {"id": "u-1", "issuer": "corp-idp"}
    with pytest.raises(ValidationError):
        Principal.model_validate(data)


def test_a_principal_refuses_unknown_fields() -> None:
    data = _principal().model_dump(mode="json")
    data["raw_token"] = "x"
    with pytest.raises(ValidationError):
        Principal.model_validate(data)


# Fingerprints


def test_one_agent_for_two_users_gives_two_fingerprints() -> None:
    args = ("broker.order", "POST /orders", {"value": 100})
    one = request_fingerprint(_principal("u-1"), *args)
    two = request_fingerprint(_principal("u-2"), *args)
    assert one != two
    assert one == request_fingerprint(_principal("u-1"), *args)


def test_the_fingerprint_changes_with_parameters_tool_and_action() -> None:
    base = request_fingerprint(_principal(), "t", "a", {"v": 1})
    assert base != request_fingerprint(_principal(), "t", "a", {"v": 2})
    assert base != request_fingerprint(_principal(), "t2", "a", {"v": 1})
    assert base != request_fingerprint(_principal(), "t", "a2", {"v": 1})
    assert request_fingerprint(_principal(), "t", "a", None) == request_fingerprint(
        _principal(), "t", "a", {}
    )


def test_the_accountable_human() -> None:
    assert accountable_human(_principal("u-1")) == {"id": "u-1", "issuer": "corp-idp"}
    assert accountable_human(_principal(None, "alice")) == {"id": "alice", "issuer": None}
    nobody = _principal(None, None)
    assert accountable_human(nobody) == {"id": None, "issuer": None}
    assert not has_accountable_human(nobody)


def test_hashes_are_prefixed_and_stable() -> None:
    assert parameter_hash({"a": 1}) == sha256_ref({"a": 1})
    assert principal_digest(_principal()).startswith("sha256:")
    assert principal_digest(_principal("u-1")) != principal_digest(_principal("u-2"))


# Messages


def _ask() -> AskRequested:
    return AskRequested(
        contract_version="0.2",
        type="ask.requested",
        event_id="evt-1",
        timestamp="2026-11-23T10:00:00Z",
        source="demo-mcp",
        request_fingerprint=HASH,
        principal=_principal(),
        boundary="mcp",
        tool="broker.order",
        action="POST /orders",
        parameter_hash=HASH,
        parameters={"value": 100},
        body_ref=None,
        policy_version=3,
        trace_id=None,
        linked_to=None,
    )


def test_a_message_round_trips_as_canonical_json() -> None:
    message = _ask()
    data = encode(message)
    assert data == canonical(json.loads(data))
    assert decode(data) == message


def _messages() -> list[dict[str, Any]]:
    base = {
        "contract_version": "0.2",
        "event_id": "e-1",
        "timestamp": "2026-11-23T10:00:00Z",
        "source": "demo-mcp",
    }
    principal = _principal().model_dump(mode="json")
    return [
        {
            **base,
            "type": "call.denied",
            "principal": principal,
            "boundary": "mcp",
            "tool": "t",
            "action": "a",
            "parameters": None,
            "binding_id": None,
            "policy_version": None,
            "reason": "no binding",
        },
        {
            **base,
            "type": "grant.used",
            "request_fingerprint": HASH,
            "grant_id": "g-1",
            "outcome": "accepted",
        },
        {
            **base,
            "type": "request.executed",
            "request_fingerprint": HASH,
            "grant_id": None,
            "outcome": "success",
            "latency_ms": 12,
        },
        {**base, "type": "request.expired", "request_fingerprint": HASH, "reason": "ttl"},
        {**base, "type": "inventory.reported", "inventory": {"agents": []}},
        {
            **base,
            "type": "limit.near",
            "principal": principal,
            "tool": "t",
            "limit": "max_order_value",
            "limit_value": 50000,
            "observed": 49000.5,
        },
        {
            **base,
            "type": "policy.published",
            "policy_version": 4,
            "bundle": {"policy_version": 4},
            "ask_trigger_rules": [],
            "author": "alice",
            "approvers": ["bob"],
            "effective_date": "2026-11-24T00:00:00Z",
        },
        {
            **base,
            "type": "decision.resolved",
            "request_fingerprint": HASH,
            "outcome": "reject",
            "approver": "bob",
            "rationale_ref": None,
            "grant": None,
        },
        {
            **base,
            "type": "decision.resolved",
            "request_fingerprint": HASH,
            "outcome": "approve",
            "approver": "bob",
            "rationale_ref": "r-1",
            "grant": "a.b.c",
        },
        {
            **base,
            "type": "ledger.entry",
            "entry": {
                "event_id": "e-1",
                "timestamp": "2026-11-23T10:00:00Z",
                "type": "decision",
                "prev_hash": "0" * 64,
                "hash": "1" * 64,
            },
        },
    ]


@pytest.mark.parametrize("data", _messages(), ids=lambda d: f"{d['type']}-{d.get('outcome', '')}")
def test_every_message_type_is_read(data: dict[str, Any]) -> None:
    message = decode(json.dumps(data).encode())
    assert message.type == data["type"]
    assert decode(encode(message)) == message


def test_other_versions_are_refused_naming_the_supported_one() -> None:
    data = json.loads(encode(_ask()))
    data["contract_version"] = "0.1"
    with pytest.raises(ContractError, match=r"0\.2"):
        decode(json.dumps(data).encode())


def test_unknown_fields_and_types_are_refused() -> None:
    data = json.loads(encode(_ask()))
    with pytest.raises(ContractError):
        decode(json.dumps({**data, "extra": 1}).encode())
    with pytest.raises(ContractError):
        decode(json.dumps({**data, "type": "ask.invented"}).encode())


@pytest.mark.parametrize(
    "raw", [b"not json", b'["a"]', b'{"contract_version": "0.2", "x": NaN}', b"\xff"]
)
def test_unreadable_bodies_are_refused(raw: bytes) -> None:
    with pytest.raises(ContractError):
        decode(raw)


def test_only_an_approval_carries_a_grant() -> None:
    approve, reject = _messages()[8], _messages()[7]
    bad_approve = deepcopy(approve)
    bad_approve["grant"] = None
    bad_reject = deepcopy(reject)
    bad_reject["grant"] = "a.b.c"
    for data in (bad_approve, bad_reject):
        with pytest.raises(ContractError):
            decode(json.dumps(data).encode())
    assert isinstance(decode(json.dumps(approve).encode()), DecisionResolved)


def test_a_bad_event_id_or_timestamp_is_refused() -> None:
    data = json.loads(encode(_ask()))
    for field, value in (("event_id", "has space"), ("timestamp", "2026-11-23 10:00")):
        with pytest.raises(ContractError):
            decode(json.dumps({**data, field: value}).encode())


# Signing


def test_a_signed_request_verifies() -> None:
    body = b'{"a":1}'
    headers = sign("post", "/v1/events", body, "ep-1", KEYS["ep-1"], now=NOW)
    assert verify("POST", "/v1/events", body, headers, KEYS, now=NOW) == "ep-1"
    lower = {key.lower(): value for key, value in headers.items()}
    assert verify("POST", "/v1/events", body, lower, KEYS, now=NOW + 10) == "ep-1"


@pytest.mark.parametrize(
    ("method", "path", "body"),
    [("GET", "/v1/events", b"x"), ("POST", "/v1/other", b"x"), ("POST", "/v1/events", b"y")],
)
def test_changing_method_path_or_body_breaks_the_signature(
    method: str, path: str, body: bytes
) -> None:
    headers = sign("POST", "/v1/events", b"x", "ep-1", KEYS["ep-1"], now=NOW)
    with pytest.raises(SignatureError, match="signature"):
        verify(method, path, body, headers, KEYS, now=NOW)


def test_a_wrong_or_unknown_key_is_refused() -> None:
    headers = sign("POST", "/v1/events", b"x", "ep-1", b"another-key", now=NOW)
    with pytest.raises(SignatureError, match="signature"):
        verify("POST", "/v1/events", b"x", headers, KEYS, now=NOW)
    headers = sign("POST", "/v1/events", b"x", "ep-9", KEYS["ep-1"], now=NOW)
    with pytest.raises(SignatureError, match="unknown key"):
        verify("POST", "/v1/events", b"x", headers, KEYS, now=NOW)


def test_five_minutes_of_skew_is_the_limit() -> None:
    headers = sign("POST", "/p", b"", "ep-1", KEYS["ep-1"], now=NOW)
    assert verify("POST", "/p", b"", headers, KEYS, now=NOW + MAX_SKEW_SECONDS) == "ep-1"
    for clock in (NOW + MAX_SKEW_SECONDS + 1, NOW - MAX_SKEW_SECONDS - 1):
        with pytest.raises(SignatureError, match=r"5 minutes"):
            verify("POST", "/p", b"", headers, KEYS, now=clock)


def test_missing_and_malformed_headers_are_refused() -> None:
    headers = sign("POST", "/p", b"", "ep-1", KEYS["ep-1"], now=NOW)
    for name in list(headers):
        partial = {key: value for key, value in headers.items() if key != name}
        with pytest.raises(SignatureError, match="not signed"):
            verify("POST", "/p", b"", partial, KEYS, now=NOW)
    with pytest.raises(SignatureError, match="whole seconds"):
        verify("POST", "/p", b"", {**headers, "Ritvo-Timestamp": "yesterday"}, KEYS, now=NOW)
    with pytest.raises(SignatureError):
        verify("POST", "/p", b"", {**headers, HEADER_SIGNATURE: "zzé"}, KEYS, now=NOW)
