"""Hold the current bundle: verify it, refuse an older one, keep it on disk (docs/SPEC.md 4.6)."""

import time
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from ritvo.adapter_contract.bundle import (
    Bundle,
    BundleError,
    SignedBundle,
    encode_signed_bundle,
    read_signed_bundle,
    verify_bundle,
)


class BundleStore:
    """The bundle an enforcement point holds. It starts from its kept file if that verifies."""

    def __init__(
        self,
        keys: Mapping[str, Ed25519PublicKey],
        path: Path | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._keys = keys
        self._path = path
        self._clock = clock
        self._signed: SignedBundle | None = None
        if path is not None and path.is_file():
            try:
                self.install(read_signed_bundle(path.read_bytes()), keep=False)
            except (BundleError, OSError):
                self._signed = None

    @property
    def bundle(self) -> Bundle | None:
        return None if self._signed is None else self._signed.bundle

    def install(self, signed: SignedBundle, keep: bool = True) -> Bundle:
        """Verify and hold a bundle. A lower `policy_version` than the one held is refused."""
        bundle = verify_bundle(signed, self._keys)
        held = self.bundle
        if held is not None:
            if bundle.policy_version < held.policy_version:
                raise BundleError("the bundle is older than the one held")
            if bundle.policy_version == held.policy_version and bundle != held:
                raise BundleError("a different bundle has the same policy_version as the one held")
        self._signed = signed
        if keep and self._path is not None:
            temp = self._path.with_name(self._path.name + ".tmp")
            temp.write_bytes(encode_signed_bundle(signed))
            temp.replace(self._path)
        return bundle

    def is_stale(self) -> bool:
        """No bundle counts as stale. A bundle is stale `max_age_minutes` after it was issued."""
        bundle = self.bundle
        if bundle is None:
            return True
        issued = datetime.strptime(bundle.issued_at.split(".")[0].rstrip("Z"), "%Y-%m-%dT%H:%M:%S")
        age_minutes = (self._clock() - issued.replace(tzinfo=UTC).timestamp()) / 60
        return age_minutes > bundle.max_age_minutes
