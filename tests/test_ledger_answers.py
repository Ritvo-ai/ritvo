import sqlite3
from pathlib import Path
from typing import Any

import pytest
import yaml
from conftest import Stack
from typer.testing import CliRunner

from ritvo.cli.main import app
from ritvo.diagnostic.review.draft import ledger_answers
from ritvo.diagnostic.review.scoring import ReviewError, load_answers
from ritvo.enforcement.evaluate import ledger_event
from ritvo.ledger.store import Ledger

runner = CliRunner()


def _inventory(tmp_path: Path) -> Path:
    from test_review_draft import _data, _inventory_file

    return _inventory_file(tmp_path, _data([], []))


def _ledger(stack: Stack, tmp_path: Path, mode: str = "observe", calls: int = 3) -> Path:
    path = tmp_path / "observed.db"
    ledger = Ledger(path)
    evaluator = stack.evaluator(mode)
    for n in range(calls):
        attended = n % 2 == 0
        claims: dict[str, Any] = {} if attended else {"sub": "acme.research", "act": None}
        result = evaluator.evaluate(
            stack.idp.mint(**claims), "broker.order", "tools/call", {"order_value": n}
        )
        ledger.append(
            ledger_event(
                result, "broker.order", "tools/call", "mcp", f"evt-{n}", "2026-11-23T10:00:00Z"
            )
        )
    ledger.close()
    return path


def test_the_ledger_gives_the_two_questions_it_can(stack: Stack, tmp_path: Path) -> None:
    answers = {a.question: a for a in ledger_answers(_ledger(stack, tmp_path))}
    assert set(answers) == {"record.decisions", "record.identity"}
    assert answers["record.identity"].answer == "both"
    assert answers["record.decisions"].answer == "partly"  # never yes
    assert "3 observed decisions: 2 attended, 1 unattended" in answers["record.identity"].note
    assert (
        "3 decisions recorded, 3 that enforce mode would not have allowed"
        in answers["record.decisions"].note
    )
    assert all(a.source == "observed" for a in answers.values())
    assert answers["record.identity"].evidence.startswith("observed.db sha256:")


def test_no_observed_decisions_is_unknown(tmp_path: Path) -> None:
    path = tmp_path / "empty.db"
    Ledger(path).close()
    assert {a.answer for a in ledger_answers(path)} == {"unknown"}


def test_a_policy_change_alone_is_not_a_decision(stack: Stack, tmp_path: Path) -> None:
    from ritvo.ledger.event import LedgerEvent

    path = tmp_path / "l.db"
    ledger = Ledger(path)
    ledger.append(
        LedgerEvent(
            event_id="a-1", timestamp="2026-11-23T10:00:00Z", type="policy_change", policy_version=1
        )
    )
    ledger.close()
    assert {a.answer for a in ledger_answers(path)} == {"unknown"}


def test_a_ledger_that_does_not_verify_stops_the_draft(stack: Stack, tmp_path: Path) -> None:
    path = _ledger(stack, tmp_path)
    db = sqlite3.connect(path, isolation_level=None)
    db.execute("DROP TRIGGER ledger_no_update")
    db.execute(
        "UPDATE ledger SET entry = replace(entry, 'broker.order', 'broker.quote') WHERE seq = 2"
    )
    db.close()
    with pytest.raises(ReviewError, match="does not verify"):
        ledger_answers(path)
    result = runner.invoke(
        app, ["review", "--draft", str(_inventory(tmp_path)), "--ledger", str(path)]
    )
    assert result.exit_code == 1 and not (tmp_path / "answers.draft.yaml").exists()


def test_the_command_drafts_them_as_observed(stack: Stack, tmp_path: Path) -> None:
    ledger = _ledger(stack, tmp_path)
    inventory = _inventory(tmp_path)
    result = runner.invoke(app, ["review", "--draft", str(inventory), "--ledger", str(ledger)])
    assert result.exit_code == 0, result.output
    assert "record.identity" in result.output and "record.decisions" in result.output
    parsed = yaml.safe_load((tmp_path / "answers.draft.yaml").read_text(encoding="utf-8"))
    assert parsed["answers"]["record.identity"] == "both"
    basis = parsed["basis"]["record.identity"]
    assert (
        basis["source"] == "observed"
        and "observed.db sha256:" in basis["evidence"]
        and "question sha256:" in basis["evidence"]
    )
    assert str(tmp_path) not in basis["evidence"]
    assert "agents.inventory" in parsed["answers"]
    # the draft is a proposal: a person removes the draft line before it is scored
    confirmed = tmp_path / "answers.yaml"
    confirmed.write_text(
        (tmp_path / "answers.draft.yaml").read_text().replace("draft: true\n", ""), encoding="utf-8"
    )
    assert load_answers(confirmed).basis["record.identity"].source == "observed"


def test_the_ledger_is_only_read(stack: Stack, tmp_path: Path) -> None:
    ledger = _ledger(stack, tmp_path)
    before = ledger.read_bytes()
    runner.invoke(app, ["review", "--draft", str(_inventory(tmp_path)), "--ledger", str(ledger)])
    assert ledger.read_bytes() == before


def test_a_ledger_answer_is_carried_only_if_unchanged(stack: Stack, tmp_path: Path) -> None:
    first = tmp_path / "one"
    first.mkdir()
    ledger = _ledger(stack, first)
    inventory = _inventory(first)
    assert (
        runner.invoke(app, ["review", "--draft", str(inventory), "--ledger", str(ledger)]).exit_code
        == 0
    )
    previous = first / "answers.draft.yaml"
    again = runner.invoke(
        app,
        [
            "review",
            "--draft",
            str(inventory),
            "--ledger",
            str(ledger),
            "--previous",
            str(previous),
            "--out",
            str(first / "again.yaml"),
        ],
    )
    assert again.exit_code == 0, again.output
    assert ["record.identity", "both", "carried"] in [
        line.split() for line in again.output.splitlines()
    ]
    more = Ledger(ledger)
    more_stack_result = stack.evaluator("observe").evaluate(
        stack.idp.mint(), "broker.order", "tools/call", {}
    )
    more.append(
        ledger_event(
            more_stack_result,
            "broker.order",
            "tools/call",
            "mcp",
            "evt-new",
            "2026-11-23T11:00:00Z",
        )
    )
    more.close()
    changed = runner.invoke(
        app,
        [
            "review",
            "--draft",
            str(inventory),
            "--ledger",
            str(ledger),
            "--previous",
            str(previous),
            "--out",
            str(first / "changed.yaml"),
        ],
    )
    assert ["record.decisions", "partly", "changed"] in [
        line.split()[:3] for line in changed.output.splitlines()
    ]


def test_ledger_needs_draft(tmp_path: Path) -> None:
    path = tmp_path / "l.db"
    Ledger(path).close()
    assert runner.invoke(app, ["review", str(path), "--ledger", str(path)]).exit_code == 1
