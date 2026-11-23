"""An issuer's signing keys, fetched and cached (docs/SPEC.md section 5.10)."""

import json
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

import httpx
import jwt

FETCH_TIMEOUT_SECONDS = 5
CACHE_SECONDS = 600
REFETCH_SECONDS = 60


class JwksError(Exception):
    """The key set could not be fetched or read."""


def fetch_url(url: str) -> bytes:
    """Read a key set from `https://` or, for development, `file://`."""
    parsed = urlparse(url)
    if parsed.scheme == "file":
        try:
            return Path(unquote(parsed.path)).read_bytes()
        except OSError:
            raise JwksError("the key set file could not be read") from None
    if parsed.scheme != "https":
        raise JwksError("a key set is read from https:// or file://")
    try:
        response = httpx.get(url, timeout=FETCH_TIMEOUT_SECONDS, follow_redirects=False)
        response.raise_for_status()
    except httpx.HTTPError:
        raise JwksError("the key set could not be fetched") from None
    return response.content


class JwksSource:
    """The keys of one issuer. An unknown `kid` causes one new fetch, at most once a minute."""

    def __init__(
        self,
        url: str,
        fetch: Callable[[str], bytes] = fetch_url,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._url = url
        self._fetch = fetch
        self._clock = clock
        self._keys: dict[str, Any] = {}
        self._fetched_at: float | None = None

    def _load(self) -> None:
        data = self._fetch(self._url)
        try:
            parsed = json.loads(data.decode("utf-8"))
            if not isinstance(parsed, dict) or not isinstance(parsed.get("keys"), list):
                raise ValueError
            keyset = jwt.PyJWKSet.from_dict(parsed)
        except (UnicodeDecodeError, ValueError, jwt.PyJWTError):
            raise JwksError("the key set is not valid") from None
        self._keys = {key.key_id: key for key in keyset.keys if key.key_id}
        self._fetched_at = self._clock()

    def key_for(self, kid: str) -> Any:
        """The key for a `kid`, or None. Raises JwksError when no key set can be had."""
        now = self._clock()
        age = None if self._fetched_at is None else now - self._fetched_at
        if (
            age is None
            or age >= CACHE_SECONDS
            or (kid not in self._keys and age >= REFETCH_SECONDS)
        ):
            self._load()
        return self._keys.get(kid)
