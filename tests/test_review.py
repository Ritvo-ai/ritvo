import ast
import hashlib
import re
from datetime import date
from pathlib import Path

import pytest
from typer.testing import CliRunner

from ritvo.cli.main import app
from ritvo.diagnostic.review.questions import AREAS, QUESTIONS, UNKNOWN, Level
from ritvo.diagnostic.review.report import render_html
from ritvo.diagnostic.review.scoring import AreaResult, Review, ReviewError, load_answers, score

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / "examples" / "review" / "answers.yaml"
SPEC = ROOT / "docs" / "SPEC.md"

runner = CliRunner()


def _write(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "answers.yaml"
    path.write_text(text, encoding="utf-8")
    return path


def _review(tmp_path: Path, answers: dict[str, str], extra: str = "") -> Review:
    lines = ["review_version: 0", "answers:"]
    lines += [f'  {qid}: "{value}"' for qid, value in answers.items()]
    if not answers:
        lines[-1] = "answers: {}"
    return score(load_answers(_write(tmp_path, "\n".join(lines) + "\n" + extra)))


def _area(review: Review, area_id: str) -> AreaResult:
    return next(area for area in review.areas if area.area.id == area_id)


# Questions match the spec


QUESTIONS_PER_AREA = {"agents": 4, "approval": 3, "escalation": 4, "policy_change": 3, "record": 3}


def test_seventeen_questions_in_five_areas() -> None:
    assert len(QUESTIONS) == 17
    for area in AREAS:
        in_area = [q for q in QUESTIONS if q.area == area]
        assert len(in_area) == QUESTIONS_PER_AREA[area.id]
        assert all(q.id.startswith(area.id + ".") for q in in_area)
    for question in QUESTIONS:
        levels = list(question.options.values())
        assert levels.count(Level.GAP) == 2  # the gap option and unknown
        assert levels.count(Level.PARTIAL) == 1
        assert levels.count(Level.IN_PLACE) == 1
        assert question.options[UNKNOWN] is Level.GAP
        assert question.why and question.next_step


def test_questions_match_spec_table() -> None:
    section = SPEC.read_text(encoding="utf-8").split("### 8.1 Gap review", 1)[1]
    section = section.split("\n## ", 1)[0]
    rows = re.findall(
        r"^\| `([a-z_]+\.[a-z_]+)` \| (.+?) \| `([a-z_]+)` \| `([a-z_]+)` \| `([a-z_]+)` \|$",
        section,
        flags=re.MULTILINE,
    )
    assert len(rows) == 17
    for (qid, text, gap, partial, in_place), question in zip(rows, QUESTIONS, strict=True):
        assert qid == question.id
        assert text == question.text
        assert question.options[gap] is Level.GAP
        assert question.options[partial] is Level.PARTIAL
        assert question.options[in_place] is Level.IN_PLACE


def test_guidance_wording() -> None:
    for question in QUESTIONS:
        for text in (question.text, question.why, question.next_step):
            assert "reach" not in text
            assert "acts for" not in text
            assert "acted for" not in text
    by_id = {question.id: question for question in QUESTIONS}
    second_person = by_id["policy_change.second_person"].next_step
    assert "accepted risk" in second_person
    assert "not a substitute" in second_person
    assert "add a waiting period" not in second_person
    assert "refused, not approved" in by_id["escalation.path"].next_step
    assert "must match the approved request" in by_id["escalation.expiry"].next_step


# Scoring


def test_area_takes_weakest_answer(tmp_path: Path) -> None:
    review = _review(
        tmp_path,
        {"agents.inventory": "complete", "agents.owner": "some", "agents.reach": "none"},
    )
    assert _area(review, "agents").level is Level.GAP


def test_all_in_place(tmp_path: Path) -> None:
    review = _review(
        tmp_path,
        {
            "agents.inventory": "complete",
            "agents.owner": "all",
            "agents.reach": "all",
            "agents.new_tools": "needs_decision",
        },
    )
    area = _area(review, "agents")
    assert area.level is Level.IN_PLACE
    assert not area.incomplete


def test_incomplete_area_is_capped_at_partial(tmp_path: Path) -> None:
    review = _review(tmp_path, {"agents.inventory": "complete", "agents.owner": "all"})
    area = _area(review, "agents")
    assert area.level is Level.PARTIAL
    assert area.incomplete
    assert area.answered == 2


def test_new_questions_score(tmp_path: Path) -> None:
    review = _review(tmp_path, {"agents.new_tools": "automatic", "escalation.stop": "informal"})
    new_tools = _area(review, "agents").questions[3]
    stop = _area(review, "escalation").questions[3]
    assert (new_tools.question.id, new_tools.level) == ("agents.new_tools", Level.GAP)
    assert (stop.question.id, stop.level) == ("escalation.stop", Level.PARTIAL)


def test_incomplete_area_keeps_a_gap(tmp_path: Path) -> None:
    review = _review(tmp_path, {"agents.inventory": "none"})
    assert _area(review, "agents").level is Level.GAP


def test_unanswered_area(tmp_path: Path) -> None:
    review = _review(tmp_path, {})
    for area in review.areas:
        assert area.level is Level.NOT_ANSWERED
    assert review.answered == 0


def test_unknown_scores_gap(tmp_path: Path) -> None:
    review = _review(tmp_path, {"record.tamper": "unknown"})
    result = _area(review, "record").questions[1]
    assert result.level is Level.GAP


def test_bare_yes_and_no_are_options(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        "review_version: 0\nanswers:\n  approval.self: yes\n  record.tamper: no\n",
    )
    answers = load_answers(path)
    assert answers.answers == {"approval.self": "yes", "record.tamper": "no"}
    review = score(answers)
    assert _area(review, "approval").questions[1].level is Level.GAP
    assert _area(review, "record").questions[1].level is Level.IN_PLACE


def test_null_answer_is_not_answered(tmp_path: Path) -> None:
    path = _write(tmp_path, "review_version: 0\nanswers:\n  agents.owner:\n")
    assert load_answers(path).answers == {}


def test_hash_is_of_file_bytes(tmp_path: Path) -> None:
    path = _write(tmp_path, "review_version: 0\n")
    assert load_answers(path).sha256 == hashlib.sha256(path.read_bytes()).hexdigest()


def test_example_file_scores() -> None:
    review = score(load_answers(EXAMPLE))
    levels = {area.area.id: area.level for area in review.areas}
    assert levels == {
        "agents": Level.GAP,
        "approval": Level.PARTIAL,
        "escalation": Level.GAP,
        "policy_change": Level.PARTIAL,
        "record": Level.GAP,
    }
    assert review.answered == 16
    assert review.organisation == "Example Logistics Ltd"


# Validation


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("review_version: 1\n", "review_version must be 0"),
        ("review_version: false\n", "review_version"),
        ("review_version: 0.0\n", "review_version"),
        ('review_version: "0"\n', "review_version"),
        ("answers: {}\n", "review_version"),
        ("review_version: 0\nextra: 1\n", "extra"),
        ("- 1\n- 2\n", "must be a YAML mapping"),
        ("review_version: 0\nanswers: [\n", "not valid YAML at line"),
        ("review_version: 0\nanswers:\n  agents.colour: none\n", "unknown question ID"),
        ("review_version: 0\nanswers:\n  agents.owner: most\n", "valid options: none, some, all"),
        ("review_version: 0\nnotes:\n  nope: text\n", "unknown question ID 'nope' in notes"),
        ("review_version: 0\nanswers:\n  agents.owner: 3\n", "answers.agents.owner"),
    ],
)
def test_invalid_files(tmp_path: Path, text: str, message: str) -> None:
    with pytest.raises(ReviewError, match=re.escape(message)):
        load_answers(_write(tmp_path, text))


def test_unknown_id_error_names_valid_ids(tmp_path: Path) -> None:
    with pytest.raises(ReviewError) as info:
        load_answers(_write(tmp_path, "review_version: 0\nanswers:\n  x.y: none\n"))
    assert all(question.id in str(info.value) for question in QUESTIONS)


def test_not_utf8(tmp_path: Path) -> None:
    path = tmp_path / "answers.yaml"
    path.write_bytes(b"review_version: 0\norganisation: \xff\n")
    with pytest.raises(ReviewError, match="not UTF-8"):
        load_answers(path)


def test_yaml_error_does_not_echo_content(tmp_path: Path) -> None:
    with pytest.raises(ReviewError) as info:
        load_answers(_write(tmp_path, "review_version: 0\nsecret-ish: [unclosed\n"))
    assert "secret-ish" not in str(info.value)
    assert "unclosed" not in str(info.value)


# Report


def _html(tmp_path: Path, extra: str = "") -> str:
    path = _write(
        tmp_path,
        "review_version: 0\norganisation: Example Co\nanswers:\n  agents.owner: some\n" + extra,
    )
    return render_html(score(load_answers(path)), date(2026, 10, 14))


def test_report_is_self_contained(tmp_path: Path) -> None:
    html = _html(tmp_path)
    lowered = html.lower()
    for forbidden in (
        "<script",
        "<link",
        "<img",
        "<iframe",
        "@import",
        "url(",
        "http://",
        "https://",
    ):
        assert forbidden not in lowered
    assert "font-family" in html


def test_report_contents(tmp_path: Path) -> None:
    html = _html(tmp_path)
    digest = hashlib.sha256((tmp_path / "answers.yaml").read_bytes()).hexdigest()
    assert digest in html
    assert "2026-10-14" in html
    assert "Example Co" in html
    assert "1 of 17 questions answered" in html
    assert "not a conformity assessment" in html
    for area in AREAS:
        assert area.title in html
    for label in ("Gap", "Partial", "In place", "Not answered"):
        assert label in html
    # Every question not in place is listed with its why and next step.
    for question in QUESTIONS:
        assert question.next_step.replace("'", "&#39;") in html


def test_report_never_contains_the_path(tmp_path: Path) -> None:
    html = _html(tmp_path)
    assert str(tmp_path) not in html
    assert "answers.yaml" not in html


def test_report_escapes_notes_and_organisation(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        'review_version: 0\norganisation: "<b>Org</b>"\nanswers:\n  agents.owner: some\n'
        'notes:\n  agents.owner: "<script>alert(1)</script>"\n',
    )
    html = render_html(score(load_answers(path)), date(2026, 10, 14))
    assert "<script" not in html
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html
    assert "&lt;b&gt;Org&lt;/b&gt;" in html


def test_every_level_has_a_shape(tmp_path: Path) -> None:
    html = _html(tmp_path)
    for level in Level:
        chips = re.findall(rf'<span class="chip st-{level.value}"><svg[^>]*>', html)
        assert chips, level


# CLI


def test_cli_writes_beside_answers(tmp_path: Path) -> None:
    path = _write(tmp_path, EXAMPLE.read_text(encoding="utf-8"))
    result = runner.invoke(app, ["review", str(path)])
    assert result.exit_code == 0, result.output
    assert (tmp_path / "review.html").is_file()
    assert "Agents and what they can do" in result.output
    assert "How the rules change              Partial (2 of 3 answered)" in result.output
    assert "Wrote" in result.output


def test_cli_out(tmp_path: Path) -> None:
    out = tmp_path / "elsewhere.html"
    result = runner.invoke(app, ["review", str(EXAMPLE), "--out", str(out)])
    assert result.exit_code == 0, result.output
    assert out.is_file()


def test_cli_invalid_file_writes_nothing(tmp_path: Path) -> None:
    path = _write(tmp_path, "review_version: 0\nanswers:\n  agents.owner: most\n")
    result = runner.invoke(app, ["review", str(path)])
    assert result.exit_code == 1
    assert "error:" in result.output
    assert not (tmp_path / "review.html").exists()


def test_cli_missing_file(tmp_path: Path) -> None:
    result = runner.invoke(app, ["review", str(tmp_path / "missing.yaml")])
    assert result.exit_code != 0


# The diagnostic never connects (docs/SPEC.md section 3)

NETWORK_MODULES = {
    "socket",
    "ssl",
    "http",
    "urllib",
    "httpx",
    "requests",
    "aiohttp",
    "ftplib",
    "smtplib",
}


def test_diagnostic_imports_no_network_modules() -> None:
    package = ROOT / "ritvo" / "diagnostic"
    for source in package.rglob("*.py"):
        tree = ast.parse(source.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            names: list[str] = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            for name in names:
                assert name.split(".")[0] not in NETWORK_MODULES, f"{source}: {name}"
