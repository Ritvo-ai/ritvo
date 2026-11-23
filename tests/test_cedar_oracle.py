"""Check cedarpy against the official Cedar command line tool (docs/SPEC.md section 5.9, control 3).

CI installs the tool and sets CEDAR_ORACLE_REQUIRED so a missing tool fails the job. Without it
the test is skipped, so a developer need not install Rust.
"""

import itertools
import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

import cedarpy
import pytest

from ritvo.config.load import load_config
from ritvo.enforcement.bundles import canary
from ritvo.enforcement.bundles.engine import SETS, context_for
from ritvo.governance_os.routing.compile import compile_bundle

DEMO = Path(__file__).resolve().parents[1] / "examples" / "mcp-demo" / "ritvo.yaml"
CEDAR = shutil.which("cedar")

if CEDAR is None and os.environ.get("CEDAR_ORACLE_REQUIRED"):
    pytest.fail("the Cedar command line tool is required here and is not installed", pytrace=False)

pytestmark = pytest.mark.skipif(
    CEDAR is None, reason="the cedar command line tool is not installed"
)


def _oracle(
    directory: Path, policies: str, entities: list[dict[str, Any]], request: dict[str, Any]
) -> str:
    (directory / "p.cedar").write_text(policies, encoding="utf-8")
    (directory / "e.json").write_text(json.dumps(entities), encoding="utf-8")
    (directory / "c.json").write_text(json.dumps(request["context"]), encoding="utf-8")
    assert CEDAR is not None
    done = subprocess.run(  # noqa: S603 - fixed argv, no shell
        [
            CEDAR, "authorize",
            "--policies", str(directory / "p.cedar"),
            "--entities", str(directory / "e.json"),
            "--principal", request["principal"],
            "--action", request["action"],
            "--resource", request["resource"],
            "--context", str(directory / "c.json"),
        ],
        capture_output=True, text=True, check=False,
    )  # fmt: skip
    words = done.stdout.split()
    assert words and words[0] in ("ALLOW", "DENY"), (
        f"unreadable output: {done.stdout!r} {done.stderr!r}"
    )
    return words[0]


def _ours(policies: str, entities: list[dict[str, Any]], request: dict[str, Any]) -> str:
    answer = cedarpy.is_authorized(request, policies, entities)
    assert answer.decision in (cedarpy.Decision.Allow, cedarpy.Decision.Deny)
    return "ALLOW" if answer.decision == cedarpy.Decision.Allow else "DENY"


def _request(agent: str, tool: str, params: dict[str, Any]) -> dict[str, Any]:
    return {
        "principal": f'Agent::"{agent}"',
        "action": 'Action::"call"',
        "resource": f'Tool::"{tool}"',
        "context": context_for("attended", ["orders:read"], False, params),
    }


def test_the_canary_agrees_with_the_official_tool(tmp_path: Path) -> None:
    for name, policies in canary.POLICIES.items():
        for agent, tool, params, _, _ in canary.CASES:
            request = _request(agent, tool, params)
            assert _ours(policies, canary.ENTITIES, request) == _oracle(
                tmp_path, policies, canary.ENTITIES, request
            ), f"{name}: {agent} {tool} {params}"


def test_the_compiled_demo_bundle_agrees_with_the_official_tool(tmp_path: Path) -> None:
    bundle = compile_bundle(load_config(DEMO), 1)
    entities = bundle.entities
    agents = [e["uid"]["id"] for e in entities if e["uid"]["type"] == "Agent"]
    tools = [e["uid"]["id"] for e in entities if e["uid"]["type"] == "Tool"] + ["no.such.tool"]
    values: list[dict[str, Any]] = [
        {},
        {"order_value": 0},
        {"order_value": 1200},
        {"order_value": 50000},
        {"order_value": 50001},
        {"order_value": 90000.5},
    ]
    checked = 0
    for name in SETS:
        policies = getattr(bundle.cedar, name)
        if not policies:
            continue
        for agent, tool, params in itertools.product(agents, tools, values):
            request = _request(agent, tool, params)
            assert _ours(policies, entities, request) == _oracle(
                tmp_path, policies, entities, request
            ), f"{name}: {agent} {tool} {params}"
            checked += 1
    assert checked > 50
