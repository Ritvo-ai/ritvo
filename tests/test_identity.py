import json
from collections.abc import Callable
from typing import Any

import jwt
import pytest
from conftest import AUDIENCE, ISS, NOW, Idp

from ritvo.adapter_contract.canonical import sha256_hex
from ritvo.config.schema import ClaimsMap
from ritvo.enforcement.identity.jwks import JwksError, JwksSource
from ritvo.enforcement.identity.principal import build_principal, dig, widened
from ritvo.enforcement.identity.tokens import Issuer, TokenError, TokenValidator

CLAIMS = ClaimsMap(
    subject="sub", actor="act.sub", scopes="scope", actor_platform="plat", chain="ritvo_chain"
)


def _validator(idp: Idp, **kwargs: Any) -> TokenValidator:
    issuer = Issuer("corp-idp", ISS, AUDIENCE, CLAIMS, JwksSource(idp.url))
    return TokenValidator([issuer], clock=lambda: NOW, **kwargs)


def _code(validator: TokenValidator, token: str) -> str:
    with pytest.raises(TokenError) as error:
        validator.validate(token)
    return error.value.code


@pytest.mark.parametrize("kid", ["k-rsa", "k-ec", "k-ed"])
def test_a_good_token_validates_for_each_algorithm(idp: Idp, kid: str) -> None:
    token = idp.mint(kid)
    validated = _validator(idp).validate(token)
    assert validated.claims["sub"] == "u-1"
    assert validated.token_ref == "sha256:" + sha256_hex(token.encode())
    assert validated.issuer.name == "corp-idp"


def test_malformed_tokens(idp: Idp) -> None:
    validator = _validator(idp)
    assert _code(validator, "not-a-jwt") == "malformed"
    assert _code(validator, "") == "malformed"
    no_kid = jwt.encode(
        {"iss": ISS, "aud": AUDIENCE, "exp": int(NOW) + 60}, idp.keys["k-ed"], algorithm="EdDSA"
    )
    assert _code(validator, no_kid) == "malformed"
    no_exp = idp.mint(exp=None)
    assert _code(validator, no_exp) == "malformed"


def test_an_unknown_issuer(idp: Idp) -> None:
    assert _code(_validator(idp), idp.mint(iss="https://evil.example.com")) == "unknown_issuer"


def test_other_algorithms_are_refused(idp: Idp) -> None:
    validator = _validator(idp)
    body = {"iss": ISS, "aud": AUDIENCE, "sub": "u", "exp": int(NOW) + 60}
    hs = jwt.encode(
        body,
        "a-shared-secret-of-sufficient-length-1234",
        algorithm="HS256",
        headers={"kid": "k-rsa"},
    )
    assert _code(validator, hs) == "unsupported_alg"
    none = jwt.encode(body, None, algorithm="none", headers={"kid": "k-ed"})  # type: ignore[arg-type]
    assert _code(validator, none) == "unsupported_alg"
    ps = jwt.encode(body, idp.keys["k-rsa"], algorithm="PS256", headers={"kid": "k-rsa"})
    assert _code(validator, ps) == "unsupported_alg"


def test_the_algorithm_confusion_attack_is_refused(idp: Idp) -> None:
    """A token signed HS256 with the public key as the secret must not be accepted."""
    import base64
    import hashlib
    import hmac

    from cryptography.hazmat.primitives import serialization

    public = (
        idp.keys["k-rsa"]
        .public_key()
        .public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
    )

    def part(value: dict[str, Any]) -> str:
        return base64.urlsafe_b64encode(json.dumps(value).encode()).rstrip(b"=").decode()

    signing_input = (
        part({"alg": "HS256", "typ": "JWT", "kid": "k-rsa"})
        + "."
        + part({"iss": ISS, "aud": AUDIENCE, "sub": "u", "exp": int(NOW) + 60})
    )
    mac = hmac.new(public, signing_input.encode(), hashlib.sha256).digest()
    forged = signing_input + "." + base64.urlsafe_b64encode(mac).rstrip(b"=").decode()
    assert _code(_validator(idp), forged) == "unsupported_alg"


def test_a_bad_signature_or_unknown_key(idp: Idp) -> None:
    validator = _validator(idp)
    token = idp.mint("k-ed")
    head, body, _sig = token.split(".")
    assert _code(validator, f"{head}.{body}.AAAA") == "bad_signature"
    swapped = jwt.encode(
        {"iss": ISS, "aud": AUDIENCE, "sub": "u", "exp": int(NOW) + 60},
        idp.keys["k-rsa"],
        algorithm="RS256",
        headers={"kid": "k-ec"},
    )
    assert _code(validator, swapped) == "bad_signature"
    unknown = jwt.encode(
        {"iss": ISS, "aud": AUDIENCE, "exp": int(NOW) + 60},
        idp.keys["k-ed"],
        algorithm="EdDSA",
        headers={"kid": "nope"},
    )
    assert _code(validator, unknown) == "bad_signature"


def test_expiry_with_leeway(idp: Idp) -> None:
    validator = _validator(idp)
    assert _code(validator, idp.mint(exp=int(NOW) - 100)) == "expired"
    validator.validate(idp.mint(exp=int(NOW) - 30))
    assert _code(validator, idp.mint(nbf=int(NOW) + 100)) == "expired"
    assert _code(_validator(idp, leeway_seconds=0), idp.mint(exp=int(NOW) - 1)) == "expired"


def test_audience(idp: Idp) -> None:
    validator = _validator(idp)
    assert _code(validator, idp.mint(aud="other-mcp")) == "wrong_audience"
    assert _code(validator, idp.mint(aud=["a", "b"])) == "wrong_audience"
    validator.validate(idp.mint(aud=["agent-platform", AUDIENCE]))
    assert _code(validator, idp.mint(aud="agent-platform")) == "wrong_audience"


def test_an_error_never_carries_the_token(idp: Idp) -> None:
    token = idp.mint(aud="other-mcp")
    with pytest.raises(TokenError) as error:
        _validator(idp).validate(token)
    assert token not in str(error.value) and error.value.code == "wrong_audience"


# The key set


def test_keys_are_cached_and_an_unknown_kid_refetches_at_most_once_a_minute(idp: Idp) -> None:
    now = [0.0]
    fetched: list[str] = []

    def fetch(url: str) -> bytes:
        fetched.append(url)
        return idp.file.read_bytes()

    source = JwksSource("https://idp.example.com/keys", fetch, lambda: now[0])
    assert source.key_for("k-ed") is not None
    assert source.key_for("k-ec") is not None
    assert len(fetched) == 1
    assert source.key_for("new") is None and len(fetched) == 1
    now[0] = 61
    assert source.key_for("new") is None and len(fetched) == 2
    assert source.key_for("new") is None and len(fetched) == 2
    now[0] = 61 + 601
    assert source.key_for("k-ed") is not None and len(fetched) == 3


def test_a_rotated_in_key_is_found_after_a_refetch(idp: Idp) -> None:
    now = [0.0]
    idp.publish(["k-ed"])
    source = JwksSource(idp.url, clock=lambda: now[0])
    assert source.key_for("k-rsa") is None
    idp.publish(["k-ed", "k-rsa"])
    now[0] = 61
    assert source.key_for("k-rsa") is not None


def _serving(data: bytes) -> Callable[[str], bytes]:
    def fetch(url: str) -> bytes:
        return data

    return fetch


def test_keys_that_cannot_be_had_stop_validation(idp: Idp) -> None:
    def broken(url: str) -> bytes:
        raise JwksError("down")

    issuer = Issuer("corp-idp", ISS, AUDIENCE, CLAIMS, JwksSource("https://x", broken))
    assert _code(TokenValidator([issuer], clock=lambda: NOW), idp.mint()) == "bad_signature"
    for bad in (b"not json", b'{"keys": "x"}', b"[]", b'{"keys": [{"kty": "nope"}]}'):
        source = JwksSource("https://x", _serving(bad))
        with pytest.raises(JwksError):
            source.key_for("k")


def test_only_https_and_file_urls_are_read() -> None:
    from ritvo.enforcement.identity.jwks import fetch_url

    for url in ("http://idp.example.com/keys", "ftp://x/y", "/etc/passwd"):
        with pytest.raises(JwksError):
            fetch_url(url)
    with pytest.raises(JwksError):
        fetch_url("file:///does/not/exist.json")


# The principal


def _principal(idp: Idp, owner: str | None = "alice", **claims: Any):  # type: ignore[no-untyped-def]
    validated = _validator(idp).validate(idp.mint(**claims))
    return build_principal(validated, "mcp", lambda agent: owner), validated


def test_an_attended_principal(idp: Idp) -> None:
    (principal, flags), validated = _principal(idp, plat="agentcore")
    assert principal.mode == "attended"
    assert (principal.subject.id, principal.subject.issuer) == ("u-1", "corp-idp")
    assert principal.actor.id == "acme.research" and principal.actor.platform == "agentcore"
    assert principal.owner == "alice" and flags == []
    hop = principal.chain[0]
    assert (hop.boundary, hop.audience, hop.scopes) == (
        "mcp",
        AUDIENCE,
        ["orders:read", "orders:write"],
    )
    assert hop.token_ref == validated.token_ref


def test_an_unattended_principal_is_the_agent_itself(idp: Idp) -> None:
    (principal, flags), _ = _principal(idp, sub="acme.research", act=None)
    assert principal.mode == "unattended" and principal.subject is None
    assert principal.actor.id == "acme.research" and principal.owner == "alice" and flags == []


def test_an_unattended_agent_with_no_owner_is_flagged(idp: Idp) -> None:
    (principal, flags), _ = _principal(idp, owner=None, sub="acme.reporting", act=None)
    assert principal.owner is None and flags == ["no_owner", "no_accountable_human"]


def test_an_attended_request_with_no_owner_is_not_flagged(idp: Idp) -> None:
    (_, flags), _ = _principal(idp, owner=None)
    assert flags == []


def test_scopes_as_a_list(idp: Idp) -> None:
    (principal, _), _ = _principal(idp, scope=["a", "b"])
    assert principal.chain[0].scopes == ["a", "b"]
    (principal, _), _ = _principal(idp, scope=None)
    assert principal.chain[0].scopes == []


def test_a_missing_subject_refuses_the_token(idp: Idp) -> None:
    with pytest.raises(TokenError) as error:
        _principal(idp, sub=None)
    assert error.value.code == "malformed"


HOP = {
    "boundary": "edge",
    "audience": "agent-platform",
    "scopes": ["orders:read"],
    "token_ref": "sha256:" + "ab" * 32,
}


def test_earlier_hops_come_first_and_widening_is_flagged(idp: Idp) -> None:
    (principal, flags), _ = _principal(idp, ritvo_chain=[HOP])
    assert [hop.boundary for hop in principal.chain] == ["edge", "mcp"]
    assert flags == ["scope_widened"]


def test_narrowing_is_not_widening(idp: Idp) -> None:
    wide = {**HOP, "scopes": ["orders:read", "orders:write", "admin"]}
    (_, flags), _ = _principal(idp, ritvo_chain=[wide])
    assert flags == []
    same = {**HOP, "scopes": ["orders:read", "orders:write"]}
    assert _principal(idp, ritvo_chain=[same])[0][1] == []


@pytest.mark.parametrize(
    "chain",
    ["not a list", [{"boundary": "edge"}], [{**HOP, "token_ref": "a.b.c"}], [{**HOP, "extra": 1}]],
)
def test_a_chain_that_is_not_usable_refuses_the_token(idp: Idp, chain: object) -> None:
    with pytest.raises(TokenError) as error:
        _principal(idp, ritvo_chain=chain)
    assert error.value.code == "malformed"


def test_the_principal_never_holds_the_token(idp: Idp) -> None:
    token = idp.mint()
    validated = _validator(idp).validate(token)
    principal, _ = build_principal(validated, "mcp", lambda agent: None)
    assert token not in json.dumps(principal.model_dump(mode="json"))


def test_dig_and_widened() -> None:
    assert dig({"a": {"b": 1}}, "a.b") == 1
    assert dig({"a": 1}, "a.b") is None and dig({}, "a") is None
    assert not widened([])


def test_a_file_url_works_with_a_windows_drive_path() -> None:
    """`file:///C:/keys/jwks.json` has the path `/C:/keys/jwks.json`; the drive must be kept."""
    from urllib.request import url2pathname

    assert url2pathname("/C:/keys/jwks.json").replace("\\", "/").endswith("keys/jwks.json")
    assert url2pathname("/keys/a%20b.json").endswith("a b.json")
