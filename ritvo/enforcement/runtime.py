"""Build an enforcement point from ritvo.yaml (docs/SPEC.md sections 5.12 and 6.5)."""

import json
import logging
import os
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import httpx
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from ritvo.adapter_contract.bundle import trusted_keys
from ritvo.adapter_contract.messages import Message, encode
from ritvo.adapter_contract.signing import sign
from ritvo.config.load import load_config
from ritvo.config.schema import ClaimsMap, Config
from ritvo.enforcement.bundles.fetch import fetch_latest
from ritvo.enforcement.bundles.store import BundleStore
from ritvo.enforcement.evaluate import Evaluator
from ritvo.enforcement.identity.jwks import JwksSource
from ritvo.enforcement.identity.tokens import Issuer, TokenValidator
from ritvo.ledger.redact import REDACTOR, RedactingFilter
from ritvo.ledger.store import Ledger
from ritvo.secrets.access import Secrets
from ritvo.secrets.base import SecretError
from ritvo.secrets.providers import from_backend

log = logging.getLogger("ritvo.enforcement")
REFRESH_INTERVAL_SECONDS = 30
ENV_FILE_VARIABLE = "RITVO_ENV_FILE"


class RuntimeBuildError(Exception):
    """An enforcement point cannot be built from its config."""


def open_secrets(config: Config) -> Secrets:
    assert config.secrets is not None  # noqa: S101 - a complete config has one
    env_file = os.environ.get(ENV_FILE_VARIABLE)
    provider = from_backend(config.secrets.backend, Path(env_file) if env_file else None)
    return Secrets(provider)


def read_keys(path: Path) -> dict[str, Ed25519PublicKey]:
    """The trusted bundle-signing keys. A missing or unreadable file trusts nothing."""
    try:
        return dict(trusted_keys(json.loads(path.read_text(encoding="utf-8"))))
    except (OSError, ValueError):
        return {}


@dataclass
class Runtime:
    config: Config
    secrets: Secrets
    validator: TokenValidator
    store: BundleStore
    evaluator: Evaluator
    ledger: Ledger
    service: httpx.Client | None
    service_key_id: str | None
    service_key: bytes | None
    _last_refresh: float = 0.0
    _etag: str | None = None

    def send(self, message: Message) -> None:
        """Send an event to the decision service. A failure is logged and never raised."""
        if self.service is None or self.service_key is None or self.service_key_id is None:
            return
        body = encode(message)
        headers = sign("POST", "/v1/events", body, self.service_key_id, self.service_key)
        try:
            self.service.post("/v1/events", content=body, headers=headers).raise_for_status()
        except httpx.HTTPError:
            log.warning("the decision service did not take %s", message.type)

    def refresh(self, force: bool = False) -> bool:
        """Fetch the bundle if stale or missing, at most every 30 seconds. True if replaced."""
        if self.service is None or self.service_key is None or self.service_key_id is None:
            return False
        now = time.monotonic()
        if not force and (
            not self.store.is_stale() or now - self._last_refresh < REFRESH_INTERVAL_SECONDS
        ):
            return False
        self._last_refresh = now
        try:
            fetched = fetch_latest(self.service, self.service_key_id, self.service_key, self._etag)
            if fetched is None:
                return False
            signed, etag = fetched
            self.store.install(signed)
            self._etag = etag
            return True
        except Exception as exc:
            log.warning("the bundle was not refreshed: %s", type(exc).__name__)
            return False

    def close(self) -> None:
        self.ledger.close()
        if self.service is not None:
            self.service.close()


def build_runtime(
    config_path: Path,
    secrets: Secrets | None = None,
    clock: Callable[[], float] = time.time,
) -> Runtime:
    """Everything an enforcement point needs, from a complete ritvo.yaml."""
    config = load_config(config_path)
    assert config.identity is not None and config.mode is not None  # noqa: S101
    secrets = secrets or open_secrets(config)
    base = config_path.parent
    issuers = []
    try:
        for issuer in config.identity.issuers:
            url = (
                secrets.resolve(issuer.jwks_url)
                if issuer.jwks_url.startswith("secret://")
                else issuer.jwks_url
            )
            cmap = issuer.claims_map or config.identity.claims_map
            assert isinstance(cmap, ClaimsMap)  # noqa: S101 - the loader checked it
            issuers.append(
                Issuer(
                    issuer.name,
                    issuer.issuer or issuer.name,
                    issuer.audience,
                    cmap,
                    JwksSource(url),
                )
            )
    except SecretError as exc:
        raise RuntimeBuildError(
            f"a key set address could not be read from the secrets store: {type(exc).__name__}"
        ) from None
    validator = TokenValidator(issuers, config.identity.jwks_leeway_seconds, clock)
    store = BundleStore(read_keys(base / config.bundle.keys), base / config.bundle.path, clock)
    ledger = Ledger(base / config.ledger.path)
    owners = {agent.id: agent.owner for agent in config.agents}
    boundary = config.enforcement[0].boundary
    evaluator = Evaluator(
        mode=config.mode,
        boundary=boundary,
        validator=validator,
        store=store,
        owner_of=lambda agent: owners.get(agent),
        require_owner=config.identity.unattended == "require_owner",
        clock=clock,
    )
    service = key_id = key = None
    if config.decision_service is not None:
        try:
            key = secrets.resolve(config.decision_service.key).encode("utf-8")
        except SecretError as exc:
            raise RuntimeBuildError(
                f"the decision service key could not be read: {type(exc).__name__}"
            ) from None
        key_id = config.decision_service.key_id
        service = httpx.Client(base_url=config.decision_service.url, timeout=5)
    logging.getLogger("ritvo").addFilter(RedactingFilter(REDACTOR))
    return Runtime(config, secrets, validator, store, evaluator, ledger, service, key_id, key)
