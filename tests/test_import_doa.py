import shutil
from collections.abc import Callable
from decimal import Decimal
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from ritvo.cli.main import app
from ritvo.diagnostic.doa.draft import Authority, Band, bands_for, build_draft, draft_path
from ritvo.diagnostic.doa.mapping import (
    Unanswered,
    confirm_mapping,
    load_mapping,
    mapping_path,
    no_input,
    save_mapping,
)
from ritvo.diagnostic.doa.matrix import (
    DoaError,
    Kind,
    Layout,
    propose,
    read_cell,
    read_matrix,
    role_id,
)
from ritvo.diagnostic.intake.reader import load_intake

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures" / "doa"

runner = CliRunner()


class Asker:
    """Answers questions by prefix, otherwise takes the default, and records what was asked."""

    def __init__(self, answers: dict[str, str] | None = None) -> None:
        self.answers = answers or {}
        self.asked: list[str] = []

    def __call__(self, question: str, default: str, check: Callable[[str], str | None]) -> str:
        self.asked.append(question)
        for prefix, answer in self.answers.items():
            if question.startswith(prefix):
                assert check(answer) is None, (question, answer, check(answer))
                return answer
        assert check(default) is None, (question, default)
        return default


def _copy(tmp_path: Path, *names: str) -> Path:
    for name in names:
        shutil.copyfile(FIXTURES / name, tmp_path / name)
    return tmp_path / names[0]


def _bands(draft: dict[str, object], decision: str) -> list[dict[str, object]]:
    for tool in draft["tools"]:  # type: ignore[attr-defined]
        if tool["decision"] == decision:
            return tool["approvals"]["bands"]  # type: ignore[no-any-return]
    for entry in draft["unlinked"]:  # type: ignore[attr-defined]
        if entry["decision"] == decision:
            return entry["bands"]  # type: ignore[no-any-return]
    raise AssertionError(decision)


# Reading the file


def test_not_utf8_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "doa.csv"
    path.write_bytes("Decision,CFO\nPayé,5000\n".encode("cp1252"))
    with pytest.raises(DoaError, match="export the sheet again as CSV UTF-8"):
        read_matrix(path)


@pytest.mark.parametrize(("delimiter", "name"), [(",", ","), (";", ";"), ("\t", "tab")])
def test_delimiter_is_detected(tmp_path: Path, delimiter: str, name: str) -> None:
    path = tmp_path / "doa.csv"
    rows = [["Decision", "Manager", "CFO"], ["Payment", "1000", "Unlimited"]]
    path.write_text("\n".join(delimiter.join(row) for row in rows), encoding="utf-8-sig")
    matrix, detected = read_matrix(path)
    assert detected == name
    assert matrix.rows[0] == (1, ("Decision", "Manager", "CFO"))


def test_empty_file_is_refused(tmp_path: Path) -> None:
    (tmp_path / "doa.csv").write_text("\n  \n", encoding="utf-8")
    with pytest.raises(DoaError, match="no rows"):
        read_matrix(tmp_path / "doa.csv")


@pytest.mark.parametrize(
    ("value", "thousands", "kind", "limit", "currency"),
    [
        ("10,000", ",", Kind.LIMIT, 10000, None),
        ("€10,000", ",", Kind.LIMIT, 10000, "EUR"),
        ("10.000 €", ".", Kind.LIMIT, 10000, "EUR"),
        ("USD 2,500.50", ",", Kind.LIMIT, Decimal("2500.50"), "USD"),
        ("1,5m", ".", Kind.LIMIT, 1500000, None),
        ("250k", ",", Kind.LIMIT, 250000, None),
        ("2bn", ",", Kind.LIMIT, 2000000000, None),
        ("Unlimited", ",", Kind.UNLIMITED, None, None),
        ("no limit", ",", Kind.UNLIMITED, None, None),
        ("", ",", Kind.NONE, None, None),
        ("—", ",", Kind.NONE, None, None),
        ("N/A", ",", Kind.NONE, None, None),
        ("0", ",", Kind.NONE, None, None),
        ("A", ",", Kind.CODE, None, None),
        ("10,00", ",", Kind.INVALID, None, None),
        ("see policy", ",", Kind.INVALID, None, None),
    ],
)
def test_read_cell(
    value: str, thousands: str, kind: Kind, limit: object, currency: str | None
) -> None:
    cell = read_cell(value, thousands)
    assert cell.kind == kind
    assert cell.limit == (Decimal(str(limit)) if limit is not None else None)
    assert cell.currency == currency


@pytest.mark.parametrize(
    ("level", "expected"),
    [("VP Finance", "vp_finance"), ("CFO / COO", "cfo_coo"), ("--Head of Ops ", "head_of_ops")],
)
def test_role_id(level: str, expected: str) -> None:
    assert role_id(level) == expected


# Proposals


def test_propose_wide() -> None:
    matrix, _ = read_matrix(FIXTURES / "wide.csv")
    proposal = propose(matrix)
    assert proposal.header_line == 4
    assert proposal.layout == Layout.WIDE
    assert proposal.decision_column == "Decision"
    assert proposal.level_columns == ("Manager", "Director", "CFO", "CEO")
    assert proposal.ignored == ("Ref", "Notes")
    assert (proposal.thousands, proposal.currency, proposal.codes) == (",", None, ("A", "R"))


def test_propose_long() -> None:
    matrix, delimiter = read_matrix(FIXTURES / "long.csv")
    proposal = propose(matrix)
    assert delimiter == ";"
    assert proposal.layout == Layout.LONG
    assert (proposal.level_column, proposal.limit_column) == ("Approver", "Limit")
    assert (proposal.thousands, proposal.currency) == (".", "EUR")


# Bands


def _authorities(*items: tuple[str, int | None]) -> list[Authority]:
    return [
        Authority("d", role, Decimal(limit) if limit is not None else None, order)
        for order, (role, limit) in enumerate(items)
    ]


def test_bands_from_limits() -> None:
    bands = bands_for(
        _authorities(("manager", 10000), ("director", 100000), ("cfo", 1000000), ("ceo", None))
    )
    assert bands == [
        Band(Decimal(10000), ("manager", "director", "cfo", "ceo")),
        Band(Decimal(100000), ("director", "cfo", "ceo")),
        Band(Decimal(1000000), ("cfo", "ceo")),
        Band(None, ("ceo",)),
    ]


def test_bands_without_unlimited_role_end_empty() -> None:
    assert bands_for(_authorities(("lead", 500)))[-1] == Band(None, ())


def test_equal_limits_keep_column_order() -> None:
    bands = bands_for(_authorities(("b", 100), ("a", 100), ("x", None), ("w", None)))
    assert bands == [Band(Decimal(100), ("b", "a", "x", "w")), Band(None, ("x", "w"))]


# The whole import


def _run(
    tmp_path: Path, matrix: str = "wide.csv", answers: dict[str, str] | None = None
) -> tuple[dict[str, object], Asker]:
    path = _copy(tmp_path, matrix, "tools.csv", "people.csv")
    intake = load_intake(tmp_path)
    asker = Asker(answers)
    source, mapping = confirm_mapping(path, load_mapping(mapping_path(path)), intake.tools, asker)
    save_mapping(mapping, mapping_path(path))
    return build_draft(source, mapping, intake.people).data, asker


def test_wide_import(tmp_path: Path) -> None:
    draft, asker = _run(tmp_path, answers={"Currency": "GBP"})
    assert draft["doa_draft_version"] == 1
    assert draft["source"]["file"] == "wide.csv"  # type: ignore[index]
    tools = draft["tools"]
    assert [tool["id"] for tool in tools] == [  # type: ignore[attr-defined]
        "erp.payment_create",
        "zendesk.refund_issue",
    ]
    payment = tools[0]["approvals"]  # type: ignore[index]
    assert payment["parameter"] == "amount" and payment["currency"] == "GBP"
    assert payment["bands"] == [
        {"up_to": 10000, "ladder": ["manager", "director", "cfo", "ceo"]},
        {"up_to": 100000, "ladder": ["director", "cfo", "ceo"]},
        {"up_to": 1000000, "ladder": ["cfo", "ceo"]},
        {"ladder": ["ceo"]},
    ]
    # Refunds were linked by the proposal from tools.csv.
    assert _bands(draft, "Customer refund")[-1] == {"ladder": []}
    # A is approves by default, R is none: the contract goes to CFO, then CEO.
    assert _bands(draft, "New supplier contract") == [{"ladder": ["cfo", "ceo"]}]
    assert draft["roles"] == [
        {"id": "manager", "holders": ["wei"]},
        {"id": "director", "holders": ["amara"]},
        {"id": "cfo", "holders": ["amara"]},
        {"id": "ceo", "holders": []},
    ]
    problems = draft["problems"]
    assert 'decision "Customer refund": no approver above 5000' in problems  # type: ignore[operator]
    assert "role ceo is held by nobody in people.csv" in problems  # type: ignore[operator]
    assert any(question.startswith("Code 'R' means") for question in asker.asked)


def test_long_import(tmp_path: Path) -> None:
    draft, _ = _run(tmp_path, "long.csv")
    payment = _bands(draft, "Supplier payment")
    assert payment == [
        {"up_to": 10000, "ladder": ["manager", "director", "cfo"]},
        {"up_to": 100000, "ladder": ["director", "cfo"]},
        {"up_to": 1500000, "ladder": ["cfo"]},
        {"ladder": []},
    ]
    # The empty decision cells continue the decision above them.
    assert _bands(draft, "Customer refund") == [
        {"up_to": 500, "ladder": ["team_lead", "cfo"]},
        {"ladder": ["cfo"]},
    ]
    tools = {tool["id"]: tool for tool in draft["tools"]}  # type: ignore[attr-defined]
    assert tools["erp.payment_create"]["approvals"]["currency"] == "EUR"


def test_decisions_can_be_left_unlinked(tmp_path: Path) -> None:
    draft, _ = _run(tmp_path, answers={"Tools for decision": "none"})
    assert draft["tools"] == []
    assert [entry["decision"] for entry in draft["unlinked"]] == [  # type: ignore[attr-defined]
        "Supplier payment",
        "Customer refund",
        "New supplier contract",
    ]


def test_two_decisions_on_one_tool_give_no_bands(tmp_path: Path) -> None:
    draft, _ = _run(tmp_path, answers={"Tools for decision": "erp.payment_create"})
    assert draft["tools"] == []
    assert any("is linked to 3 decisions" in p for p in draft["problems"])  # type: ignore[attr-defined]


def test_unknown_tool_is_not_accepted(tmp_path: Path) -> None:
    path = _copy(tmp_path, "wide.csv", "tools.csv")
    intake = load_intake(tmp_path)
    checks: dict[str, Callable[[str], str | None]] = {}

    def ask(question: str, default: str, check: Callable[[str], str | None]) -> str:
        checks.setdefault(question.split(" (")[0], check)
        return default

    confirm_mapping(path, None, intake.tools, ask)
    check = checks["Tools for decision 'Supplier payment'"]
    assert check("erp.unknown") == "not in tools.csv"
    assert check("Not An ID") == "not a valid tool ID"
    assert check("none") is None


def test_same_role_two_limits_uses_the_smaller(tmp_path: Path) -> None:
    path = tmp_path / "doa.csv"
    path.write_text("Decision,Manager,CFO\nPayment,1000,Unlimited\n,500,\n", encoding="utf-8")
    source, mapping = confirm_mapping(path, None, None, Asker())
    draft = build_draft(source, mapping, None).data
    assert _bands(draft, "Payment")[0] == {"up_to": 500, "ladder": ["manager", "cfo"]}
    problems = draft["problems"]
    assert problems == [
        'line 3: decision "Payment" gives manager two different limits; the smaller is used'
    ]


def test_invalid_cells_and_mixed_currencies(tmp_path: Path) -> None:
    path = tmp_path / "doa.csv"
    path.write_text(
        "Decision,Manager,CFO\nPayment,€1000,see the fixture-text-never-copied\n"
        "Refund,$50,Unlimited\n",
        encoding="utf-8",
    )
    source, mapping = confirm_mapping(path, None, None, Asker({"Level columns": "Manager; CFO"}))
    problems = build_draft(source, mapping, None).problems
    assert "line 2, column CFO: not a limit; read as no authority" in problems
    assert any(p.startswith("the matrix mixes currencies (EUR, USD)") for p in problems)
    assert not any("never-copied" in p for p in problems)


# The mapping file


def test_rerun_asks_nothing(tmp_path: Path) -> None:
    _run(tmp_path)
    path = tmp_path / "wide.csv"
    intake = load_intake(tmp_path)
    previous = load_mapping(mapping_path(path))
    _, mapping = confirm_mapping(path, previous, intake.tools, no_input)
    assert mapping == previous


def test_rerun_asks_only_about_new_rows(tmp_path: Path) -> None:
    _run(tmp_path)
    path = tmp_path / "wide.csv"
    with path.open("a", encoding="utf-8") as handle:
        handle.write("Capex,C-01,X,5000,50000,Unlimited,\n")
    asker = Asker()
    confirm_mapping(path, load_mapping(mapping_path(path)), None, asker)
    assert asker.asked == [
        "Code 'X' means (approves or none)",
        "Tools for decision 'Capex' (IDs separated by ';', or none)",
    ]
    with pytest.raises(Unanswered, match="Code 'X' means"):
        confirm_mapping(path, load_mapping(mapping_path(path)), None, no_input)


def test_changed_header_asks_again(tmp_path: Path) -> None:
    _run(tmp_path)
    path = tmp_path / "wide.csv"
    path.write_text(path.read_text(encoding="utf-8").replace("Director", "Head"), encoding="utf-8")
    asker = Asker()
    confirm_mapping(path, load_mapping(mapping_path(path)), None, asker)
    assert asker.asked[0].startswith("Delimiter")


def test_invalid_mapping_file(tmp_path: Path) -> None:
    path = tmp_path / "doa.mapping.yaml"
    path.write_text("doa_mapping_version: 1\n", encoding="utf-8")
    with pytest.raises(DoaError, match="not a valid mapping file"):
        load_mapping(path)


# Command line


def test_cli_interactive_then_unattended(tmp_path: Path) -> None:
    path = _copy(tmp_path, "wide.csv", "tools.csv", "people.csv")
    original = path.read_bytes()
    result = runner.invoke(
        app, ["import-doa", str(path), "--intake", str(tmp_path)], input="\n" * 30
    )
    assert result.exit_code == 0, result.output
    assert "Ignored columns: Ref, Notes" in result.output
    assert "3 decisions, 4 roles, 2 tools linked" in result.output
    assert path.read_bytes() == original
    assert "never-copied" not in result.output
    draft = yaml.safe_load(draft_path(path).read_text(encoding="utf-8"))
    assert [tool["id"] for tool in draft["tools"]] == ["erp.payment_create", "zendesk.refund_issue"]
    assert not (tmp_path / "ritvo.yaml").exists()

    result = runner.invoke(app, ["import-doa", str(path), "--intake", str(tmp_path), "--no-input"])
    assert result.exit_code == 0, result.output
    assert "?" not in result.output.split("Ignored")[0]


def test_cli_reasks_an_invalid_answer(tmp_path: Path) -> None:
    path = _copy(tmp_path, "wide.csv")
    result = runner.invoke(app, ["import-doa", str(path)], input="pipe\n" + "\n" * 30)
    assert result.exit_code == 0, result.output
    assert "answer one of: ,, ;, tab" in result.output


def test_cli_no_input_needs_a_mapping(tmp_path: Path) -> None:
    path = _copy(tmp_path, "wide.csv")
    result = runner.invoke(app, ["import-doa", str(path), "--no-input"])
    assert result.exit_code == 1
    assert "--no-input needs wide.mapping.yaml" in result.output
    assert not draft_path(path).exists()


def test_cli_no_input_with_a_gap_writes_nothing(tmp_path: Path) -> None:
    path = _copy(tmp_path, "wide.csv")
    assert runner.invoke(app, ["import-doa", str(path)], input="\n" * 30).exit_code == 0
    before = draft_path(path).read_bytes()
    with path.open("a", encoding="utf-8") as handle:
        handle.write("Capex,C-01,1000,,,,\n")
    result = runner.invoke(app, ["import-doa", str(path), "--no-input"])
    assert result.exit_code == 1
    assert "not answered by the mapping file" in result.output
    assert draft_path(path).read_bytes() == before


def test_cli_bad_intake(tmp_path: Path) -> None:
    path = _copy(tmp_path, "wide.csv")
    (tmp_path / "tools.csv").write_text("tool_id,tier\nt1,hgih\n", encoding="utf-8")
    result = runner.invoke(app, ["import-doa", str(path), "--intake", str(tmp_path)])
    assert result.exit_code == 1
    assert f"check it with ritvo diagnose {tmp_path}" in result.output
