import re
from pathlib import Path

import pytest
from typer.testing import CliRunner

from ritvo.cli.main import app
from ritvo.diagnostic.intake.columns import AGENTS, INTAKE_FILES, PEOPLE, TOOLS, Access, Mode, Tier
from ritvo.diagnostic.intake.reader import CORRECTED, Intake, IntakeError, fix_intake, load_intake
from ritvo.diagnostic.intake.templates import write_templates

ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = ROOT / "examples" / "intake"
SPEC = ROOT / "docs" / "SPEC.md"

runner = CliRunner()

AGENTS_HEADER = "agent_id,name,platform,built_by_team,owner,owner_team,mode,credential,tools\n"
TOOLS_HEADER = "tool_id,system,access,description,tier,owner_team,approval_ttl_minutes\n"
PEOPLE_HEADER = "person_id,team,roles,deputy\n"


def _load(tmp_path: Path, **files: str) -> Intake:
    for name, text in files.items():
        (tmp_path / f"{name}.csv").write_text(text, encoding="utf-8", newline="")
    return load_intake(tmp_path)


def _error(tmp_path: Path, **files: str) -> str:
    with pytest.raises(IntakeError) as info:
        _load(tmp_path, **files)
    return str(info.value)


# Columns match the spec


def _spec_columns(file_name: str) -> list[tuple[str, bool]]:
    section = SPEC.read_text(encoding="utf-8").split("### 8.2 Intake templates", 1)[1]
    table = section.split(f"`{file_name}`:", 1)[1].split("\n\n", 2)[1]
    rows = []
    for line in table.splitlines()[2:]:
        cells = [cell.strip() for cell in line.strip("|").split("|")]
        rows.append((cells[0].strip("`"), cells[1] == "yes"))
    return rows


def test_columns_match_spec_tables() -> None:
    for file in INTAKE_FILES:
        expected = [(column.name, column.required) for column in file.columns]
        assert _spec_columns(file.name) == expected


def test_fixed_values() -> None:
    assert [mode.value for mode in Mode] == ["attended", "unattended", "both"]
    assert [access.value for access in Access] == ["read", "write"]
    assert [tier.value for tier in Tier] == ["low", "medium", "high"]


# Templates


def test_write_templates(tmp_path: Path) -> None:
    target = tmp_path / "new" / "intake"
    written = write_templates(target)
    assert [path.name for path in written] == ["agents.csv", "tools.csv", "people.csv"]
    assert (target / "agents.csv").read_bytes() == AGENTS_HEADER.encode()
    assert (target / "tools.csv").read_bytes() == TOOLS_HEADER.encode()
    assert (target / "people.csv").read_bytes() == PEOPLE_HEADER.encode()
    intake = load_intake(target)
    assert intake == Intake(agents=(), tools=(), people=())


def test_templates_never_overwrite(tmp_path: Path) -> None:
    (tmp_path / "tools.csv").write_text("mine\n", encoding="utf-8")
    with pytest.raises(IntakeError, match=r"tools\.csv; nothing was written"):
        write_templates(tmp_path)
    assert sorted(path.name for path in tmp_path.iterdir()) == ["tools.csv"]
    assert (tmp_path / "tools.csv").read_text(encoding="utf-8") == "mine\n"


def test_templates_target_is_a_file(tmp_path: Path) -> None:
    target = tmp_path / "file"
    target.write_text("", encoding="utf-8")
    with pytest.raises(IntakeError, match="is not a directory"):
        write_templates(target)


# Reading


def test_example_files_load() -> None:
    intake = load_intake(EXAMPLES)
    assert intake.agents is not None and intake.tools is not None and intake.people is not None
    assert len(intake.agents) == 4
    assert len(intake.tools) == 6
    assert len(intake.people) == 5
    matcher = intake.agents[0]
    assert matcher.agent_id == "exl.ap_matcher"
    assert matcher.owner == "amara"
    assert matcher.mode is Mode.UNATTENDED
    assert matcher.tools == ("erp.invoice_read", "erp.payment_create")
    payment = intake.tools[1]
    assert (payment.tier, payment.access, payment.approval_ttl_minutes) == (
        Tier.HIGH,
        Access.WRITE,
        15,
    )
    assert intake.tools[5].description == "Change a shipment's route, carrier or date"


def test_example_files_are_fictional() -> None:
    for file in INTAKE_FILES:
        text = (EXAMPLES / file.name).read_text(encoding="utf-8")
        assert "@" not in text


def test_absent_files_are_none(tmp_path: Path) -> None:
    intake = _load(tmp_path, agents=AGENTS_HEADER + "a1,,,,someone,,,,t1\n")
    assert intake.tools is None
    assert intake.people is None
    assert intake.agents is not None
    # References into an absent file are not checked.
    assert intake.agents[0].owner == "someone"
    assert intake.agents[0].tools == ("t1",)


def test_empty_and_unknown_mean_not_known(tmp_path: Path) -> None:
    intake = _load(
        tmp_path,
        tools=TOOLS_HEADER + "t1,,unknown,,unknown,,\nt2, , , , , , \n",
    )
    assert intake.tools is not None
    for tool in intake.tools:
        assert tool.access is None
        assert tool.tier is None
        assert tool.system is None
        assert tool.approval_ttl_minutes is None


def test_fixed_values_ignore_case(tmp_path: Path) -> None:
    intake = _load(
        tmp_path,
        agents="agent_id,mode\na1,Attended\na2,UNATTENDED\na3,Unknown\n",
        tools="tool_id,access,tier\nt1,Write,High\n",
    )
    assert intake.agents is not None and intake.tools is not None
    assert [agent.mode for agent in intake.agents] == [Mode.ATTENDED, Mode.UNATTENDED, None]
    assert (intake.tools[0].access, intake.tools[0].tier) == (Access.WRITE, Tier.HIGH)


def test_ids_stay_exact(tmp_path: Path) -> None:
    message = _error(tmp_path, agents="Agent_ID\na1\n")
    assert "line 1, column 1: unknown column" in message


def test_team_ids(tmp_path: Path) -> None:
    intake = _load(
        tmp_path,
        agents="agent_id,built_by_team,owner_team\na1,platform,finance-ops\n",
        tools="tool_id,owner_team\nt1,risk\n",
        people="person_id,team\np1,support\n",
    )
    assert intake.agents is not None and intake.tools is not None and intake.people is not None
    assert (intake.agents[0].built_by_team, intake.agents[0].owner_team) == (
        "platform",
        "finance-ops",
    )
    assert intake.tools[0].owner_team == "risk"
    assert intake.people[0].team == "support"


@pytest.mark.parametrize(
    ("files", "column"),
    [
        ({"agents": "agent_id,built_by_team\na1,Platform\n"}, "built_by_team"),
        ({"agents": "agent_id,owner_team\na1,finance ops\n"}, "owner_team"),
        ({"tools": "tool_id,owner_team\nt1,Risk\n"}, "owner_team"),
        ({"people": "person_id,team\np1,Customer Support\n"}, "team"),
    ],
)
def test_team_must_be_an_id(tmp_path: Path, files: dict[str, str], column: str) -> None:
    message = _error(tmp_path, **files)
    assert f"line 2, column {column}: not a valid ID" in message


def test_format_details(tmp_path: Path) -> None:
    text = '\ufeffroles , deputy,person_id\r\n" a ; b ;; c ;",,p1\r\n,,\r\n\r\nx,p1,p2\r\n'
    intake = _load(tmp_path, people=text)
    assert intake.people is not None
    assert [person.person_id for person in intake.people] == ["p1", "p2"]
    assert intake.people[0].roles == ("a", "b", "c")
    assert intake.people[0].deputy is None
    assert intake.people[1].roles == ("x",)
    assert intake.people[1].deputy == "p1"


def test_short_rows_leave_cells_empty(tmp_path: Path) -> None:
    intake = _load(tmp_path, agents=AGENTS_HEADER + "a1,Agent one\n")
    assert intake.agents is not None
    assert intake.agents[0].name == "Agent one"
    assert intake.agents[0].owner is None


def test_quoted_newline_keeps_line_numbers(tmp_path: Path) -> None:
    message = _error(
        tmp_path,
        tools=TOOLS_HEADER + 't1,,,"two\nlines",,,\nt2,,,,top,,\n',
    )
    assert "tools.csv line 4, column tier" in message


# Errors


@pytest.mark.parametrize(
    ("files", "message"),
    [
        ({"agents": ""}, "agents.csv has no header row"),
        (
            {"agents": "agent_id,colour\n"},
            "agents.csv line 1, column 2: unknown column; valid columns: agent_id, name,",
        ),
        ({"agents": "agent_id,name,name\n"}, "line 1, column 3: repeated column name"),
        ({"tools": "system\n"}, "tools.csv line 1: missing required column tool_id"),
        ({"agents": "agent_id,name\n,x\n"}, "agents.csv line 2, column agent_id: required"),
        ({"agents": "agent_id\nA1\n"}, "column agent_id: not a valid ID"),
        ({"agents": "agent_id\n.a\n"}, "not a valid ID"),
        ({"agents": "agent_id\n" + "a" * 65 + "\n"}, "not a valid ID"),
        (
            {"agents": "agent_id\na1\na2\na1\n"},
            "line 4, column agent_id: duplicate ID 'a1', first on line 2",
        ),
        (
            {"agents": "agent_id,mode\na1,always\n"},
            "must be one of attended, unattended, both, unknown, or empty",
        ),
        (
            {"tools": "tool_id,tier\nt1,critical\n"},
            "must be one of low, medium, high, unknown, or empty",
        ),
        ({"tools": "tool_id,access\nt1,delete\n"}, "must be one of read, write, unknown, or empty"),
        ({"tools": "tool_id,approval_ttl_minutes\nt1,0\n"}, "at least 1"),
        ({"tools": "tool_id,approval_ttl_minutes\nt1,-5\n"}, "at least 1"),
        ({"tools": "tool_id,approval_ttl_minutes\nt1,1.5\n"}, "at least 1"),
        ({"tools": "tool_id,approval_ttl_minutes\nt1,\u00b2\n"}, "at least 1"),
        ({"agents": "agent_id,tools\na1,t1;t1\n"}, "column tools: 't1' is listed twice"),
        ({"agents": "agent_id,tools\na1,t1;T 2\n"}, "column tools: not a valid ID"),
        ({"people": "person_id,roles\np1,Finance Lead\n"}, "column roles: not a valid ID"),
        (
            {"agents": "agent_id\na1,extra\n"},
            "agents.csv line 2: 2 cells but the header has 1 columns",
        ),
        ({"agents": 'agent_id\n"a1\n'}, "agents.csv line 2: not valid CSV"),
        (
            {"agents": "agent_id,owner\na1,bob\n", "people": PEOPLE_HEADER + "alice,,,\n"},
            "agents.csv line 2, column owner: 'bob' is not in people.csv",
        ),
        (
            {"agents": "agent_id,tools\na1,t1;t9\n", "tools": "tool_id\nt1\n"},
            "agents.csv line 2, column tools: 't9' is not in tools.csv",
        ),
        ({"people": "person_id,deputy\np1,p2\n"}, "column deputy: 'p2' is not in people.csv"),
        ({"people": "person_id,deputy\np1,p1\n"}, "a person cannot be their own deputy"),
    ],
)
def test_invalid_files(tmp_path: Path, files: dict[str, str], message: str) -> None:
    assert message in _error(tmp_path, **files)


def test_not_utf8(tmp_path: Path) -> None:
    (tmp_path / "people.csv").write_bytes(b"person_id,team\np1,\xff\n")
    with pytest.raises(IntakeError, match=r"people\.csv is not UTF-8 text"):
        load_intake(tmp_path)


def test_not_a_directory(tmp_path: Path) -> None:
    with pytest.raises(IntakeError, match="is not a directory"):
        load_intake(tmp_path / "missing")


def test_all_problems_reported_together(tmp_path: Path) -> None:
    message = _error(
        tmp_path,
        agents="agent_id,mode\na1,sometimes\n",
        tools="tool_id,tier\nt1,urgent\n",
    )
    assert "agents.csv line 2, column mode" in message
    assert "tools.csv line 2, column tier" in message


def test_errors_never_echo_cell_content(tmp_path: Path) -> None:
    hidden = "Do-Not-Echo-This-Value"
    message = _error(
        tmp_path,
        agents=f"agent_id,{hidden}\na1,x\n",
    )
    assert "line 1, column 2: unknown column" in message
    assert hidden not in message
    message = _error(
        tmp_path,
        agents=(
            "agent_id,name,credential,mode,owner,tools\n"
            f"Agent-{hidden},{hidden},{hidden},{hidden},{hidden},{hidden}\n"
        ),
    )
    assert hidden not in message
    assert "column agent_id" in message
    assert "column mode" in message and "column owner" in message and "column tools" in message


def test_credential_is_kept_as_given(tmp_path: Path) -> None:
    intake = _load(
        tmp_path,
        agents=AGENTS_HEADER + "a1,,,,,,,shared-erp-key,\na2,,,,,,,shared-erp-key,\n",
    )
    assert intake.agents is not None
    assert {agent.credential for agent in intake.agents} == {"shared-erp-key"}


def test_file_names() -> None:
    assert (AGENTS.name, TOOLS.name, PEOPLE.name) == ("agents.csv", "tools.csv", "people.csv")


# CLI


def test_cli_templates(tmp_path: Path) -> None:
    target = tmp_path / "intake"
    result = runner.invoke(app, ["diagnose", "--templates", str(target)])
    assert result.exit_code == 0, result.output
    assert sorted(path.name for path in target.iterdir()) == [
        "agents.csv",
        "people.csv",
        "tools.csv",
    ]
    assert result.output.count("Wrote ") == 3


def test_cli_templates_refuses_existing(tmp_path: Path) -> None:
    (tmp_path / "agents.csv").write_text("mine\n", encoding="utf-8")
    result = runner.invoke(app, ["diagnose", "--templates", str(tmp_path)])
    assert result.exit_code == 1
    assert "error:" in result.output
    assert re.search(r"agents\.csv; nothing was written", result.output)
    assert not (tmp_path / "tools.csv").exists()
    assert (tmp_path / "agents.csv").read_text(encoding="utf-8") == "mine\n"


@pytest.mark.parametrize(
    ("args", "message"),
    [
        ([], "give an intake folder, or --templates <dir>"),
        (["{dir}", "--templates", "{dir}"], "give an intake folder, or --templates <dir>"),
        (["--templates", "{dir}", "--fix"], "--fix needs an intake folder"),
    ],
)
def test_cli_diagnose_needs_one_input(tmp_path: Path, args: list[str], message: str) -> None:
    result = runner.invoke(app, ["diagnose", *(arg.format(dir=tmp_path) for arg in args)])
    assert result.exit_code == 1
    assert message in result.output


# Suggested fixes (docs/SPEC.md section 8.2, Diagnosing intake files)


def _problems(tmp_path: Path, **files: str) -> IntakeError:
    with pytest.raises(IntakeError) as info:
        _load(tmp_path, **files)
    return info.value


def test_header_fix_and_rows_still_checked(tmp_path: Path) -> None:
    error = _problems(tmp_path, agents="Agent ID,Owner-Team,mode\na1,ops,sometimes\n")
    assert error.problems[:2] == (
        "agents.csv line 1, column 1: unknown column; fix: agent_id",
        "agents.csv line 1, column 2: unknown column; fix: owner_team",
    )
    # The rows are read under the fixed header, so their problems show in the same run.
    assert error.problems[2].startswith("agents.csv line 2, column mode:")
    assert error.fixes == 2


def test_header_without_fix_names_closest_column(tmp_path: Path) -> None:
    error = _problems(tmp_path, agents="agent_id,ownr\na1,x\n")
    assert error.problems == (
        "agents.csv line 1, column 2: unknown column; valid columns: "
        "agent_id, name, platform, built_by_team, owner, owner_team, mode, credential, tools"
        "; did you mean 'owner'?",
    )
    assert error.fixes == 0


def test_header_fix_never_repeats_a_column(tmp_path: Path) -> None:
    error = _problems(tmp_path, agents="agent_id,Agent ID\na1,a2\n")
    assert "fix:" not in error.problems[0]


@pytest.mark.parametrize(
    ("cell", "fixed"),
    [("Ops", "ops"), ("Finance Ops", "finance_ops"), ("  AP   Matcher ", "ap_matcher")],
)
def test_id_fixes(tmp_path: Path, cell: str, fixed: str) -> None:
    error = _problems(tmp_path, agents=f"agent_id,owner_team\na1,{cell}\n")
    assert error.problems == (
        "agents.csv line 2, column owner_team: not a valid ID (1 to 64 characters from a-z, "
        f"0-9, '.', '_' and '-', starting with a letter or digit); fix: '{fixed}'",
    )
    assert error.fixes == 1


@pytest.mark.parametrize(
    ("cell", "reason"),
    [
        ("Amara Okafor", "use a person_id, not a full name"),
        ("amara@example.com", "use a person_id, not an email address"),
    ],
)
def test_person_columns_never_turn_names_into_ids(tmp_path: Path, cell: str, reason: str) -> None:
    error = _problems(tmp_path, agents=f"agent_id,owner\na1,{cell}\n")
    assert error.problems[0].endswith(reason)
    assert cell not in error.problems[0]
    assert error.fixes == 0


def test_person_id_case_is_fixed(tmp_path: Path) -> None:
    error = _problems(tmp_path, people="person_id,deputy\nAmara,wei\nwei,\n")
    assert error.problems[0].endswith("fix: 'amara'")


def test_email_is_never_an_id_in_other_columns(tmp_path: Path) -> None:
    error = _problems(tmp_path, agents="agent_id,owner_team\na1,Ops@example.com\n")
    assert "fix:" not in error.problems[0]


def test_list_fixes(tmp_path: Path) -> None:
    error = _problems(tmp_path, people="person_id,roles\np1,Policy Author; finance_approver\n")
    assert error.problems[0].endswith("fix: 'policy_author'")
    assert error.fixes == 1


@pytest.mark.parametrize(
    ("files", "hint"),
    [
        ({"agents": "agent_id,mode\na1,atended\n"}, "did you mean 'attended'?"),
        ({"tools": "tool_id,tier\nt1,hihg\n"}, "did you mean 'high'?"),
        ({"tools": "tool_id,access\nt1,wirte\n"}, "did you mean 'write'?"),
        (
            {"agents": "agent_id,owner\na1,amra\n", "people": "person_id\namara\n"},
            "'amra' is not in people.csv; did you mean 'amara'?",
        ),
        (
            {"people": "person_id,deputy\namara,wie\nwei,\n"},
            "'wie' is not in people.csv; did you mean 'wei'?",
        ),
        (
            {
                "agents": "agent_id,tools\na1,erp.invoice_red\n",
                "tools": "tool_id\nerp.invoice_read\n",
            },
            "'erp.invoice_red' is not in tools.csv; did you mean 'erp.invoice_read'?",
        ),
    ],
)
def test_did_you_mean(tmp_path: Path, files: dict[str, str], hint: str) -> None:
    error = _problems(tmp_path, **files)
    assert error.problems[0].endswith(hint)
    # A suggestion is never applied.
    assert error.fixes == 0


def test_no_hint_when_nothing_is_close(tmp_path: Path) -> None:
    error = _problems(tmp_path, tools="tool_id,tier\nt1,catastrophic\n")
    assert "did you mean" not in error.problems[0]


# The corrected copy


BROKEN_AGENTS = (
    "Agent ID,name,owner,mode\n"
    'AP Matcher,"Invoice\nmatcher",Amara,unattended\n'
    "\n"
    "b1,,Amara Okafor,atended\n"
    "c1,,,,extra\n"
)


def test_fix_writes_a_corrected_copy(tmp_path: Path) -> None:
    (tmp_path / "agents.csv").write_text(BROKEN_AGENTS, encoding="utf-8", newline="")
    (tmp_path / "tools.csv").write_bytes(b"\xff\xfe not utf-8")
    original = (tmp_path / "agents.csv").read_bytes()
    corrected = fix_intake(tmp_path)
    assert corrected is not None
    assert corrected.directory == tmp_path / CORRECTED
    assert [path.name for path in corrected.files] == ["agents.csv", "tools.csv"]
    assert corrected.fixes == 3
    assert (tmp_path / "agents.csv").read_bytes() == original
    # A file that could not be parsed is copied byte for byte; an absent file stays absent.
    assert (tmp_path / CORRECTED / "tools.csv").read_bytes() == b"\xff\xfe not utf-8"
    assert not (tmp_path / CORRECTED / "people.csv").exists()
    text = (tmp_path / CORRECTED / "agents.csv").read_bytes().decode("utf-8")
    assert text == (
        "agent_id,name,owner,mode\r\n"
        'ap_matcher,"Invoice\nmatcher",amara,unattended\r\n'
        "\r\n"
        "b1,,Amara Okafor,atended\r\n"
        "c1,,,,extra\r\n"
    )
    assert any("not UTF-8" in problem for problem in corrected.remaining)
    assert any("not a full name" in problem for problem in corrected.remaining)
    assert any("did you mean 'attended'?" in problem for problem in corrected.remaining)


def test_fixed_copy_reads_clean(tmp_path: Path) -> None:
    _write = (tmp_path / "agents.csv").write_text
    _write("Agent ID,Owner Team\nAP Matcher,Finance\n", encoding="utf-8")
    corrected = fix_intake(tmp_path)
    assert corrected is not None and corrected.remaining == ()
    intake = load_intake(corrected.directory)
    assert intake.agents is not None
    assert [(a.agent_id, a.owner_team) for a in intake.agents] == [("ap_matcher", "finance")]


def test_fix_refuses_an_existing_copy(tmp_path: Path) -> None:
    (tmp_path / "agents.csv").write_text("agent_id\nA1\n", encoding="utf-8")
    (tmp_path / CORRECTED).mkdir()
    with pytest.raises(IntakeError, match="already exists; nothing was written"):
        fix_intake(tmp_path)
    assert list((tmp_path / CORRECTED).iterdir()) == []


def test_fix_writes_nothing_without_a_fix(tmp_path: Path) -> None:
    (tmp_path / "agents.csv").write_text("agent_id,mode\na1,sometimes\n", encoding="utf-8")
    assert fix_intake(tmp_path) is None
    assert not (tmp_path / CORRECTED).exists()


def test_fixes_never_echo_free_text(tmp_path: Path) -> None:
    hidden = "Hidden Free Text Value"
    error = _problems(
        tmp_path,
        agents=f"agent_id,name,credential,platform\nA 1,{hidden},{hidden},{hidden}\n",
    )
    assert all(hidden.lower() not in problem.lower() for problem in error.problems)


# diagnose <folder>


def _intake(tmp_path: Path) -> Path:
    target = tmp_path / "intake"
    target.mkdir()
    for path in EXAMPLES.glob("*.csv"):
        (target / path.name).write_bytes(path.read_bytes())
    return target


def test_cli_diagnose_valid_intake(tmp_path: Path) -> None:
    target = _intake(tmp_path)
    result = runner.invoke(app, ["diagnose", str(target)])
    assert result.exit_code == 0, result.output
    assert result.output.splitlines() == [
        "agents.csv  4 agents, 2 with no owner",
        "tools.csv  6 tools, 2 with no tier",
        "people.csv  5 people",
        "Nothing was written. The draft ritvo.yaml and the gap report are not built yet.",
    ]
    assert sorted(path.name for path in target.iterdir()) == [
        "agents.csv",
        "people.csv",
        "tools.csv",
    ]


def test_cli_diagnose_absent_files(tmp_path: Path) -> None:
    (tmp_path / "agents.csv").write_text("agent_id\na1\n", encoding="utf-8")
    result = runner.invoke(app, ["diagnose", str(tmp_path)])
    assert result.exit_code == 0, result.output
    assert "tools.csv  not given; tool references not checked" in result.output
    assert "people.csv  not given; owner and deputy references not checked" in result.output


def test_cli_diagnose_no_intake_files(tmp_path: Path) -> None:
    result = runner.invoke(app, ["diagnose", str(tmp_path)])
    assert result.exit_code == 1
    assert "no intake files in" in result.output


def test_cli_diagnose_errors_suggest_fix(tmp_path: Path) -> None:
    target = _intake(tmp_path)
    agents = (target / "agents.csv").read_text(encoding="utf-8")
    (target / "agents.csv").write_text(
        agents.replace("exl.research", "Exl.Research").replace(",both,", ",bth,"),
        encoding="utf-8",
    )
    result = runner.invoke(app, ["diagnose", str(target)])
    assert result.exit_code == 1
    assert "fix: 'exl.research'" in result.output
    assert "did you mean 'both'?" in result.output
    assert "1 of 2 problems have a mechanical fix; run again with --fix" in result.output
    assert "Nothing was written." in result.output
    assert not (target / CORRECTED).exists()


def test_cli_diagnose_fix(tmp_path: Path) -> None:
    target = _intake(tmp_path)
    agents = (target / "agents.csv").read_text(encoding="utf-8")
    (target / "agents.csv").write_text(
        agents.replace("exl.research", "Exl.Research"), encoding="utf-8"
    )
    result = runner.invoke(app, ["diagnose", str(target), "--fix"])
    assert result.exit_code == 0, result.output
    assert "1 fixes applied; the original files were not changed." in result.output
    assert "agents.csv  4 agents, 2 with no owner" in result.output
    assert sorted(path.name for path in (target / CORRECTED).iterdir()) == [
        "agents.csv",
        "people.csv",
        "tools.csv",
    ]
    assert "Exl.Research" in (target / "agents.csv").read_text(encoding="utf-8")


def test_cli_diagnose_fix_with_remaining_problems(tmp_path: Path) -> None:
    (tmp_path / "agents.csv").write_text("agent_id,mode\nA1,bth\n", encoding="utf-8")
    result = runner.invoke(app, ["diagnose", str(tmp_path), "--fix"])
    assert result.exit_code == 1
    assert "1 problems remain in" in result.output


def test_cli_diagnose_fix_without_a_fix(tmp_path: Path) -> None:
    (tmp_path / "agents.csv").write_text("agent_id,mode\na1,bth\n", encoding="utf-8")
    result = runner.invoke(app, ["diagnose", str(tmp_path), "--fix"])
    assert result.exit_code == 1
    assert "No problem has a mechanical fix. Nothing was written." in result.output
    assert not (tmp_path / CORRECTED).exists()


def test_cli_diagnose_fix_on_valid_intake(tmp_path: Path) -> None:
    target = _intake(tmp_path)
    result = runner.invoke(app, ["diagnose", str(target), "--fix"])
    assert result.exit_code == 0
    assert "Nothing to fix." in result.output
    assert not (target / CORRECTED).exists()
