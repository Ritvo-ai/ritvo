import keyring
import keyring.errors
import pytest
from keyring.backend import KeyringBackend


class MemoryKeyring(KeyringBackend):
    """A keyring held in memory, so no test touches the real one."""

    priority = 1

    def __init__(self) -> None:
        super().__init__()  # type: ignore[no-untyped-call]
        self._values: dict[tuple[str, str], str] = {}

    def get_password(self, service: str, username: str) -> str | None:
        return self._values.get((service, username))

    def set_password(self, service: str, username: str, password: str) -> None:
        self._values[(service, username)] = password

    def delete_password(self, service: str, username: str) -> None:
        if (service, username) not in self._values:
            raise keyring.errors.PasswordDeleteError("not set")
        del self._values[(service, username)]


@pytest.fixture(autouse=True)
def memory_keyring() -> object:
    """Every test uses an in-memory keyring and puts the original back."""
    previous = keyring.get_keyring()
    backend = MemoryKeyring()
    keyring.set_keyring(backend)
    yield backend
    keyring.set_keyring(previous)


# A fake identity provider and a complete policy stack, shared by the phase 1 tests.

import json  # noqa: E402
import time  # noqa: E402
from collections.abc import Callable  # noqa: E402
from dataclasses import dataclass  # noqa: E402
from datetime import UTC, datetime  # noqa: E402
from pathlib import Path  # noqa: E402
from typing import Any  # noqa: E402

import jwt  # noqa: E402
from cryptography.hazmat.primitives.asymmetric import ec, rsa  # noqa: E402
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey  # noqa: E402
from jwt.algorithms import ECAlgorithm, OKPAlgorithm, RSAAlgorithm  # noqa: E402

from ritvo.adapter_contract.bundle import (  # noqa: E402
    SignedBundle,
    jwks_of,
    new_seed,
    private_key,
    trusted_keys,
)
from ritvo.config.load import load_config  # noqa: E402
from ritvo.config.schema import ClaimsMap, Config  # noqa: E402
from ritvo.enforcement.bundles.store import BundleStore  # noqa: E402
from ritvo.enforcement.evaluate import Evaluator  # noqa: E402
from ritvo.enforcement.identity.jwks import JwksSource  # noqa: E402
from ritvo.enforcement.identity.tokens import Issuer, TokenValidator  # noqa: E402
from ritvo.governance_os.routing.publish import publish_bundle  # noqa: E402
from ritvo.secrets.access import Secrets  # noqa: E402
from ritvo.secrets.providers import MemoryProvider  # noqa: E402

NOW = 1_800_000_000.0
ISS = "https://idp.example.com"
AUDIENCE = "demo-mcp"
EXAMPLE = Path(__file__).resolve().parents[1] / "examples" / "trading-demo" / "ritvo.yaml"


class Idp:
    """A fake identity provider with an RSA, an EC and an Ed25519 key."""

    def __init__(self, directory: Path) -> None:
        self.keys: dict[str, Any] = {
            "k-rsa": rsa.generate_private_key(public_exponent=65537, key_size=2048),
            "k-ec": ec.generate_private_key(ec.SECP256R1()),
            "k-ed": Ed25519PrivateKey.generate(),
        }
        self.algs = {"k-rsa": "RS256", "k-ec": "ES256", "k-ed": "EdDSA"}
        self.file = directory / "jwks.json"
        self.publish(list(self.keys))

    def publish(self, kids: list[str]) -> None:
        jwks = []
        for kid in kids:
            public = self.keys[kid].public_key()
            algorithm = {"RS256": RSAAlgorithm, "ES256": ECAlgorithm, "EdDSA": OKPAlgorithm}[
                self.algs[kid]
            ]
            jwk = json.loads(algorithm.to_jwk(public))  # type: ignore[attr-defined]
            jwk.update({"kid": kid, "use": "sig", "alg": self.algs[kid]})
            jwks.append(jwk)
        self.file.write_text(json.dumps({"keys": jwks}), encoding="utf-8")

    @property
    def url(self) -> str:
        return self.file.as_uri()

    def mint(self, kid: str = "k-ed", **claims: Any) -> str:
        body: dict[str, Any] = {
            "iss": ISS,
            "aud": AUDIENCE,
            "sub": "u-1",
            "act": {"sub": "acme.research"},
            "scope": "orders:read orders:write",
            "iat": int(NOW),
            "exp": int(NOW) + 3600,
        }
        body.update(claims)
        body = {key: value for key, value in body.items() if value is not None}
        return jwt.encode(body, self.keys[kid], algorithm=self.algs[kid], headers={"kid": kid})


@dataclass
class Stack:
    """Everything the policy path needs, built the way the commands will build it."""

    config: Config
    idp: Idp
    signed: SignedBundle
    seed: str
    secrets: Secrets
    clock: list[float]

    def validator(self) -> TokenValidator:
        issuer = self.config.identity.issuers[0]  # type: ignore[union-attr]
        claims = issuer.claims_map or self.config.identity.claims_map  # type: ignore[union-attr]
        assert isinstance(claims, ClaimsMap)
        return TokenValidator(
            [
                Issuer(
                    issuer.name,
                    issuer.issuer or ISS,
                    issuer.audience,
                    claims,
                    JwksSource(self.idp.url),
                )
            ],
            clock=lambda: self.clock[0],
        )

    def store(self, signed: SignedBundle | None = None) -> BundleStore:
        keys = trusted_keys(jwks_of(private_key(self.seed).public_key()))
        store = BundleStore(keys, clock=lambda: self.clock[0])
        store.install(signed or self.signed)
        return store

    def evaluator(self, mode: str = "enforce", store: BundleStore | None = None) -> Evaluator:
        owners = {agent.id: agent.owner for agent in self.config.agents}
        return Evaluator(
            mode=mode,  # type: ignore[arg-type]
            boundary="mcp",
            validator=self.validator(),
            store=store or self.store(),
            owner_of=lambda agent: owners.get(agent),
            clock=lambda: self.clock[0],
        )


def make_stack(
    directory: Path, edit: Callable[[dict[str, Any]], None] | None = None, **config_changes: Any
) -> Stack:
    import yaml

    data = yaml.safe_load(EXAMPLE.read_text(encoding="utf-8"))
    idp = Idp(directory)
    data["identity"]["issuers"][0]["issuer"] = ISS
    data["identity"]["issuers"][0]["jwks_url"] = idp.url
    data["agents"].append({"id": "acme.reporting", "owner": None, "owner_team": "ops"})
    data["tools"].append(
        {"id": "broker.quote", "tier": "low", "match": {"method": "GET", "path": "/quotes/*"}}
    )
    data["tools"].append(
        {"id": "ledger.adjust", "tier": "medium", "match": {"method": "POST", "path": "/ledger/*"}}
    )
    data["bindings"] += [
        {"agent": "acme.research", "tool": "broker.quote", "decision": "allow"},
        {"agent": "acme.research", "tool": "ledger.adjust", "decision": "allow"},
        {
            "agent": "acme.reporting",
            "tool": "broker.quote",
            "decision": "allow",
            "limits": {"max_depth": 10},
        },
    ]
    data.update(config_changes)
    if edit is not None:
        edit(data)
    path = directory / "ritvo.yaml"
    import yaml as _yaml

    path.write_text(_yaml.safe_dump(data), encoding="utf-8")
    config = load_config(path)
    secrets = Secrets(MemoryProvider())
    seed = new_seed()
    secrets.set("ritvo/bundle-signing-key", seed)
    clock = [NOW]
    signed = publish_bundle(config, secrets, 1, datetime.fromtimestamp(NOW, UTC))
    return Stack(config, idp, signed, seed, secrets, clock)


@pytest.fixture
def idp(tmp_path: Path) -> Idp:
    return Idp(tmp_path)


@pytest.fixture
def stack(tmp_path: Path) -> Stack:
    return make_stack(tmp_path)


def fixed_time() -> float:
    return time.time()
