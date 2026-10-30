import hashlib
import json
import re
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from ritvo.cli.main import app
from ritvo.inspector.inventory import Inventory, load_inventory
from ritvo.inspector.report import build, render_html

runner = CliRunner()

SHA = "ab" * 32
HASH = "sha256:" + "c0" * 32
ANNOTATIONS = {"readOnly": None, "destructive": None, "idempotent": None, "openWorld": None}


def _agent(agent_id: str, owner: str | None, credential: str | None = None) -> dict[str, object]:
    return {
        "id": agent_id,
        "source": "code",
        "name": None,
        "platform": None,
        "platform_ref": f"{agent_id}.py",
        "framework": "langgraph",
        "owner": owner,
        "owner_team": "finance" if owner else None,
        "credential": credential,
    }


def _server(server_id: str, transport: str, auth: dict[str, object] | None) -> dict[str, object]:
    return {
        "id": server_id,
        "source": "config",
        "transport": transport,
        "url": "https://mcp.example.com" if transport == "http" else None,
        "command": "npx" if transport == "stdio" else None,
        "auth": auth,
    }


def _mcp_tool(server: str, name: str, tier: str | None, destructive: bool) -> dict[str, object]:
    return {
        "id": f"{server}/{name}",
        "source": "mcp",
        "server": server,
        "sdk": None,
        "platform_ref": None,
        "calls": [],
        "description_hash": HASH,
        "input_schema_hash": HASH,
        "annotations": {
            "readOnly": not destructive,
            "destructive": destructive,
            "idempotent": None,
            "openWorld": False,
        },
        "proposed_tier": tier,
        "reached_by": [],
    }


def _inventory_data() -> dict[str, Any]:
    return {
        "schema_version": 2,
        "generated_at": "2026-10-13T09:30:00Z",
        "tool": "ritvo-inspect 0.0.1.dev0",
        "manifest": {
            "paths": ["./services", "~/.cursor/mcp.json"],
            "files_read": 12,
            "unreadable": ["./services/broken.py"],
            "servers_contacted": ["ledger-mcp"],
            "env_names_seen": ["SHARED_API_KEY", "APP_MODE"],
            "registry_exports": [],
            "owner_overlays": [],
        },
        "agents": [
            _agent("code.app.triage", None, "SHARED_API_KEY"),
            _agent("code.app.router", "dev", "SHARED_API_KEY"),
            _agent("code.app.reader", "priya"),
        ],
        "mcp_servers": [
            _server(
                "ledger-mcp",
                "http",
                {
                    "protected_resource_metadata": True,
                    "authorization_servers": ["https://idp.example.com"],
                    "scopes_supported": ["ledger:read"],
                },
            ),
            _server(
                "crm-mcp",
                "http",
                {
                    "protected_resource_metadata": False,
                    "authorization_servers": [],
                    "scopes_supported": [],
                },
            ),
            _server("web-mcp", "stdio", None),
            _server("files-mcp", "http", None),
        ],
        "tools": [
            _mcp_tool("ledger-mcp", "get_entry", "low", False),
            _mcp_tool("ledger-mcp", "purge_entries", "high", True),
            _mcp_tool("ledger-mcp", "post_entry", None, False),
            {
                "id": "code.app.router/kiteconnect",
                "source": "code",
                "server": None,
                "sdk": "kiteconnect",
                "platform_ref": "router.py",
                "calls": ["place_order"],
                "description_hash": None,
                "input_schema_hash": None,
                "annotations": ANNOTATIONS,
                "proposed_tier": "high",
                "reached_by": ["code.app.router", "code.app.triage"],
            },
        ],
        "identity": {"issuers": ["https://idp.example.com"]},
        "findings": [
            {"rule": "agent.no_owner", "subject": "code.app.triage", "agents": ["code.app.triage"]},
            {
                "rule": "identity.shared_key_name",
                "subject": "SHARED_API_KEY",
                "agents": ["code.app.router", "code.app.triage"],
            },
            {
                "rule": "tool.destructive_unowned",
                "subject": "code.app.router/kiteconnect",
                "agents": ["code.app.triage"],
            },
            {"rule": "mcp.no_auth", "subject": "crm-mcp", "agents": []},
        ],
    }


def _inventory(data: dict[str, Any] | None = None) -> Inventory:
    return Inventory.model_validate(data or _inventory_data())


def _html(data: dict[str, Any] | None = None) -> str:
    return render_html(_inventory(data), SHA)


def test_report_is_one_file_with_no_remote_resource() -> None:
    html = _html()
    assert html.startswith("<!doctype html>")
    assert "<title>Ritvo inspection report</title>" in html
    assert not re.search(r"(src|href)\s*=", html)
    assert not re.search(r"<link\b|@import|url\(|<img\b|<iframe\b|fetch\(|XMLHttpRequest", html)
    assert "http://" not in html
    # Server and issuer URLs are shown as text only.
    assert re.search(re.escape("https://idp.example.com"), html)
    assert "font-family: var(--f-body)" in html
    assert "fonts.g" not in html


def test_report_states_the_inventory_hash_and_the_priority_rule() -> None:
    html = _html()
    assert f"sha256 {SHA}" in html
    assert "It is not a measure of exploitability" in html
    assert "not a conformity assessment" in html


def test_cover_counts_and_reach() -> None:
    data = build(_inventory(), SHA)
    assert data["lede"] == (
        "Found 3 agents, 4 MCP servers and 4 tools. 1 of 3 agents have no owner. "
        "3 servers not inspected, so its tools are unknown."
    )
    assert [reading["value"] for reading in data["readings"]] == [3, 4, 4, 4]
    assert data["readings"][2]["note"] == "2 would start at the high tier, 1 tier unknown"
    assert data["readings"][3]["note"] == "3 to fix first"


def test_findings_rank_by_reach_and_carry_three_fields() -> None:
    findings = build(_inventory(), SHA)["findings"]
    assert [(f["priority"], f["rule"]) for f in findings] == [
        (1, "tool.destructive_unowned"),
        (1, "mcp.no_auth"),
        (1, "agent.no_owner"),
        (2, "identity.shared_key_name"),
    ]
    for finding in findings:
        assert finding["saw"] and finding["why"] and finding["next"]
    no_owner = findings[2]
    assert no_owner["saw"] == (
        "No owner in the inventory. Reaches 1 tool found in code, 1 at the high tier."
    )


def test_no_owner_with_no_known_reach_is_priority_two() -> None:
    data = _inventory_data()
    data["tools"] = [tool for tool in data["tools"] if tool["source"] == "mcp"]
    data["findings"] = [
        {"rule": "agent.no_owner", "subject": "code.app.triage", "agents": ["code.app.triage"]}
    ]
    finding = build(_inventory(data), SHA)["findings"][0]
    assert finding["priority"] == 2
    assert finding["saw"] == "No owner in the inventory. No tool reach is known."


def test_unknown_is_shown_as_unknown_never_as_safe() -> None:
    data = build(_inventory(), SHA)
    servers = {row["id"]: row for row in data["servers"]}
    assert servers["files-mcp"]["auth"] == "Unknown: not inspected"
    assert servers["files-mcp"]["tools"] is None
    assert servers["files-mcp"]["order"] == "Inspect first"
    assert servers["web-mcp"]["inspected"] is False
    assert servers["crm-mcp"]["auth_kind"] == "none"
    assert servers["crm-mcp"]["order"] == "Inspect first"
    assert servers["ledger-mcp"]["order"] == "Wrap early"
    html = _html()
    assert "Not inspected: tools unknown" in html
    assert "unknown: not inspected" in html
    unknown_tool = next(row for row in data["tools"] if row["name"] == "post_entry")
    assert unknown_tool["tier"] == "unknown"
    assert ">No tier proposed" not in html and "no tier proposed" in html


def test_every_status_has_a_label_and_a_shape() -> None:
    html = _html()
    for chip in re.findall(r'<span class="chip [^"]+">(.*?)</span>', html):
        assert "<svg" in chip, chip
        assert re.sub(r"<svg.*?</svg>", "", chip).strip(), chip


def test_agent_matrix_and_decisions() -> None:
    data = build(_inventory(), SHA)
    assert data["matrix"]["columns"] == ["code.app.router/kiteconnect"]
    cells = {row["agent"]: row["cells"] for row in data["matrix"]["rows"]}
    assert cells == {
        "code.app.triage": ["high"],
        "code.app.router": ["high"],
        "code.app.reader": [None],
    }
    agents = {row["id"]: row for row in data["agents"]}
    assert agents["code.app.reader"]["highest"] == "unknown"
    assert agents["code.app.triage"]["high"] == 1
    assert [d["who"] for d in data["decisions"]] == [
        "Agent sponsor",
        "Policy owner",
        "Platform lead",
    ]
    assert "code.app.triage" in data["decisions"][0]["text"]
    assert "files-mcp" in data["decisions"][2]["text"]


def test_control_mapping_statuses() -> None:
    controls = {c["area"]: c for c in build(_inventory(), SHA)["controls"]}
    assert controls["Named accountable owner for each agent"]["status"] == "partial"
    assert controls["Access control on tool servers"]["status"] == "gap"
    assert controls["Inventory of agents and tools"]["status"] == "partial"
    assert controls["Human oversight assigned to named people"]["status"] == "na"
    assert controls["Logging of agent actions"]["status"] == "na"


def test_control_mapping_when_everything_is_in_place() -> None:
    data = _inventory_data()
    data["agents"] = [_agent("code.app.reader", "priya")]
    data["mcp_servers"] = [data["mcp_servers"][0]]
    data["tools"] = []
    data["findings"] = []
    data["manifest"]["unreadable"] = []
    controls = {c["area"]: c["status"] for c in build(_inventory(data), SHA)["controls"]}
    assert controls["Inventory of agents and tools"] == "ok"
    assert controls["Named accountable owner for each agent"] == "ok"
    assert controls["Access control on tool servers"] == "ok"


def test_empty_inventory_says_so() -> None:
    data = _inventory_data()
    for key in ("agents", "mcp_servers", "tools", "findings"):
        data[key] = []
    data["manifest"]["servers_contacted"] = []
    html = _html(data)
    assert "No MCP servers were found." in html
    assert "No agents were found." in html
    assert "No findings. That means the rules found nothing in what was read" in html
    assert all(c["status"] == "na" for c in build(_inventory(data), SHA)["controls"][:3])


def test_four_views_and_keyboard_tabs() -> None:
    html = _html()
    for view in ("engineer", "security", "owner", "auditor"):
        assert f'id="panel-{view}"' in html
    assert 'role="tablist"' in html and 'setAttribute("role", "tab")' in html
    for key in ("ArrowRight", "ArrowLeft", "Home", "End"):
        assert key in html
    assert "All, for print" in html
    # Without a script every view is already visible; the script hides the others.
    assert not re.search(r'<section class="panel"[^>]*\shidden', html)


def test_print_stylesheet_uses_the_light_palette_one_view_per_page() -> None:
    html = _html()
    printed = html[html.index("@media print") :]
    assert "--bg: #FFFFFF" in printed
    assert ".panel { break-before: page; }" in printed
    assert ".panel[hidden] { display: flex !important; }" in printed


def test_names_are_escaped() -> None:
    data = _inventory_data()
    data["manifest"]["paths"] = ["./<script>alert(1)</script>"]
    html = _html(data)
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html


def test_registry_commands_are_listed_for_platforms_not_yet_read() -> None:
    html = _html()
    assert "aws bedrock-agentcore-control list-agent-runtimes" in html
    data = _inventory_data()
    data["manifest"]["registry_exports"] = [
        {"platform": "agentcore", "sha256": "ab" * 32, "agents": 2}
    ]
    html = _html(data)
    assert "aws bedrock-agentcore-control list-agent-runtimes" not in html
    assert "reasoningEngines" in html


def test_credential_names_only_and_no_values() -> None:
    credentials = build(_inventory(), SHA)["credentials"]
    assert credentials == [
        {"name": "SHARED_API_KEY", "agents": ["code.app.router", "code.app.triage"]}
    ]
    assert "APP_MODE" not in _html().split("Credential names seen")[1].split("</section>")[0]


# Contrast


def _luminance(colour: str) -> float:
    channels = [int(colour[i : i + 2], 16) / 255 for i in (1, 3, 5)]
    lin = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
    return float(0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2])


def _contrast(a: str, b: str) -> float:
    high, low = sorted((_luminance(a), _luminance(b)), reverse=True)
    return (high + 0.05) / (low + 0.05)


def _palettes(html: str) -> list[dict[str, str]]:
    css = html[html.index("<style>") : html.index("</style>")]
    blocks = re.findall(r":root \{(.*?)\}", css, flags=re.S)
    return [dict(re.findall(r"--([\w-]+):\s*(#[0-9A-Fa-f]{6})", block)) for block in blocks]


PAIRS = [
    ("ink", "bg"),
    ("ink", "surface"),
    ("ink2", "bg"),
    ("ink2", "surface"),
    ("muted", "bg"),
    ("muted", "surface"),
    ("accent", "bg"),
    ("accent-ink", "accent"),
    ("t-low-ink", "t-low"),
    ("t-med-ink", "t-med"),
    ("t-high-ink", "t-high"),
    ("p1", "p1-bg"),
    ("p2", "p2-bg"),
    ("ok", "ok-bg"),
    ("ink2", "line2"),
    ("p1", "surface"),
    ("ink2", "surface"),
]


def test_text_meets_wcag_aa_in_light_dark_and_print() -> None:
    palettes = _palettes(_html())
    assert len(palettes) == 3
    for palette in palettes:
        for foreground, background in PAIRS:
            ratio = _contrast(palette[foreground], palette[background])
            assert ratio >= 4.5, (foreground, background, round(ratio, 2))


# CLI

REGISTRY = ["--registry", "agentcore:tests/fixtures/registry/agentcore/valid.json"]


def _write_inventory(path: Path) -> Path:
    path.write_text(json.dumps(_inventory_data(), indent=2) + "\n", encoding="utf-8")
    return path


def test_cli_report_beside_the_inventory(tmp_path: Path) -> None:
    code = tmp_path / "app"
    code.mkdir()
    (code / "agent.py").write_text("import anthropic\n", encoding="utf-8")
    out = tmp_path / "out" / "inventory.json"
    out.parent.mkdir()
    result = runner.invoke(app, ["inspect", "--code", str(code), "--report", "--out", str(out)])
    assert result.exit_code == 0, result.output
    report = out.parent / "report.html"
    assert f"Wrote {report}" in result.output
    html = report.read_text(encoding="utf-8")
    assert f"sha256 {hashlib.sha256(out.read_bytes()).hexdigest()}" in html
    assert "code.app.agent" in html


def test_cli_report_alone_from_an_existing_inventory(tmp_path: Path) -> None:
    source = _write_inventory(tmp_path / "inventory.json")
    before = source.read_bytes()
    result = runner.invoke(app, ["inspect", "--inventory", str(source), "--report"])
    assert result.exit_code == 0, result.output
    assert source.read_bytes() == before
    html = (tmp_path / "report.html").read_text(encoding="utf-8")
    assert f"sha256 {hashlib.sha256(before).hexdigest()}" in html
    assert result.output.strip().endswith(f"Wrote {tmp_path / 'report.html'}")
    assert result.output.count("Wrote") == 1
    assert load_inventory(source).agents


def test_cli_report_is_not_written_without_the_flag(tmp_path: Path) -> None:
    source = _write_inventory(tmp_path / "inventory.json")
    result = runner.invoke(app, ["inspect", "--inventory", str(source)])
    assert result.exit_code == 1
    assert "--inventory needs --owners or --report" in result.output
    assert not (tmp_path / "report.html").exists()


def test_cli_report_alone_refuses_out(tmp_path: Path) -> None:
    source = _write_inventory(tmp_path / "inventory.json")
    result = runner.invoke(
        app, ["inspect", "--inventory", str(source), "--report", "--out", str(tmp_path / "x.json")]
    )
    assert result.exit_code == 1
    assert "--out cannot be used with --inventory and --report alone" in result.output
    assert not (tmp_path / "report.html").exists()


@pytest.mark.parametrize("flag", [[], ["--report"]])
def test_cli_rejects_a_bad_inventory_and_writes_nothing(tmp_path: Path, flag: list[str]) -> None:
    source = tmp_path / "inventory.json"
    source.write_text('{"schema_version": 1}', encoding="utf-8")
    result = runner.invoke(app, ["inspect", "--inventory", str(source), "--report", *flag])
    assert result.exit_code == 1
    assert not (tmp_path / "report.html").exists()
