import hashlib
import json
from pathlib import Path
from typing import Any

import pytest
import yaml
from typer.testing import CliRunner

from ritvo.cli.main import app
from ritvo.diagnostic.review.draft import draft_answers, draft_from_file
from ritvo.diagnostic.review.questions import QUESTIONS, Level
from ritvo.diagnostic.review.scoring import ReviewError, load_answers, score
from ritvo.inspector.inventory import Inventory, InventoryError

runner = CliRunner()
HASH = "sha256:" + "c0" * 32


def _agent(agent_id: str, owner: str | None) -> dict[str, Any]:
    return {
        "id": agent_id,
        "source": "code",
        "name": None,
        "platform": None,
        "platform_ref": None,
        "framework": None,
        "owner": owner,
        "owner_team": None,
        "credential": None,
    }


def _tool(name: str, tier: str | None, reached_by: list[str]) -> dict[str, Any]:
    return {
        "id": f"srv/{name}",
        "source": "mcp",
        "server": "srv",
        "sdk": None,
        "platform_ref": None,
        "calls": [],
        "description_hash": HASH,
        "input_schema_hash": HASH,
        "annotations": {
            "readOnly": None,
            "destructive": None,
            "idempotent": None,
            "openWorld": None,
        },
        "proposed_tier": tier,
        "reached_by": reached_by,
    }


def _data(agents: list[dict[str, Any]], tools: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "schema_version": 2,
        "generated_at": "2026-10-13T09:30:00Z",
        "tool": "ritvo-inspect 0.1.0",
        "manifest": {
            "paths": [],
            "files_read": 0,
            "unreadable": [],
            "servers_contacted": [],
            "env_names_seen": [],
            "registry_exports": [],
            "owner_overlays": [],
        },
        "agents": agents,
        "mcp_servers": [],
        "tools": tools,
        "identity": {"issuers": []},
        "findings": [],
    }


def _answers(data: dict[str, Any]) -> dict[str, str]:
    return {item.question: item.answer for item in draft_answers(Inventory.model_validate(data))}


def test_no_agents_is_unknown_never_none() -> None:
    assert _answers(_data([], [])) == {
        "agents.inventory": "unknown",
        "agents.owner": "unknown",
        "agents.reach": "unknown",
    }


def test_inventory_is_partial_never_complete() -> None:
    assert _answers(_data([_agent("a", "alice")], []))["agents.inventory"] == "partial"


def test_owner_all_only_when_every_agent_has_one() -> None:
    both = _data([_agent("a", "alice"), _agent("b", "bob")], [])
    assert _answers(both)["agents.owner"] == "all"
    one = _data([_agent("a", "alice"), _agent("b", None)], [])
    assert _answers(one)["agents.owner"] == "unknown"
    none = _data([_agent("a", None)], [])
    assert _answers(none)["agents.owner"] == "unknown"


def test_reach_all_some_and_unknown() -> None:
    agents = [_agent("a", None), _agent("b", None)]
    assert _answers(_data(agents, [_tool("t", None, ["a", "b"])]))["agents.reach"] == "all"
    assert _answers(_data(agents, [_tool("t", None, ["a"])]))["agents.reach"] == "some"
    assert _answers(_data(agents, [_tool("t", "high", [])]))["agents.reach"] == "unknown"


def test_tier_never_changes_the_answer() -> None:
    agents = [_agent("a", None)]
    low = _answers(_data(agents, [_tool("t", "low", ["a"])]))
    high = _answers(_data(agents, [_tool("t", "high", ["a"])]))
    assert low == high


def test_notes_hold_counts_not_names() -> None:
    data = _data([_agent("secret-agent", "zed")], [_tool("hidden-tool", "high", ["secret-agent"])])
    drafted = draft_answers(Inventory.model_validate(data))
    notes = " ".join(item.note for item in drafted)
    assert "1 of 1 agents have an owner recorded." in notes
    assert "1 tools have a proposed high tier" in notes
    for name in ("secret-agent", "zed", "hidden-tool"):
        assert name not in notes


def _inventory_file(tmp_path: Path, data: dict[str, Any] | None = None) -> Path:
    path = tmp_path / "inventory.json"
    body = data if data is not None else _data([_agent("a", "alice")], [_tool("t", "high", ["a"])])
    path.write_text(json.dumps(body, indent=2) + "\n", encoding="utf-8")
    return path


def test_draft_file_shape_and_evidence(tmp_path: Path) -> None:
    path = _inventory_file(tmp_path)
    _, text = draft_from_file(path)
    parsed = yaml.safe_load(text)
    assert parsed["review_version"] == 1
    assert parsed["draft"] is True
    assert "organisation" not in parsed
    assert set(parsed["answers"]) == {"agents.inventory", "agents.owner", "agents.reach"}
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    for basis in parsed["basis"].values():
        assert basis == {"source": "derived", "evidence": f"inventory.json sha256:{digest}"}
        assert str(tmp_path) not in basis["evidence"]


def test_review_refuses_a_draft_until_the_line_is_removed(tmp_path: Path) -> None:
    path = _inventory_file(tmp_path)
    draft = tmp_path / "answers.draft.yaml"
    result = runner.invoke(app, ["review", "--draft", str(path)])
    assert result.exit_code == 0, result.output
    assert draft.exists()
    refused = runner.invoke(app, ["review", str(draft)])
    assert refused.exit_code == 1
    assert "draft" in refused.output
    assert not (tmp_path / "review.html").exists()
    with pytest.raises(ReviewError, match="draft"):
        load_answers(draft)
    confirmed = tmp_path / "answers.yaml"
    confirmed.write_text(draft.read_text().replace("draft: true\n", ""), encoding="utf-8")
    review = score(load_answers(confirmed))
    levels = {q.question.id: q.level for a in review.areas for q in a.questions}
    assert levels["agents.owner"] is Level.IN_PLACE
    assert levels["agents.inventory"] is Level.PARTIAL
    assert levels["approval.named"] is Level.NOT_ANSWERED


def test_cli_terminal_output(tmp_path: Path) -> None:
    path = _inventory_file(tmp_path)
    result = runner.invoke(app, ["review", "--draft", str(path)])
    assert result.exit_code == 0
    assert f"{len(QUESTIONS) - 3} questions are left to a person." in result.output
    assert f"Wrote {tmp_path / 'answers.draft.yaml'}" in result.output


def test_never_written_over_a_file(tmp_path: Path) -> None:
    path = _inventory_file(tmp_path)
    target = tmp_path / "answers.draft.yaml"
    target.write_text("mine\n", encoding="utf-8")
    result = runner.invoke(app, ["review", "--draft", str(path)])
    assert result.exit_code == 1
    assert target.read_text(encoding="utf-8") == "mine\n"


def test_out_cannot_be_the_inventory(tmp_path: Path) -> None:
    path = _inventory_file(tmp_path)
    before = path.read_bytes()
    result = runner.invoke(app, ["review", "--draft", str(path), "--out", str(path)])
    assert result.exit_code == 1
    assert path.read_bytes() == before


def test_out_names_the_draft(tmp_path: Path) -> None:
    path = _inventory_file(tmp_path)
    target = tmp_path / "mine.yaml"
    result = runner.invoke(app, ["review", "--draft", str(path), "--out", str(target)])
    assert result.exit_code == 0
    assert target.exists()
    assert not (tmp_path / "answers.draft.yaml").exists()


def test_invalid_inventory_writes_nothing(tmp_path: Path) -> None:
    bad = tmp_path / "inventory.json"
    bad.write_text(json.dumps({"schema_version": 1}), encoding="utf-8")
    result = runner.invoke(app, ["review", "--draft", str(bad)])
    assert result.exit_code == 1
    assert "schema_version 1" in result.output
    assert not (tmp_path / "answers.draft.yaml").exists()
    with pytest.raises(InventoryError):
        draft_from_file(bad)
