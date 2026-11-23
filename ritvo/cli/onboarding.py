"""ritvo init, apply and check (docs/SPEC.md section 6.5)."""

import json
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import yaml
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from ritvo.adapter_contract.bundle import (
    SIGNING_KEY_PATH,
    BundleError,
    SignedBundle,
    encode_signed_bundle,
    key_id_of,
    new_seed,
    private_key,
    public_jwk,
    read_signed_bundle,
    trusted_keys,
    verify_bundle,
)
from ritvo.config.load import ConfigError, load_config, parse_config
from ritvo.enforcement.bundles.engine import Engine, EngineError
from ritvo.enforcement.identity.jwks import JwksError, fetch_url
from ritvo.enforcement.runtime import open_secrets
from ritvo.governance_os.routing.compile import CompileError, compile_bundle
from ritvo.governance_os.routing.publish import publish_bundle
from ritvo.ledger.event import LedgerEvent
from ritvo.ledger.store import Ledger
from ritvo.secrets.access import Secrets
from ritvo.secrets.base import SecretError, SecretNotFound, parse_ref

DEFAULT_CLAIMS = {"subject": "sub", "actor": "act.sub", "scopes": "scope"}
DEFAULT_LADDERS = {"high": ["escalation_reviewer"], "timeout_minutes": 30}


class OnboardingError(Exception):
    """A command cannot go on. Every problem is listed and nothing is written."""

    def __init__(self, problems: list[str]) -> None:
        super().__init__("; ".join(problems))
        self.problems = problems


def _write_atomically(path: Path, data: bytes) -> None:
    temp = path.with_name(path.name + ".tmp")
    temp.write_bytes(data)
    temp.replace(path)


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


# init


@dataclass(frozen=True)
class InitResult:
    out: Path
    key_id: str
    key_made: bool


def init_config(
    draft: Path,
    out: Path,
    issuer: str,
    audience: str,
    jwks_url: str,
    name: str = "idp",
    enforcement_id: str = "mcp-server",
    backend: str = "keyring",
) -> InitResult:
    """Complete a draft ritvo.yaml for observe mode and make the signing key if there is none."""
    if out.exists():
        raise OnboardingError([f"{out} already exists"])
    try:
        data = yaml.safe_load(draft.read_bytes().decode("utf-8"))
    except (OSError, UnicodeDecodeError, yaml.YAMLError):
        raise OnboardingError([f"{draft.name} cannot be read as YAML"]) from None
    if not isinstance(data, dict):
        raise OnboardingError([f"{draft.name} must be a YAML mapping"])
    data = dict(data)
    data.setdefault("mode", "observe")
    data.setdefault("profile", "local")
    data.setdefault(
        "enforcement", [{"id": enforcement_id, "type": "mcp_middleware", "boundary": "mcp"}]
    )
    data.setdefault(
        "identity",
        {
            "issuers": [
                {"name": name, "issuer": issuer, "jwks_url": jwks_url, "audience": audience}
            ],
            "claims_map": dict(DEFAULT_CLAIMS),
            "unattended": "require_owner",
        },
    )
    data.setdefault("secrets", {"backend": backend})
    data.setdefault("ladders", dict(DEFAULT_LADDERS))
    try:
        config = parse_config(data)
    except ConfigError as exc:
        raise OnboardingError(exc.problems) from None
    secrets = open_secrets(config)
    try:
        key_made = False
        try:
            seed = secrets.get(SIGNING_KEY_PATH)
        except SecretNotFound:
            seed = new_seed()
            secrets.set(SIGNING_KEY_PATH, seed)
            key_made = True
        key_id = key_id_of(private_key(seed).public_key())
    except (SecretError, BundleError) as exc:
        raise OnboardingError(
            [f"the signing key cannot be made or read: {type(exc).__name__}"]
        ) from None
    with out.open("x", encoding="utf-8", newline="\n") as handle:
        handle.write(yaml.safe_dump(data, sort_keys=False, allow_unicode=True))
    return InitResult(out, key_id, key_made)


# apply


@dataclass(frozen=True)
class ApplyResult:
    version: int
    key_id: str
    bundle_path: Path
    keys_path: Path


def _read_jwks(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return (
            data if isinstance(data, dict) and isinstance(data.get("keys"), list) else {"keys": []}
        )
    except (OSError, ValueError):
        return {"keys": []}


def held_version(bundle_path: Path, keys: dict[str, Ed25519PublicKey]) -> int:
    """The version of the bundle on disk if it verifies, otherwise 0."""
    try:
        return verify_bundle(read_signed_bundle(bundle_path.read_bytes()), keys).policy_version
    except (OSError, BundleError):
        return 0


def apply_config(config_path: Path, secrets: Secrets | None = None) -> ApplyResult:
    """Compile and sign the config and write the bundle and the keys it needs."""
    try:
        config = load_config(config_path)
    except ConfigError as exc:
        raise OnboardingError(exc.problems) from None
    secrets = secrets or open_secrets(config)
    base = config_path.parent
    bundle_path, keys_path = base / config.bundle.path, base / config.bundle.keys
    jwks = _read_jwks(keys_path)
    version = held_version(bundle_path, trusted_keys(jwks)) + 1
    try:
        signed = publish_bundle(config, secrets, version)
    except CompileError as exc:
        raise OnboardingError(exc.problems) from None
    except SecretError:
        raise OnboardingError(
            ["there is no signing key in the secrets store; run ritvo init first"]
        ) from None
    public = private_key(secrets.get(SIGNING_KEY_PATH)).public_key()
    key_id = key_id_of(public)
    if all(key.get("kid") != key_id for key in jwks["keys"]):
        jwks["keys"].append(public_jwk(public))
    _write_atomically(keys_path, json.dumps(jwks, indent=2).encode("utf-8") + b"\n")
    _write_atomically(bundle_path, encode_signed_bundle(signed))
    ledger = Ledger(base / config.ledger.path)
    ledger.append(
        LedgerEvent(
            event_id=f"apply-{version}-{key_id}-{int(time.time())}",
            timestamp=_now(),
            type="policy_change",
            policy_version=version,
            outcome="applied",
        )
    )
    ledger.close()
    return ApplyResult(version, key_id, bundle_path, keys_path)


# check


@dataclass(frozen=True)
class Check:
    status: str  # ok, warn or FAIL
    name: str
    detail: str


def _same_policy(held: SignedBundle, fresh: Any) -> bool:
    keys = ("cedar", "entities", "tools", "bindings", "fail_modes")
    return all(getattr(held.bundle, key) == getattr(fresh, key) for key in keys)


def check_config(
    config_path: Path, online: bool = False, secrets: Secrets | None = None
) -> list[Check]:
    checks: list[Check] = []

    def add(status: str, name: str, detail: str) -> None:
        checks.append(Check(status, name, detail))

    try:
        config = load_config(config_path)
    except (ConfigError, OSError) as exc:
        problems = (
            exc.problems if isinstance(exc, ConfigError) else [f"{config_path.name} cannot be read"]
        )
        for problem in problems:
            add("FAIL", "config", problem)
        return checks
    add("ok", "config", f"complete, mode {config.mode}, profile {config.profile}")
    secrets = secrets or open_secrets(config)
    provider = secrets.describe()
    insecure = "not secure" in provider
    add("warn" if insecure else "ok", "secrets", provider)
    refs = [
        i.jwks_url
        for i in (config.identity.issuers if config.identity else [])
        if i.jwks_url.startswith("secret://")
    ]
    if config.decision_service is not None:
        refs.append(config.decision_service.key)
    for ref in refs:
        try:
            secrets.resolve(ref)
            add("ok", "secret", f"{parse_ref(ref)} resolves")
        except SecretError as exc:
            add("FAIL", "secret", f"{ref} cannot be read ({type(exc).__name__})")
    try:
        seed = secrets.get(SIGNING_KEY_PATH)
        add("ok", "signing key", key_id_of(private_key(seed).public_key()))
    except (SecretError, BundleError):
        add(
            "warn",
            "signing key",
            "none here; fine on an enforcement-only host, otherwise run ritvo init",
        )
    base = config_path.parent
    keys_path, bundle_path = base / config.bundle.keys, base / config.bundle.path
    keys = trusted_keys(_read_jwks(keys_path))
    if not keys:
        add("FAIL", "keys", f"{keys_path.name} has no trusted signing key; run ritvo apply")
    else:
        add("ok", "keys", f"{len(keys)} trusted signing key(s)")
    held: SignedBundle | None = None
    try:
        held = read_signed_bundle(bundle_path.read_bytes())
        verify_bundle(held, keys)
        add(
            "ok",
            "bundle",
            f"version {held.bundle.policy_version}, signed by {held.signature.key_id}",
        )
    except OSError:
        add("FAIL", "bundle", f"{bundle_path.name} does not exist; run ritvo apply")
    except BundleError as exc:
        held = None
        add("FAIL", "bundle", str(exc))
    if held is not None:
        issued = datetime.strptime(
            held.bundle.issued_at.split(".")[0].rstrip("Z"), "%Y-%m-%dT%H:%M:%S"
        )
        age = (time.time() - issued.replace(tzinfo=UTC).timestamp()) / 60
        if age > held.bundle.max_age_minutes:
            add(
                "warn",
                "bundle age",
                f"{int(age)} minutes old, older than its {held.bundle.max_age_minutes}",
            )
        try:
            fresh = compile_bundle(config, held.bundle.policy_version)
            if _same_policy(held, fresh):
                add("ok", "policy", "the bundle was compiled from this config")
            else:
                add(
                    "warn", "policy", "ritvo.yaml has changed since the last apply; run ritvo apply"
                )
        except CompileError as exc:
            add("FAIL", "policy", "; ".join(exc.problems))
        try:
            Engine(held.bundle)
            add("ok", "engine", "loads the bundle and passes its self-test")
        except EngineError as exc:
            add("FAIL", "engine", str(exc))
    ledger_path = base / config.ledger.path
    if ledger_path.is_file():
        ledger = Ledger(ledger_path)
        result = ledger.verify()
        ledger.close()
        if result.ok:
            add("ok", "ledger", f"{result.entries} entries verify")
        else:
            add("FAIL", "ledger", f"fails at entry {result.seq}: {result.problem}")
    else:
        add("ok", "ledger", "none yet")
    if online:
        for issuer in config.identity.issuers if config.identity else []:
            try:
                url = (
                    secrets.resolve(issuer.jwks_url)
                    if issuer.jwks_url.startswith("secret://")
                    else issuer.jwks_url
                )
                fetch_url(url)
                add("ok", "issuer", f"{issuer.name}: key set fetched")
            except (JwksError, SecretError):
                add("FAIL", "issuer", f"{issuer.name}: key set cannot be fetched")
        if config.decision_service is not None:
            try:
                httpx.get(
                    config.decision_service.url.rstrip("/") + "/v1/keys", timeout=5
                ).raise_for_status()
                add("ok", "decision service", "answers")
            except httpx.HTTPError:
                add("FAIL", "decision service", "does not answer")
    return checks
