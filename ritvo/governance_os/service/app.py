"""The decision service application (docs/SPEC.md section 6.2)."""

import hashlib
import json
import logging
import time
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse

from ritvo.adapter_contract.messages import ContractError, decode
from ritvo.adapter_contract.signing import SignatureError, verify
from ritvo.governance_os.service.events import OUTBOUND, to_ledger_event
from ritvo.ledger.store import Ledger

log = logging.getLogger("ritvo.service")


def create_app(
    ledger: Ledger,
    keys: Mapping[str, bytes],
    bundle_path: Path | None,
    jwks: dict[str, Any],
    clock: Callable[[], float] = time.time,
) -> FastAPI:
    """The service. `keys` are the HMAC keys of the enforcement points, by `key_id`."""
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

    def signed_path(request: Request) -> str:
        query = request.url.query
        return request.url.path + (f"?{query}" if query else "")

    async def authenticate(request: Request) -> tuple[bytes, JSONResponse | None]:
        body = await request.body()
        try:
            verify(request.method, signed_path(request), body, request.headers, keys, clock())
        except SignatureError as exc:
            # The reason is for the operator's log, not for the caller.
            log.warning("request refused: %s", exc)
            return body, JSONResponse({"error": "invalid_signature"}, status_code=401)
        return body, None

    @app.post("/v1/events")
    async def events(request: Request) -> Response:
        body, refused = await authenticate(request)
        if refused is not None:
            return refused
        try:
            message = decode(body)
        except ContractError as exc:
            log.warning("message refused: %s", exc)
            return JSONResponse({"error": "invalid_message"}, status_code=400)
        if message.type in OUTBOUND:
            return JSONResponse(
                {"error": f"{message.type} is sent by the Governance OS, not to it"},
                status_code=400,
            )
        duplicate = ledger.contains(message.event_id)
        written = ledger.append(to_ledger_event(message))
        return JSONResponse({"accepted": True, "duplicate": duplicate, "seq": written.seq})

    @app.get("/v1/bundles/latest")
    async def bundle(request: Request) -> Response:
        _, refused = await authenticate(request)
        if refused is not None:
            return refused
        if bundle_path is None or not bundle_path.is_file():
            return JSONResponse({"error": "no bundle has been published"}, status_code=404)
        data = bundle_path.read_bytes()
        etag = '"' + hashlib.sha256(data).hexdigest() + '"'
        if request.headers.get("if-none-match") == etag:
            return Response(status_code=304, headers={"ETag": etag})
        return Response(data, media_type="application/json", headers={"ETag": etag})

    @app.get("/v1/keys")
    async def public_keys() -> Response:
        return Response(json.dumps(jwks), media_type="application/json")

    return app
