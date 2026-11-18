from pathlib import Path
from typing import Any

import pytest
import yaml
from typer.testing import CliRunner

from ritvo.cli.main import app
from ritvo.diagnostic.questionnaire import QuestionnaireError, parse_questionnaire
from ritvo.diagnostic.review.questions import QUESTIONS, Level
from ritvo.diagnostic.review.scoring import AreaResult, load_answers, score

runner = CliRunner()
ALL_IDS = [question.id for question in QUESTIONS]


def _answers(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "answers.yaml"
    path.write_text(body, encoding="utf-8")
    return path


def _replies(answers: dict[str, str], answered: tuple[str, ...] = ()) -> str:
    """Input for the prompts: one line per open question, in the order of the spec."""
    return "".join(answers.get(qid, "") + "\n" for qid in ALL_IDS if qid not in answered)


# Documented basis (SPEC 8.1, scoring rule 4)

IN_PLACE = {
    "agents.inventory": "complete",
    "agents.owner": "all",
    "agents.reach": "all",
    "agents.new_tools": "needs_decision",
}


def _area_level(tmp_path: Path, source: str) -> AreaResult:
    basis = "".join(f"  {qid}:\n    source: {source}\n" for qid in IN_PLACE)
    answers = "".join(f"  {qid}: {value}\n" for qid, value in IN_PLACE.items())
    path = _answers(tmp_path, f"review_version: 1\nanswers:\n{answers}basis:\n{basis}")
    return score(load_answers(path)).areas[0]


def test_documented_answers_alone_cap_an_area_at_partial(tmp_path: Path) -> None:
    area = _area_level(tmp_path, "documented")
    assert area.level is Level.PARTIAL
    assert area.asserted_cap
    assert area.evidence == 0


def test_a_derived_answer_lifts_the_cap(tmp_path: Path) -> None:
    area = _area_level(tmp_path, "derived")
    assert area.level is Level.IN_PLACE
    assert not area.asserted_cap


# Interview (SPEC 8.6)


def test_interview_asks_only_open_questions_and_records_asserted(tmp_path: Path) -> None:
    path = _answers(
        tmp_path,
        "review_version: 1\norganisation: Example Ltd\nanswers:\n  agents.owner: all\n"
        "basis:\n  agents.owner:\n    source: derived\n    evidence: inventory.json sha256:ab\n"
        "notes:\n  agents.owner: kept\n",
    )
    replies = _replies(
        {"agents.inventory": "partial", "approval.self": "unknown"}, ("agents.owner",)
    )
    result = runner.invoke(app, ["review", "--interview", str(path)], input=replies)
    assert result.exit_code == 0, result.output
    assert "agents.owner:" not in result.output
    assert "2 answers recorded as asserted." in result.output
    out = yaml.safe_load((tmp_path / "answers.interview.yaml").read_text(encoding="utf-8"))
    assert out["organisation"] == "Example Ltd"
    assert out["answers"] == {
        "agents.inventory": "partial",
        "agents.owner": "all",
        "approval.self": "unknown",
    }
    assert out["basis"]["agents.inventory"] == {"source": "asserted"}
    assert out["basis"]["agents.owner"]["source"] == "derived"
    assert out["notes"] == {"agents.owner": "kept"}
    assert "draft" not in out


def test_interview_reprompts_an_invalid_reply(tmp_path: Path) -> None:
    path = _answers(tmp_path, "review_version: 1\nanswers: {}\n")
    replies = "banana\npartial\n" + "\n" * (len(ALL_IDS) - 1)
    result = runner.invoke(app, ["review", "--interview", str(path)], input=replies)
    assert result.exit_code == 0, result.output
    assert "answer with one of:" in result.output
    out = yaml.safe_load((tmp_path / "answers.interview.yaml").read_text(encoding="utf-8"))
    assert out["answers"] == {"agents.inventory": "partial"}


def test_interview_keeps_the_draft_line(tmp_path: Path) -> None:
    path = _answers(tmp_path, "review_version: 1\ndraft: true\nanswers:\n  agents.owner: all\n")
    result = runner.invoke(app, ["review", "--interview", str(path)], input=_replies({}))
    assert result.exit_code == 0, result.output
    out = yaml.safe_load((tmp_path / "answers.interview.yaml").read_text(encoding="utf-8"))
    assert out["draft"] is True


def test_interview_with_everything_answered_writes_nothing(tmp_path: Path) -> None:
    body = "".join(f"  {question.id}: unknown\n" for question in QUESTIONS)
    path = _answers(tmp_path, f"review_version: 1\nanswers:\n{body}")
    result = runner.invoke(app, ["review", "--interview", str(path)])
    assert result.exit_code == 0, result.output
    assert "Nothing was written." in result.output
    assert not (tmp_path / "answers.interview.yaml").exists()


def test_interview_never_writes_over_a_file_or_reads_version_0(tmp_path: Path) -> None:
    path = _answers(tmp_path, "review_version: 1\nanswers: {}\n")
    target = tmp_path / "answers.interview.yaml"
    target.write_text("mine\n", encoding="utf-8")
    assert runner.invoke(app, ["review", "--interview", str(path)]).exit_code == 1
    assert target.read_text(encoding="utf-8") == "mine\n"
    same = runner.invoke(app, ["review", "--interview", str(path), "--out", str(path)])
    assert same.exit_code == 1
    old = _answers(tmp_path, "review_version: 0\nanswers: {}\n")
    result = runner.invoke(
        app, ["review", "--interview", str(old), "--out", str(tmp_path / "x.yaml")]
    )
    assert result.exit_code == 1
    assert "review_version 0" in result.output


def test_interview_answers_score_as_asserted(tmp_path: Path) -> None:
    path = _answers(tmp_path, "review_version: 1\nanswers: {}\n")
    replies = _replies({qid: "unknown" for qid in ALL_IDS})
    assert runner.invoke(app, ["review", "--interview", str(path)], input=replies).exit_code == 0
    review = score(load_answers(tmp_path / "answers.interview.yaml"))
    assert review.evidence == 0
    assert all(area.level is Level.NEEDS_VALIDATION for area in review.areas)


def test_draft_and_interview_do_not_combine(tmp_path: Path) -> None:
    path = _answers(tmp_path, "review_version: 1\nanswers: {}\n")
    assert runner.invoke(app, ["review", "--draft", "--interview", str(path)]).exit_code == 1


# Questionnaire init (SPEC 8.7)

QUESTIONNAIRE = """\
questionnaire_version: 1
agents:
  - id: acme.research
    owner: alice
    owner_team: research
    mode: attended
    tools: [broker.quote, broker.order]
  - id: acme.reporting
    owner: bob
    mode: unattended
    tools: [broker.quote]
tools:
  - id: broker.quote
    tier: low
  - id: broker.order
    tier: high
    owner_team: risk
  - id: ledger.adjust
    tier: medium
roles:
  policy_author: [alice]
  escalation_reviewer: [bob, carol]
"""


def _out(tmp_path: Path) -> Path:
    out = tmp_path / "init"
    out.mkdir()
    (out / "questionnaire.yaml").write_text(QUESTIONNAIRE, encoding="utf-8")
    return out


def test_no_input_drafts_from_the_saved_questionnaire(tmp_path: Path) -> None:
    out = _out(tmp_path)
    result = runner.invoke(app, ["init", "--questionnaire", "--out", str(out), "--no-input"])
    assert result.exit_code == 0, result.output
    assert "Read 2 agents and 3 tools." in result.output
    assert "Wrote " + str(out / "questionnaire.yaml") not in result.output
    draft = yaml.safe_load((out / "ritvo.draft.yaml").read_text(encoding="utf-8"))
    assert draft["mode"] == "observe"
    assert draft["profile"] == "local"
    tools = {tool["id"]: tool for tool in draft["tools"]}
    assert tools["broker.order"] == {
        "id": "broker.order",
        "tier": "high",
        "approval_ttl_minutes": 15,
    }
    assert tools["ledger.adjust"]["approval_ttl_minutes"] == 30
    assert "approval_ttl_minutes" not in tools["broker.quote"]
    decisions = {(b["agent"], b["tool"]): b["decision"] for b in draft["bindings"]}
    assert decisions[("acme.research", "broker.quote")] == "allow"
    assert decisions[("acme.research", "broker.order")] == "ask"
    assert ("acme.reporting", "broker.order") not in decisions
    assert draft["roles"] == {
        "escalation_reviewer": ["bob", "carol"],
        "policy_author": ["alice"],
        "cool_off_minutes": 60,
    }
    assert draft["ladders"] == {"high": ["escalation_reviewer"], "timeout_minutes": 30}
    for omitted in ("enforcement", "identity", "secrets", "policy_sections"):
        assert omitted not in draft
    assert "deny" not in (out / "ritvo.draft.yaml").read_text(encoding="utf-8")


def test_no_input_needs_the_file(tmp_path: Path) -> None:
    result = runner.invoke(app, ["init", "--questionnaire", "--out", str(tmp_path), "--no-input"])
    assert result.exit_code == 1
    assert "does not exist" in result.output


def test_never_written_over_a_draft(tmp_path: Path) -> None:
    out = _out(tmp_path)
    (out / "ritvo.draft.yaml").write_text("mine\n", encoding="utf-8")
    result = runner.invoke(app, ["init", "--questionnaire", "--out", str(out), "--no-input"])
    assert result.exit_code == 1
    assert (out / "ritvo.draft.yaml").read_text(encoding="utf-8") == "mine\n"


def test_prompts_save_the_questionnaire_and_draft(tmp_path: Path) -> None:
    out = tmp_path / "init"
    lines = [
        "broker.order",
        "high",
        "risk",  # tool
        "broker.quote",
        "low",
        "",  # tool
        "",  # end of tools
        "acme.research",
        "alice",
        "research",
        "attended",
        "broker.order;broker.quote",
        "",  # end of agents
        "alice",  # policy_author
        "bob",  # escalation_reviewer
        "",
        "",
        "",  # defaults for cool-off, timeout, ladder
    ]
    result = runner.invoke(
        app, ["init", "--questionnaire", "--out", str(out)], input="\n".join(lines) + "\n"
    )
    assert result.exit_code == 0, result.output
    saved = yaml.safe_load((out / "questionnaire.yaml").read_text(encoding="utf-8"))
    assert saved["cool_off_minutes"] == 60
    assert saved["agents"][0]["tools"] == ["broker.order", "broker.quote"]
    assert (out / "ritvo.draft.yaml").exists()
    again = runner.invoke(app, ["init", "--questionnaire", "--out", str(out)])
    assert again.exit_code == 1
    assert "already exists" in again.output


def test_prompts_refuse_a_bad_answer_set_and_write_nothing(tmp_path: Path) -> None:
    out = tmp_path / "init"
    lines = [
        "broker.order",
        "high",
        "",
        "",
        "acme.research",
        "alice",
        "",
        "attended",
        "unknown.tool",
        "",
        "alice",
        "bob",
        "",
        "",
        "",
    ]
    result = runner.invoke(
        app, ["init", "--questionnaire", "--out", str(out)], input="\n".join(lines) + "\n"
    )
    assert result.exit_code == 1
    assert "is not in tools" in result.output
    assert not out.exists()


def _data() -> dict[str, Any]:
    data: dict[str, Any] = yaml.safe_load(QUESTIONNAIRE)
    return data


@pytest.mark.parametrize(
    ("change", "message"),
    [
        (lambda d: d.update(questionnaire_version=2), "questionnaire_version"),
        (lambda d: d["agents"].append(d["agents"][0]), "repeated ID"),
        (lambda d: d["agents"][0].update(owner=None), "agents.0.owner"),
        (lambda d: d["agents"][0].pop("mode"), "agents.0.mode"),
        (lambda d: d["tools"][0].update(tier="unknown"), "tools.0.tier"),
        (lambda d: d["roles"].pop("policy_author"), "roles.policy_author"),
        (lambda d: d["roles"].update(policy_author=[]), "roles.policy_author"),
        (lambda d: d.update(high_ladder=["finance"]), "not a role"),
        (lambda d: d.update(cool_off_minutes=0), "cool_off_minutes"),
        (lambda d: d.update(extra=1), "extra"),
        (lambda d: d["agents"][0].update(owner="Alice Smith"), "agents.0.owner"),
    ],
)
def test_invalid_questionnaires(change, message: str) -> None:  # type: ignore[no-untyped-def]
    data = _data()
    change(data)
    with pytest.raises(QuestionnaireError, match=message):
        parse_questionnaire(data)


def test_errors_do_not_echo_a_value(tmp_path: Path) -> None:
    out = _out(tmp_path)
    text = QUESTIONNAIRE.replace(
        "owner: alice\n    owner_team", "owner: alice@example.org\n    owner_team"
    )
    (out / "questionnaire.yaml").write_text(text, encoding="utf-8")
    result = runner.invoke(app, ["init", "--questionnaire", "--out", str(out), "--no-input"])
    assert result.exit_code == 1
    assert "alice@example.org" not in result.output
    assert not (out / "ritvo.draft.yaml").exists()


def test_init_without_the_flag_is_refused() -> None:
    assert runner.invoke(app, ["init"]).exit_code == 1


def test_the_example_questionnaire_is_valid() -> None:
    example = Path(__file__).resolve().parents[1] / "examples" / "init" / "questionnaire.yaml"
    parse_questionnaire(yaml.safe_load(example.read_text(encoding="utf-8")))
