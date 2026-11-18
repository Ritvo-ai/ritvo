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
    parsed = yaml.safe_load(draft_from_file(path).text)
    assert parsed["review_version"] == 1
    assert parsed["draft"] is True
    assert "organisation" not in parsed
    assert set(parsed["answers"]) == {"agents.inventory", "agents.owner", "agents.reach"}
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    for basis in parsed["basis"].values():
        assert basis["source"] == "derived"
        assert basis["evidence"].startswith(f"inventory.json sha256:{digest} question sha256:")
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


# Additive re-run


def _previous(tmp_path: Path, edit: str = "") -> Path:
    first = _inventory_file(tmp_path)
    assert runner.invoke(app, ["review", "--draft", str(first)]).exit_code == 0
    text = (tmp_path / "answers.draft.yaml").read_text(encoding="utf-8") + edit
    previous = tmp_path / "previous.yaml"
    previous.write_text(text, encoding="utf-8")
    (tmp_path / "answers.draft.yaml").unlink()
    return previous


def _rescan(tmp_path: Path, data: dict[str, Any]) -> Path:
    path = tmp_path / "scan2" / "inventory.json"
    path.parent.mkdir()
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return path


def _same() -> dict[str, Any]:
    return _data([_agent("a", "alice")], [_tool("t", "high", ["a"])])


def test_an_unchanged_source_is_carried_with_a_persons_edit(tmp_path: Path) -> None:
    previous = _previous(tmp_path)
    parsed = yaml.safe_load(previous.read_text(encoding="utf-8"))
    parsed["answers"]["agents.inventory"] = "complete"
    previous.write_text(yaml.safe_dump(parsed), encoding="utf-8")
    scan = _rescan(tmp_path, _same())
    result = runner.invoke(app, ["review", "--draft", str(scan), "--previous", str(previous)])
    assert result.exit_code == 0, result.output
    out = yaml.safe_load((tmp_path / "scan2" / "answers.draft.yaml").read_text(encoding="utf-8"))
    assert out["answers"]["agents.inventory"] == "complete"
    assert "agents.inventory  complete  carried" in result.output
    assert out["draft"] is True


def test_a_changed_source_is_drafted_again(tmp_path: Path) -> None:
    previous = _previous(tmp_path)
    changed = _data([_agent("a", None)], [_tool("t", "high", ["a"])])
    scan = _rescan(tmp_path, changed)
    result = runner.invoke(app, ["review", "--draft", str(scan), "--previous", str(previous)])
    assert result.exit_code == 0, result.output
    out = yaml.safe_load((tmp_path / "scan2" / "answers.draft.yaml").read_text(encoding="utf-8"))
    assert out["answers"]["agents.owner"] == "unknown"
    assert "agents.owner      unknown  changed (was all)" in result.output
    assert "agents.reach      all  carried" in result.output
    assert "agents.inventory  partial  carried" in result.output


def test_other_answers_are_carried_unchanged(tmp_path: Path) -> None:
    previous = _previous(tmp_path)
    parsed = yaml.safe_load(previous.read_text(encoding="utf-8"))
    parsed["organisation"] = "Example Ltd"
    parsed["answers"]["approval.named"] = "named_role"
    parsed["answers"]["agents.new_tools"] = "needs_decision"
    parsed["basis"]["agents.new_tools"] = {
        "source": "documented",
        "evidence": "policy.pdf sha256:ab",
    }
    previous.write_text(yaml.safe_dump(parsed), encoding="utf-8")
    before = previous.read_bytes()
    scan = _rescan(tmp_path, _same())
    result = runner.invoke(app, ["review", "--draft", str(scan), "--previous", str(previous)])
    assert result.exit_code == 0, result.output
    out = yaml.safe_load((tmp_path / "scan2" / "answers.draft.yaml").read_text(encoding="utf-8"))
    assert out["organisation"] == "Example Ltd"
    assert out["answers"]["approval.named"] == "named_role"
    assert out["basis"]["agents.new_tools"]["source"] == "documented"
    assert "2 other answers carried unchanged." in result.output
    assert previous.read_bytes() == before


def test_an_answer_with_no_question_digest_is_drafted_again(tmp_path: Path) -> None:
    previous = _previous(tmp_path)
    parsed = yaml.safe_load(previous.read_text(encoding="utf-8"))
    for basis in parsed["basis"].values():
        basis["evidence"] = "inventory.json sha256:" + "0" * 64
    previous.write_text(yaml.safe_dump(parsed), encoding="utf-8")
    scan = _rescan(tmp_path, _same())
    result = runner.invoke(app, ["review", "--draft", str(scan), "--previous", str(previous)])
    assert result.exit_code == 0, result.output
    assert sum(" changed" in line for line in result.output.splitlines()) == 3


def test_new_questions_are_new(tmp_path: Path) -> None:
    previous = tmp_path / "previous.yaml"
    previous.write_text("review_version: 1\nanswers: {}\n", encoding="utf-8")
    scan = _rescan(tmp_path, _same())
    result = runner.invoke(app, ["review", "--draft", str(scan), "--previous", str(previous)])
    assert result.exit_code == 0, result.output
    assert sum(line.endswith("  new") for line in result.output.splitlines()) == 3


def test_previous_version_0_is_refused(tmp_path: Path) -> None:
    previous = tmp_path / "previous.yaml"
    previous.write_text("review_version: 0\nanswers: {}\n", encoding="utf-8")
    scan = _rescan(tmp_path, _same())
    result = runner.invoke(app, ["review", "--draft", str(scan), "--previous", str(previous)])
    assert result.exit_code == 1
    assert "review_version 0" in result.output
    assert not (tmp_path / "scan2" / "answers.draft.yaml").exists()


def test_previous_needs_draft_and_is_never_overwritten(tmp_path: Path) -> None:
    previous = _previous(tmp_path)
    assert runner.invoke(app, ["review", str(previous), "--previous", str(previous)]).exit_code == 1
    scan = _rescan(tmp_path, _same())
    result = runner.invoke(
        app, ["review", "--draft", str(scan), "--previous", str(previous), "--out", str(previous)]
    )
    assert result.exit_code == 1


def test_the_digest_ignores_names_it_does_not_use(tmp_path: Path) -> None:
    first = draft_answers(Inventory.model_validate(_same()))
    again = draft_answers(Inventory.model_validate(_same()))
    assert [a.digest for a in first] == [a.digest for a in again]
    other = draft_answers(
        Inventory.model_validate(_data([_agent("a", "alice")], [_tool("t", "low", ["a"])]))
    )
    assert first[0].digest == other[0].digest
    assert first[2].digest != other[2].digest
