"""Fetch the latest bundle from the decision service (docs/SPEC.md section 6.2)."""

import httpx

from ritvo.adapter_contract.bundle import BundleError, SignedBundle, read_signed_bundle
from ritvo.adapter_contract.signing import sign

BUNDLE_PATH = "/v1/bundles/latest"


class ServiceUnreachable(Exception):
    """The decision service did not answer."""


def fetch_latest(
    client: httpx.Client, key_id: str, key: bytes, etag: str | None = None
) -> tuple[SignedBundle, str] | None:
    """The new bundle and its ETag, or None when the one held is current (304)."""
    headers = sign("GET", BUNDLE_PATH, b"", key_id, key)
    if etag is not None:
        headers["If-None-Match"] = etag
    try:
        response = client.get(BUNDLE_PATH, headers=headers)
    except httpx.HTTPError:
        raise ServiceUnreachable("the decision service did not answer") from None
    if response.status_code == 304:
        return None
    if response.status_code != 200:
        raise BundleError(f"the decision service answered {response.status_code}")
    return read_signed_bundle(response.content), response.headers.get("etag", "")
