import csv
import io
import json
import re
from pathlib import Path
from typing import Any

import pytest
import yaml
from typer.testing import CliRunner

from ritvo.cli.main import app
from ritvo.diagnostic.doa.draft import read_draft
from ritvo.diagnostic.doa.matrix import DoaError
from ritvo.diagnostic.gaps.model import Diagnosis
from ritvo.diagnostic.gaps.rules import RULES, Inputs, diagnose
from ritvo.diagnostic.intake.reader import IntakeError, load_intake
from ritvo.diagnostic.report.gap_report import render_html
from ritvo.diagnostic.report.matrix import cell, matrix_text
from ritvo.diagnostic.report.write import DIAGNOSIS, DRAFT, MATRIX, REPORT, write_diagnosis

runner = CliRunner()
SHA = "ab" * 32

# A fictional organisation. "shared-key" is the credential two agents use; its name must
# never appear in any output.
AGENTS = (
    "agent_id,name,owner,owner_team,mode,credential,tools\n"
    "a.pay,Payer,amara,finance,unattended,shared-key,erp.pay;erp.read\n"
    "a.dispatch,Dispatch,,ops,unattended,shared-key,erp.read;tms.update\n"
    "a.help,Help,wei,support,attended,,mail.send\n"
)
TOOLS = (
    "tool_id,system,access,description,tier,owner_team,approval_ttl_minutes\n"
    "erp.pay,erp,write,Pay a supplier,high,finance,15\n"
    "erp.read,erp,read,Read invoices,low,finance,\n"
    "tms.update,tms,write,Change a shipment,medium,ops,\n"
    "mail.send,mail,write,Send mail,,support,\n"
)
PEOPLE = (
    "person_id,team,roles,deputy\n"
    "amara,finance,policy_author,\n"
    "amara,,finance_approver,\n"
    "wei,finance,finance_approver;escalation_reviewer,tomas\n"
    "tomas,security,security_reviewer,\n"
)


def _doa(**changes: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "doa_draft_version": 1,
        "source": {"file": "doa.csv", "sha256": SHA},
        "roles": [{"id": "finance_approver", "holders": ["amara", "wei"]}],
        "tools": [
            {
                "id": "erp.pay",
                "decision": "Supplier payment",
                "approvals": {
                    "parameter": "amount",
                    "currency": "EUR",
                    "bands": [
                        {"up_to": 10000, "ladder": ["finance_approver"]},
                        {"ladder": ["finance_approver"]},
                    ],
                },
            }
        ],
        "unlinked": [],
        "problems": [],
    }
    data.update(changes)
    return data


def _folder(tmp_path: Path, **files: str) -> Path:
    target = tmp_path / "intake"
    target.mkdir(parents=True, exist_ok=True)
    for name, text in files.items():
        (target / f"{name}.csv").write_text(text, encoding="utf-8", newline="")
    return target


def _all(tmp_path: Path) -> Path:
    return _folder(tmp_path, agents=AGENTS, tools=TOOLS, people=PEOPLE)


def _diagnosis(folder: Path, doa: dict[str, Any] | None = None) -> Diagnosis:
    path = None
    if doa is not None:
        path = folder.parent / "doa.draft.yaml"
        path.write_text(yaml.safe_dump(doa), encoding="utf-8")
    return diagnose(
        Inputs(load_intake(folder), read_draft(path) if path else None, ()),
        "2026-10-30T09:00:00Z",
    )


def _findings(diagnosis: Diagnosis, rule: str) -> list[tuple[str, str]]:
    return [(f.level, f.subject) for f in diagnosis.findings if f.rule == rule]


# people.csv: one person on several rows


def test_a_person_may_repeat_and_their_rows_merge(tmp_path: Path) -> None:
    people = load_intake(_all(tmp_path)).people
    assert people is not None
    amara = next(p for p in people if p.person_id == "amara")
    assert (amara.team, amara.roles) == ("finance", ("policy_author", "finance_approver"))
    assert [p.person_id for p in people] == ["amara", "wei", "tomas"]


@pytest.mark.parametrize(
    ("people", "message"),
    [
        (
            "person_id,team,roles\nx,finance,a\nx,ops,b\n",
            "people.csv line 3, column team: 'ops' differs from 'finance' on line 2",
        ),
        (
            "person_id,roles,deputy\nx,a,y\nx,b,z\ny,,\nz,,\n",
            "people.csv line 3, column deputy: 'z' differs from 'y' on line 2",
        ),
        (
            "person_id,roles\nx,a\nx,a;b\n",
            "people.csv line 3, column roles: 'a' is already given for this person",
        ),
    ],
)
def test_rows_of_one_person_must_agree(tmp_path: Path, people: str, message: str) -> None:
    folder = _folder(tmp_path, people=people)
    with pytest.raises(IntakeError) as error:
        load_intake(folder)
    assert message in error.value.problems


# The DoA draft


def test_doa_draft_is_read_back(tmp_path: Path) -> None:
    path = tmp_path / "x.draft.yaml"
    path.write_text(yaml.safe_dump(_doa()), encoding="utf-8")
    draft = read_draft(path)
    assert draft.tools[0].approvals.bands[1].up_to is None
    assert draft.roles[0].holders == ["amara", "wei"]


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("not: [valid", "x.draft.yaml is not a YAML file"),
        (yaml.safe_dump(_doa(doa_draft_version=2)), "doa_draft_version must be 1"),
        (yaml.safe_dump({**_doa(), "extra": 1}), "not a valid DoA draft (extra)"),
        (yaml.safe_dump({**_doa(), "tools": [{"id": 1}]}), "tools.0.id"),
    ],
)
def test_doa_draft_that_is_not_valid_is_refused(tmp_path: Path, text: str, message: str) -> None:
    path = tmp_path / "x.draft.yaml"
    path.write_text(text, encoding="utf-8")
    with pytest.raises(DoaError) as error:
        read_draft(path)
    assert message in str(error.value)


# The gap rules


def test_rules_with_everything_given(tmp_path: Path) -> None:
    diagnosis = _diagnosis(_all(tmp_path), _doa())
    assert _findings(diagnosis, "approver.none") == [("gap", "tms.update")]
    assert _findings(diagnosis, "approver.self") == [
        ("gap", "a.pay"),
        ("needs_validation", "a.dispatch"),
    ]
    assert _findings(diagnosis, "escalation.none") == []
    assert _findings(diagnosis, "expiry.none") == [("needs_validation", "tms.update")]
    assert _findings(diagnosis, "policy.one_person") == []
    assert _findings(diagnosis, "tier.high_allow") == [
        ("gap", "erp.pay"),
        ("needs_validation", "mail.send"),
    ]
    assert _findings(diagnosis, "credential.shared") == [("gap", "a.dispatch+a.pay")]
    assert _findings(diagnosis, "unattended.no_owner") == [("gap", "a.dispatch")]


def test_a_rule_that_needs_a_missing_file_says_so_once(tmp_path: Path) -> None:
    diagnosis = _diagnosis(_all(tmp_path))
    for rule in ("approver.none", "approver.self", "escalation.none"):
        [finding] = [f for f in diagnosis.findings if f.rule == rule]
        assert (finding.level, finding.subject) == ("needs_validation", "*")
        assert finding.needs == ["give the DoA draft"]
        assert "the DoA draft was not given" in finding.saw
    folder = _folder(tmp_path / "x", agents=AGENTS)
    diagnosis = _diagnosis(folder)
    [policy] = [f for f in diagnosis.findings if f.rule == "policy.one_person"]
    assert policy.needs == ["give people.csv"]
    assert [f.rule for f in diagnosis.findings if f.subject == "*"].count("expiry.none") == 1


def test_empty_cells_are_never_a_gap(tmp_path: Path) -> None:
    diagnosis = _diagnosis(_all(tmp_path), _doa())
    expiry = next(f for f in diagnosis.findings if f.rule == "expiry.none")
    assert expiry.level == "needs_validation"
    assert expiry.needs == ["approval_ttl_minutes in tools.csv"]
    assert not any(f.rule == "expiry.none" and f.level == "gap" for f in diagnosis.findings)


def test_owner_that_is_unknown_makes_approver_self_not_known(tmp_path: Path) -> None:
    folder = _folder(
        tmp_path,
        agents="agent_id,tools\nx,erp.pay\n",
        tools="tool_id,tier\nerp.pay,high\n",
        people=PEOPLE,
    )
    diagnosis = _diagnosis(folder, _doa())
    assert _findings(diagnosis, "approver.self") == [("needs_validation", "x")]


def test_policy_one_person(tmp_path: Path) -> None:
    both = _folder(tmp_path, people="person_id,roles\nx,policy_author;escalation_reviewer\n")
    assert _findings(_diagnosis(both), "policy.one_person") == [("gap", "policy_author")]
    neither = _folder(tmp_path, people="person_id,roles\nx,other\n")
    assert _findings(_diagnosis(neither), "policy.one_person") == [
        ("gap", "escalation_reviewer"),
        ("gap", "policy_author"),
    ]
    two = _folder(tmp_path, people="person_id,roles\nx,policy_author\ny,escalation_reviewer\n")
    assert _findings(_diagnosis(two), "policy.one_person") == []


def test_escalation_counts_deputies(tmp_path: Path) -> None:
    folder = _folder(
        tmp_path,
        agents="agent_id,tools\nx,erp.pay\n",
        tools="tool_id,tier,approval_ttl_minutes\nerp.pay,high,15\n",
        people="person_id,roles,deputy\nboss,finance_approver,\n",
    )
    doa = _doa(roles=[{"id": "finance_approver", "holders": ["boss"]}])
    assert _findings(_diagnosis(folder, doa), "escalation.none") == [("gap", "erp.pay")]
    folder = _folder(
        tmp_path,
        agents="agent_id,tools\nx,erp.pay\n",
        tools="tool_id,tier,approval_ttl_minutes\nerp.pay,high,15\n",
        people="person_id,roles,deputy\nboss,finance_approver,second\nsecond,,\n",
    )
    assert _findings(_diagnosis(folder, doa), "escalation.none") == []


def test_unattended_with_no_owner_and_unknown_mode(tmp_path: Path) -> None:
    folder = _folder(
        tmp_path, agents="agent_id,mode,owner\na,both,\nb,,\nc,attended,\nd,unattended,p\n"
    )
    assert _findings(_diagnosis(folder), "unattended.no_owner") == [
        ("gap", "a"),
        ("needs_validation", "b"),
    ]


def test_a_tool_no_agent_uses_is_not_evaluated(tmp_path: Path) -> None:
    folder = _folder(tmp_path, agents="agent_id,tools\nx,\n", tools="tool_id,tier\nerp.pay,high\n")
    assert _findings(_diagnosis(folder), "tier.high_allow") == []


# The draft


def _draft(tmp_path: Path, doa: dict[str, Any] | None = None) -> dict[str, Any]:
    out = tmp_path / "out"
    folder = _all(tmp_path)
    args = ["diagnose", str(folder), "--out", str(out)]
    if doa is not None:
        (tmp_path / "doa.draft.yaml").write_text(yaml.safe_dump(doa), encoding="utf-8")
        args += ["--doa", str(tmp_path / "doa.draft.yaml")]
    result = runner.invoke(app, args)
    assert result.exit_code == 0, result.output
    loaded: dict[str, Any] = yaml.safe_load((out / DRAFT).read_text(encoding="utf-8"))
    return loaded


def test_draft_holds_only_what_the_inputs_say(tmp_path: Path) -> None:
    draft = _draft(tmp_path, _doa())
    assert list(draft) == ["mode", "profile", "agents", "tools", "bindings", "roles"]
    assert (draft["mode"], draft["profile"]) == ("observe", "local")
    assert draft["agents"][1] == {
        "id": "a.dispatch",
        "owner": None,
        "owner_team": "ops",
        "mode": "unattended",
    }
    tools = {tool["id"]: tool for tool in draft["tools"]}
    assert tools["mail.send"]["tier"] is None
    assert "approval_ttl_minutes" not in tools["erp.read"]
    assert tools["erp.pay"]["approvals"]["bands"] == [
        {"up_to": 10000, "ladder": ["finance_approver"]},
        {"ladder": ["finance_approver"]},
    ]
    assert draft["roles"]["finance_approver"] == ["amara", "wei"]
    assert draft["roles"]["policy_author"] == ["amara"]
    for absent in ("enforcement", "identity", "secrets", "ladders", "policy_sections"):
        assert absent not in draft
    assert "cool_off_minutes" not in draft["roles"]


def test_decisions_follow_the_tier_and_unknown_is_never_allow(tmp_path: Path) -> None:
    draft = _draft(tmp_path)
    decisions = {(b["agent"], b["tool"]): b["decision"] for b in draft["bindings"]}
    assert decisions == {
        ("a.pay", "erp.pay"): "ask",
        ("a.pay", "erp.read"): "allow",
        ("a.dispatch", "erp.read"): "allow",
        ("a.dispatch", "tms.update"): "ask",
        ("a.help", "mail.send"): "ask",
    }
    assert all(b["decision"] != "deny" for b in draft["bindings"])
    assert {b["agent"]: b["owner_team"] for b in draft["bindings"] if b["tool"] == "erp.pay"} == {
        "a.pay": "finance"
    }


def test_files_that_are_not_given_leave_their_sections_out(tmp_path: Path) -> None:
    folder = _folder(tmp_path, people="person_id,roles\nx,r\n")
    out = tmp_path / "out"
    assert runner.invoke(app, ["diagnose", str(folder), "--out", str(out)]).exit_code == 0
    draft = yaml.safe_load((out / DRAFT).read_text(encoding="utf-8"))
    assert list(draft) == ["mode", "profile", "roles"]
    diagnosis = json.loads((out / DIAGNOSIS).read_text(encoding="utf-8"))
    omitted = {item["section"]: item["reason"] for item in diagnosis["not_drafted"]}
    assert omitted["agents and bindings"] == "agents.csv was not given"
    assert omitted["tools"] == "tools.csv was not given"
    assert omitted["tools[].approvals"] == "no DoA draft was given"
    assert "roles" not in omitted
    assert "ladders" in omitted and "cool_off_minutes" in omitted


# Secrets and the credential name


def test_the_credential_name_is_never_written(tmp_path: Path) -> None:
    out = tmp_path / "out"
    folder = _all(tmp_path)
    assert runner.invoke(app, ["diagnose", str(folder), "--out", str(out)]).exit_code == 0
    for name in (DIAGNOSIS, DRAFT, MATRIX, REPORT):
        assert "shared-key" not in (out / name).read_text(encoding="utf-8"), name


# The matrix


def test_matrix_rows_and_gaps(tmp_path: Path) -> None:
    folder = _all(tmp_path)
    text = matrix_text(_diagnosis(folder, _doa()))
    assert text.endswith("\r\n") and "\n" not in text.replace("\r\n", "")
    rows = list(csv.DictReader(io.StringIO(text)))
    assert len(rows) == 5
    pay = next(r for r in rows if (r["agent_id"], r["tool_id"]) == ("a.pay", "erp.pay"))
    assert pay["approval_ladder"] == "finance_approver"
    assert pay["ladder_holders"] == "amara;wei"
    assert pay["proposed_decision"] == "ask"
    assert pay["gaps"] == "approver.self;tier.high_allow;credential.shared"
    update = next(r for r in rows if r["tool_id"] == "tms.update")
    assert update["gaps"] == (
        "approver.none;approver.self:unknown;expiry.none:unknown;"
        "credential.shared;unattended.no_owner"
    )
    assert update["owner"] == ""


@pytest.mark.parametrize("value", ["=1+1", "+1", "-1", "@SUM(A1)", "\tx", "\rx"])
def test_a_cell_that_could_be_a_formula_is_quoted(value: str) -> None:
    assert cell(value) == "'" + value
    assert cell("plain") == "plain"
    assert cell(None) == ""


def test_matrix_guards_names_from_the_files(tmp_path: Path) -> None:
    folder = _folder(
        tmp_path,
        agents='agent_id,name,tools\nx,"=HYPERLINK(1)",t\n',
        tools="tool_id,description\nt,@evil\n",
    )
    rows = list(csv.DictReader(io.StringIO(matrix_text(_diagnosis(folder)))))
    assert rows[0]["agent_name"] == "'=HYPERLINK(1)"


# The report


def test_report_is_one_file_and_states_the_hashes(tmp_path: Path) -> None:
    html = render_html(_diagnosis(_all(tmp_path), _doa()), SHA)
    assert html.startswith("<!doctype html>")
    assert not re.search(r"(src|href)\s*=|<link\b|@import|url\(|<script", html)
    assert "http://" not in html and "https://" not in html
    assert f"sha256 {SHA}" in html
    assert "not a conformity assessment" in html
    assert "self-reported" in html
    assert "Ritvo authority gap report" in html


def test_every_status_has_a_label_and_a_shape(tmp_path: Path) -> None:
    html = render_html(_diagnosis(_all(tmp_path), _doa()), SHA)
    chips = re.findall(r'<span class="chip [^"]+">(.*?)</span>', html, flags=re.S)
    assert chips
    for chip in chips:
        assert "<svg" in chip, chip
        assert re.sub(r"<svg.*?</svg>", "", chip).strip(), chip


def test_report_shows_gaps_first_and_the_weakest_basis(tmp_path: Path) -> None:
    html = render_html(_diagnosis(_all(tmp_path), _doa()), SHA)
    assert html.index("lv-gap") < html.index('lv-needs_validation">')
    assert "asserted (weakest); also documented" in html
    for rule in RULES:
        assert rule in html


def test_report_escapes_names(tmp_path: Path) -> None:
    folder = _folder(tmp_path, agents="agent_id,name,tools\nx,<b>bold</b>,\n")
    html = render_html(_diagnosis(folder), SHA)
    assert "<b>bold</b>" not in html


def _luminance(colour: str) -> float:
    channels = [int(colour[i : i + 2], 16) / 255 for i in (1, 3, 5)]
    lin = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
    return float(0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2])


def test_text_meets_wcag_aa_in_light_dark_and_print(tmp_path: Path) -> None:
    html = render_html(_diagnosis(_all(tmp_path), _doa()), SHA)
    css = html[html.index("<style>") : html.index("</style>")]
    palettes = [
        dict(re.findall(r"--([\w-]+):\s*(#[0-9A-Fa-f]{6})", block))
        for block in re.findall(r":root \{(.*?)\}", css, flags=re.S)
    ]
    assert len(palettes) == 3
    pairs = [
        ("ink", "bg"),
        ("ink", "surface"),
        ("ink2", "bg"),
        ("ink2", "surface"),
        ("muted", "bg"),
        ("muted", "surface"),
        ("accent", "bg"),
        ("t-low-ink", "t-low"),
        ("t-med-ink", "t-med"),
        ("t-high-ink", "t-high"),
        ("p1", "p1-bg"),
        ("p2", "p2-bg"),
        ("ok", "ok-bg"),
        ("ink2", "surface"),
    ]
    for palette in palettes:
        for foreground, background in pairs:
            a, b = _luminance(palette[foreground]), _luminance(palette[background])
            ratio = (max(a, b) + 0.05) / (min(a, b) + 0.05)
            assert ratio >= 4.5, (foreground, background, round(ratio, 2))


# The command


def _run(folder: Path, *args: str) -> Any:
    return runner.invoke(app, ["diagnose", str(folder), *args])


def test_command_writes_four_files_and_replaces_them(tmp_path: Path) -> None:
    folder = _all(tmp_path)
    first = _run(folder)
    assert first.exit_code == 0, first.output
    out = folder / "diagnosis"
    names = {p.name for p in out.iterdir()}
    assert names == {DIAGNOSIS, DRAFT, MATRIX, REPORT}
    (out / "keep.txt").write_text("mine", encoding="utf-8")
    (out / MATRIX).write_text("stale", encoding="utf-8")
    before = (folder / "agents.csv").read_bytes()
    assert _run(folder).exit_code == 0
    assert (out / "keep.txt").read_text(encoding="utf-8") == "mine"
    assert (out / MATRIX).read_text(encoding="utf-8").startswith("agent_id,")
    assert (folder / "agents.csv").read_bytes() == before
    lines = first.output.splitlines()
    assert lines[3].startswith("approver.none") and "0 gap, 1 not known" in lines[3]
    assert lines[-1] == f"Wrote {out / REPORT}"


def test_exit_status_is_zero_whatever_the_findings(tmp_path: Path) -> None:
    result = _run(_all(tmp_path), "--doa", str(_write_doa(tmp_path)))
    assert result.exit_code == 0
    assert "1 gap" in result.output


def _write_doa(tmp_path: Path, data: dict[str, Any] | None = None) -> Path:
    path = tmp_path / "doa.draft.yaml"
    path.write_text(yaml.safe_dump(data or _doa()), encoding="utf-8")
    return path


def test_doa_inputs_are_hashed_by_name_only(tmp_path: Path) -> None:
    folder = _all(tmp_path)
    assert _run(folder, "--doa", str(_write_doa(tmp_path))).exit_code == 0
    data = json.loads((folder / "diagnosis" / DIAGNOSIS).read_text(encoding="utf-8"))
    assert [item["file"] for item in data["inputs"]] == [
        "agents.csv",
        "tools.csv",
        "people.csv",
        "doa.draft.yaml",
    ]
    assert all(re.fullmatch(r"[0-9a-f]{64}", item["sha256"]) for item in data["inputs"])
    assert str(tmp_path) not in json.dumps(data)


def test_diagnosis_json_reads_back(tmp_path: Path) -> None:
    folder = _all(tmp_path)
    assert _run(folder, "--doa", str(_write_doa(tmp_path))).exit_code == 0
    text = (folder / "diagnosis" / DIAGNOSIS).read_text(encoding="utf-8")
    assert Diagnosis.model_validate_json(text).diagnosis_version == 1


def test_output_is_deterministic_apart_from_the_time(tmp_path: Path) -> None:
    folder = _all(tmp_path)
    _run(folder, "--out", str(tmp_path / "one"))
    _run(folder, "--out", str(tmp_path / "two"))
    for name in (DRAFT, MATRIX):
        assert (tmp_path / "one" / name).read_bytes() == (tmp_path / "two" / name).read_bytes()


def test_invalid_intake_writes_nothing(tmp_path: Path) -> None:
    folder = _folder(tmp_path, agents="agent_id,owner\nx,Not An Id\n")
    result = _run(folder)
    assert result.exit_code == 1
    assert "Nothing was written." in result.output
    assert not (folder / "diagnosis").exists()


@pytest.mark.parametrize("text", ["not: [valid", yaml.safe_dump({"doa_draft_version": 1})])
def test_invalid_doa_draft_writes_nothing(tmp_path: Path, text: str) -> None:
    folder = _all(tmp_path)
    doa = tmp_path / "bad.draft.yaml"
    doa.write_text(text, encoding="utf-8")
    result = _run(folder, "--doa", str(doa))
    assert result.exit_code == 1
    assert "error: bad.draft.yaml" in result.output
    assert not (folder / "diagnosis").exists()


def test_option_combinations(tmp_path: Path) -> None:
    folder = _all(tmp_path)
    assert _run(folder, "--fix", "--out", str(tmp_path / "o")).exit_code == 1
    assert _run(folder, "--fix", "--doa", str(_write_doa(tmp_path))).exit_code == 1
    assert _run(folder, "--out", str(folder / "corrected")).exit_code == 1
    templates = runner.invoke(
        app, ["diagnose", "--templates", str(tmp_path / "t"), "--out", str(tmp_path / "o")]
    )
    assert templates.exit_code == 1
    assert "--doa and --out need an intake folder" in templates.output
    assert not (tmp_path / "o").exists()


def test_fix_on_valid_files_does_not_write_a_diagnosis(tmp_path: Path) -> None:
    folder = _all(tmp_path)
    result = _run(folder, "--fix")
    assert result.exit_code == 0
    assert "Nothing to fix." in result.output
    assert not (folder / "diagnosis").exists()


def test_out_that_is_a_file_is_refused(tmp_path: Path) -> None:
    folder = _all(tmp_path)
    target = tmp_path / "file"
    target.write_text("x", encoding="utf-8")
    result = _run(folder, "--out", str(target))
    assert result.exit_code != 0
    assert target.read_text(encoding="utf-8") == "x"


def test_owner_who_is_a_deputy_of_a_holder_is_a_gap(tmp_path: Path) -> None:
    folder = _folder(
        tmp_path,
        agents="agent_id,owner,tools\na,amara,erp.pay\n",
        tools="tool_id,tier\nerp.pay,high\n",
        people="person_id,roles,deputy\nwei,finance_approver,amara\namara,,\n",
    )
    doa = _doa(roles=[{"id": "finance_approver", "holders": ["wei"]}])
    assert _findings(_diagnosis(folder, doa), "approver.self") == [("gap", "a")]


def test_a_band_with_no_approver_is_a_gap_even_when_a_lower_band_has_one(tmp_path: Path) -> None:
    folder = _folder(
        tmp_path,
        agents="agent_id,tools\na,erp.pay\n",
        tools="tool_id,tier\nerp.pay,high\n",
        people="person_id,roles\nwei,finance_approver\n",
    )
    doa = _doa()
    doa["tools"][0]["approvals"]["bands"] = [
        {"up_to": 10000, "ladder": ["finance_approver"]},
        {"ladder": []},
    ]
    [finding] = [f for f in _diagnosis(folder, doa).findings if f.rule == "approver.none"]
    assert (finding.level, finding.subject) == ("gap", "erp.pay")
    assert "has no approver" in finding.saw


def test_a_finding_for_a_missing_file_rests_on_no_fact(tmp_path: Path) -> None:
    diagnosis = _diagnosis(_all(tmp_path))
    [finding] = [f for f in diagnosis.findings if f.rule == "approver.none"]
    assert finding.bases == []
    assert "none; no fact was read" in render_html(diagnosis, SHA)


def test_report_shows_names_and_descriptions(tmp_path: Path) -> None:
    html = render_html(_diagnosis(_all(tmp_path), _doa()), SHA)
    assert "Payer" in html and "Pay a supplier" in html


def test_a_failure_while_building_leaves_the_folder_as_it_was(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    folder = _all(tmp_path)
    assert _run(folder).exit_code == 0
    out = folder / "diagnosis"
    before = {p.name: p.read_bytes() for p in out.iterdir()}

    def boom(*_: Any) -> str:
        raise RuntimeError("template failed")

    monkeypatch.setattr("ritvo.diagnostic.report.write.render_html", boom)
    with pytest.raises(RuntimeError):
        write_diagnosis(_diagnosis(folder), out)
    assert {p.name: p.read_bytes() for p in out.iterdir()} == before


def test_an_out_folder_that_cannot_be_made_is_an_error(tmp_path: Path) -> None:
    folder = _all(tmp_path)
    blocker = tmp_path / "blocker"
    blocker.write_text("x", encoding="utf-8")
    result = _run(folder, "--out", str(blocker / "sub"))
    assert result.exit_code == 1
    assert "error:" in result.output
