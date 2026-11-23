"""Run a demo folder in observe mode (docs/SPEC.md section 6.6).

Everything here is throwaway: the identity provider's key, the signing key and the tokens are made
for one run in a temporary folder and nothing in it is a credential.
"""

import importlib.util
import json
import logging
import os
import shutil
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import jwt
import yaml
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from jwt.algorithms import OKPAlgorithm
from starlette.testclient import TestClient

from ritvo.adapter_contract.bundle import new_seed
from ritvo.cli.onboarding import apply_config
from ritvo.config.load import load_config
from ritvo.diagnostic.review.draft import ledger_answers
from ritvo.enforcement.runtime import ENV_FILE_VARIABLE
from ritvo.secrets.access import Secrets
from ritvo.secrets.providers import EnvFileProvider

PLACEHOLDER = "file://__JWKS_URL__"
HEADERS = {
    "Accept": "application/json, text/event-stream",
    "MCP-Protocol-Version": "2026-07-28",
    "Mcp-Method": "tools/call",
}
META = {
    "io.modelcontextprotocol/protocolVersion": "2026-07-28",
    "io.modelcontextprotocol/clientInfo": {"name": "ritvo-demo", "version": "0"},
    "io.modelcontextprotocol/clientCapabilities": {},
}


class DemoError(Exception):
    """The demo folder cannot be run."""


@dataclass(frozen=True)
class CallOutcome:
    agent: str
    user: str | None
    tool: str
    passed: bool | None  # None when the SDK refused the token before the middleware saw it
    would_decide: str | None
    reason: str | None


@dataclass(frozen=True)
class DemoResult:
    outcomes: list[CallOutcome]
    entries: int
    decisions: int
    verified: bool
    answers: list[tuple[str, str, str]]
    workspace: Path | None


def _read_calls(folder: Path) -> list[dict[str, Any]]:
    try:
        data = yaml.safe_load((folder / "calls.yaml").read_text(encoding="utf-8"))
        calls = data["calls"]
        if not isinstance(calls, list) or not all(isinstance(call, dict) for call in calls):
            raise TypeError
        return calls
    except (OSError, KeyError, TypeError, yaml.YAMLError):
        raise DemoError("calls.yaml must hold a list called calls") from None


def run_demo(folder: Path, keep: bool = False) -> DemoResult:
    for noisy in ("mcp", "httpx", "httpx2", "starlette", "uvicorn"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    root = logging.getLogger()
    root.handlers = [h for h in root.handlers if h.level >= logging.WARNING]
    for name in ("server.py", "ritvo.yaml", "calls.yaml"):
        if not (folder / name).is_file():
            raise DemoError(f"{folder} has no {name}")
    calls = _read_calls(folder)
    workspace = Path(tempfile.mkdtemp(prefix="ritvo-demo-"))
    previous_env = os.environ.get(ENV_FILE_VARIABLE)
    try:
        return _run(folder, workspace, calls, keep)
    finally:
        if previous_env is None:
            os.environ.pop(ENV_FILE_VARIABLE, None)
        else:
            os.environ[ENV_FILE_VARIABLE] = previous_env
        if not keep:
            shutil.rmtree(workspace, ignore_errors=True)


def _run(folder: Path, workspace: Path, calls: list[dict[str, Any]], keep: bool) -> DemoResult:
    key = Ed25519PrivateKey.generate()
    jwk = json.loads(OKPAlgorithm.to_jwk(key.public_key()))
    jwk.update({"kid": "demo-idp", "use": "sig", "alg": "EdDSA"})
    jwks = workspace / "jwks.json"
    jwks.write_text(json.dumps({"keys": [jwk]}), encoding="utf-8")
    for name in ("server.py", "ritvo.yaml"):
        shutil.copy(folder / name, workspace / name)
    config_path = workspace / "ritvo.yaml"
    text = config_path.read_text(encoding="utf-8")
    if PLACEHOLDER not in text:
        raise DemoError(f"ritvo.yaml must name its key set {PLACEHOLDER}")
    config_path.write_text(text.replace(PLACEHOLDER, jwks.as_uri()), encoding="utf-8")
    config = load_config(config_path)
    if config.secrets is None or config.secrets.backend != "env_file":
        raise DemoError("the demo's ritvo.yaml must use secrets.backend env_file")
    if config.mode != "observe":
        raise DemoError("the demo runs in observe mode")
    env_file = workspace / "secrets.env"
    os.environ[ENV_FILE_VARIABLE] = str(env_file)
    Secrets(EnvFileProvider(env_file)).set("ritvo/bundle-signing-key", new_seed())
    apply_config(config_path)

    issuer = config.identity.issuers[0]  # type: ignore[union-attr]
    spec = importlib.util.spec_from_file_location("ritvo_demo_server", workspace / "server.py")
    if spec is None or spec.loader is None:
        raise DemoError("server.py cannot be loaded")
    module = importlib.util.module_from_spec(spec)
    os.environ["RITVO_CONFIG"] = str(config_path)
    spec.loader.exec_module(module)
    server, protection = getattr(module, "server", None), getattr(module, "protection", None)
    if server is None or protection is None:
        raise DemoError("server.py must define server and protection (the result of protect)")

    outcomes: list[CallOutcome] = []
    before = 0
    with TestClient(server.streamable_http_app(), base_url="http://localhost:8000") as client:
        for number, call in enumerate(calls, start=1):
            agent, user = str(call["agent"]), call.get("user")
            claims: dict[str, Any] = {
                "iss": issuer.issuer or issuer.name,
                "aud": issuer.audience,
                "sub": str(user) if user else agent,
                "scope": str(call.get("scope", "")),
                "exp": int(time.time()) + 600,
            }
            if user:
                claims["act"] = {"sub": agent}
            token = jwt.encode(claims, key, algorithm="EdDSA", headers={"kid": "demo-idp"})
            body = {
                "jsonrpc": "2.0",
                "id": number,
                "method": "tools/call",
                "params": {
                    "name": call["tool"],
                    "arguments": call.get("arguments") or {},
                    "_meta": META,
                },
            }
            response = client.post(
                "/mcp",
                json=body,
                headers={
                    **HEADERS,
                    "Mcp-Name": str(call["tool"]),
                    "Authorization": f"Bearer {token}",
                },
            )
            entries = protection.runtime.ledger.entries()
            fresh = entries[before:]
            before = len(entries)
            if response.status_code != 200 or not fresh:
                outcomes.append(
                    CallOutcome(
                        agent, user, call["tool"], None, None, f"http {response.status_code}"
                    )
                )
                continue
            entry = fresh[-1].entry
            outcomes.append(
                CallOutcome(
                    agent,
                    user,
                    call["tool"],
                    entry.decision == "allow",
                    entry.would_decide,
                    entry.outcome,
                )
            )
    ledger = protection.runtime.ledger
    result = ledger.verify()
    decisions = sum(1 for item in ledger.entries() if item.entry.type == "decision")
    protection.remove()
    answers = [
        (a.question, a.answer, a.note) for a in ledger_answers(workspace / config.ledger.path)
    ]
    return DemoResult(
        outcomes, result.entries, decisions, result.ok, answers, workspace if keep else None
    )
