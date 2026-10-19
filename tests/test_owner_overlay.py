import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from ritvo.cli.main import app
from ritvo.inspector.inventory import InventoryError, load_inventory, write_inventory
from ritvo.inspector.owners import OverlayError, apply_overlay, read_overlay
from ritvo.inspector.registry import inspect_registry

ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = ROOT / "examples" / "inspect"
OWNERS = EXAMPLES / "owners" / "agents.csv"
NOW = datetime(2026, 10, 19, 9, 30, tzinfo=UTC)
REGISTRY = [
    "--registry",
    f"agentcore:{EXAMPLES / 'agentcore-runtimes.json'}",
    "--registry",
    f"vertex:{EXAMPLES / 'vertex-reasoning-engines.json'}",
]

runner = CliRunner()


def _owners(tmp_path: Path, text: str, people: str | None = None) -> Path:
    directory = tmp_path / "intake"
    directory.mkdir(exist_ok=True)
    path = directory / "agents.csv"
    path.write_text(text, encoding="utf-8")
    if people is not None:
        (directory / "people.csv").write_text(people, encoding="utf-8")
    return path


def _inventory(tmp_path: Path) -> Path:
    path = tmp_path / "inventory.json"
    sources = [
        ("agentcore", EXAMPLES / "agentcore-runtimes.json"),
        ("vertex", EXAMPLES / "vertex-reasoning-engines.json"),
    ]
    write_inventory(inspect_registry(sources, path, NOW).inventory, path)
    return path


def _agents(path: Path) -> dict[str, dict[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    return {agent["id"]: agent for agent in data["agents"]}


# With --registry


def test_overlay_with_registry(tmp_path: Path) -> None:
    out = tmp_path / "inventory.json"
    result = runner.invoke(app, ["inspect", *REGISTRY, "--owners", str(OWNERS), "--out", str(out)])
    assert result.exit_code == 0, result.output
    assert "owners: 5 fields set, 0 replaced a value" in result.output
    assert "agent.no_owner: 1 (agents that need an owner)" in result.output
    inventory = load_inventory(out)
    agents = {agent.id: agent for agent in inventory.agents}
    assert (
        agents["agentcore.support_triage"].owner,
        agents["agentcore.support_triage"].owner_team,
    ) == (
        "priya",
        "support",
    )
    # The empty owner cell left the owner unknown; the team was still set.
    customs = agents["vertex.4711000000000000002"]
    assert (customs.owner, customs.owner_team) == (None, "trade")
    # agent.no_owner was worked out again; the shared credential finding was kept.
    rules = [(finding.rule, finding.subject) for finding in inventory.findings]
    assert rules == [
        ("agent.no_owner", "vertex.4711000000000000002"),
        ("identity.shared_key_name", "agents-shared@example-logistics.iam.gserviceaccount.com"),
    ]
    entries = [entry.model_dump() for entry in inventory.manifest.owner_overlays]
    assert entries[0] == {
        "agent": "agentcore.support_triage",
        "field": "owner",
        "previous": None,
        "source_row": 2,
    }
    assert [(entry["agent"], entry["field"]) for entry in entries] == [
        ("agentcore.support_triage", "owner"),
        ("agentcore.support_triage", "owner_team"),
        ("agentcore.invoice_matcher", "owner"),
        ("agentcore.invoice_matcher", "owner_team"),
        ("vertex.4711000000000000002", "owner_team"),
    ]
    assert inventory.manifest.paths[-1] == str(OWNERS)
    assert inventory.manifest.files_read == 3


def test_overlay_wins_and_the_override_is_recorded(tmp_path: Path) -> None:
    owners = _owners(tmp_path, "agent_id,owner,owner_team\nvertex.4711000000000000001,amara,\n")
    out = tmp_path / "out.json"
    result = runner.invoke(app, ["inspect", *REGISTRY, "--owners", str(owners), "--out", str(out)])
    assert result.exit_code == 0, result.output
    assert "owners: 1 fields set, 1 replaced a value" in result.output
    planner = _agents(out)["vertex.4711000000000000001"]
    # The overlay won for owner; the empty owner_team cell overwrote nothing.
    assert (planner["owner"], planner["owner_team"]) == ("amara", "operations")
    [entry] = json.loads(out.read_text(encoding="utf-8"))["manifest"]["owner_overlays"]
    assert entry == {
        "agent": "vertex.4711000000000000001",
        "field": "owner",
        "previous": "jonas",
        "source_row": 2,
    }


def test_equal_value_is_not_a_change(tmp_path: Path) -> None:
    owners = _owners(
        tmp_path, "agent_id,owner,owner_team\nvertex.4711000000000000001,jonas,operations\n"
    )
    out = tmp_path / "out.json"
    result = runner.invoke(app, ["inspect", *REGISTRY, "--owners", str(owners), "--out", str(out)])
    assert result.exit_code == 0, result.output
    assert "owners: 0 fields set, 0 replaced a value" in result.output
    assert json.loads(out.read_text(encoding="utf-8"))["manifest"]["owner_overlays"] == []


def test_unmatched_row_is_a_warning(tmp_path: Path) -> None:
    owners = _owners(
        tmp_path,
        "agent_id,owner\n\nagentcore.nobody,amara\nagentcore.support_triage,priya\n",
    )
    out = tmp_path / "out.json"
    result = runner.invoke(app, ["inspect", *REGISTRY, "--owners", str(owners), "--out", str(out)])
    assert result.exit_code == 0, result.output
    assert (
        "warning: " + str(owners) + ": line 3: agent_id 'agentcore.nobody' matches no agent"
        in result.output
    )
    assert _agents(out)["agentcore.support_triage"]["owner"] == "priya"
    [entry] = json.loads(out.read_text(encoding="utf-8"))["manifest"]["owner_overlays"]
    assert entry["source_row"] == 4


def test_nulls_stay_null(tmp_path: Path) -> None:
    owners = _owners(tmp_path, "agent_id,owner,owner_team\nagentcore.support_triage,,\n")
    out = tmp_path / "out.json"
    result = runner.invoke(app, ["inspect", *REGISTRY, "--owners", str(owners), "--out", str(out)])
    assert result.exit_code == 0, result.output
    for agent in _agents(out).values():
        if agent["platform"] == "agentcore":
            assert agent["owner"] is None
            assert agent["owner_team"] is None


# With --inventory


def test_overlay_on_an_existing_inventory(tmp_path: Path) -> None:
    source = _inventory(tmp_path)
    before = source.read_bytes()
    out = tmp_path / "merged.json"
    result = runner.invoke(
        app,
        ["inspect", "--inventory", str(source), "--owners", str(OWNERS), "--out", str(out)],
    )
    assert result.exit_code == 0, result.output
    assert source.read_bytes() == before
    merged = load_inventory(out)
    original = load_inventory(source)
    assert merged.manifest.registry_exports == original.manifest.registry_exports
    assert merged.manifest.paths == [*original.manifest.paths, str(OWNERS)]
    assert len(merged.manifest.owner_overlays) == 5
    assert [agent.id for agent in merged.agents] == [agent.id for agent in original.agents]


def test_overlays_accumulate(tmp_path: Path) -> None:
    source = _inventory(tmp_path)
    first = tmp_path / "first.json"
    second = tmp_path / "second.json"
    runner.invoke(
        app, ["inspect", "--inventory", str(source), "--owners", str(OWNERS), "--out", str(first)]
    )
    owners = _owners(tmp_path, "agent_id,owner\nagentcore.support_triage,wei\n")
    result = runner.invoke(
        app, ["inspect", "--inventory", str(first), "--owners", str(owners), "--out", str(second)]
    )
    assert result.exit_code == 0, result.output
    entries = load_inventory(second).manifest.owner_overlays
    assert len(entries) == 6
    assert (entries[-1].agent, entries[-1].previous) == ("agentcore.support_triage", "priya")


def test_out_cannot_be_the_inventory(tmp_path: Path) -> None:
    source = _inventory(tmp_path)
    before = source.read_bytes()
    result = runner.invoke(
        app,
        ["inspect", "--inventory", str(source), "--owners", str(OWNERS), "--out", str(source)],
    )
    assert result.exit_code == 1
    assert "is the file named by --inventory" in result.output
    assert source.read_bytes() == before


def test_out_cannot_be_an_intake_file(tmp_path: Path) -> None:
    owners = _owners(tmp_path, "agent_id,owner\nagentcore.support_triage,priya\n")
    before = owners.read_bytes()
    result = runner.invoke(
        app, ["inspect", *REGISTRY, "--owners", str(owners), "--out", str(owners)]
    )
    assert result.exit_code == 1
    assert "is one of the intake files" in result.output
    assert owners.read_bytes() == before


def test_version_0_inventory_is_rejected(tmp_path: Path) -> None:
    source = _inventory(tmp_path)
    data = json.loads(source.read_text(encoding="utf-8"))
    data["schema_version"] = 0
    del data["manifest"]["owner_overlays"]
    source.write_text(json.dumps(data), encoding="utf-8")
    out = tmp_path / "out.json"
    result = runner.invoke(
        app, ["inspect", "--inventory", str(source), "--owners", str(OWNERS), "--out", str(out)]
    )
    assert result.exit_code == 1
    assert "schema_version 0" in result.output
    assert "run the inspector again" in result.output
    assert not out.exists()


# Options


@pytest.mark.parametrize(
    ("args", "message"),
    [
        (["--owners", str(OWNERS)], "give at least one --registry"),
        (["--inventory", "inventory.json"], "--inventory needs --owners"),
        (
            [*REGISTRY, "--inventory", "inventory.json", "--owners", str(OWNERS)],
            "use --registry or --inventory, not both",
        ),
    ],
)
def test_option_combinations(tmp_path: Path, args: list[str], message: str) -> None:
    out = tmp_path / "out.json"
    result = runner.invoke(app, ["inspect", *args, "--out", str(out)])
    assert result.exit_code == 1
    assert message in result.output
    assert not out.exists()


# The overlay file


def test_file_must_be_named_agents_csv(tmp_path: Path) -> None:
    path = tmp_path / "owners.csv"
    path.write_text("agent_id,owner\nagentcore.support_triage,priya\n", encoding="utf-8")
    with pytest.raises(OverlayError, match=r"must be named agents\.csv"):
        read_overlay(path)


def test_missing_file(tmp_path: Path) -> None:
    with pytest.raises(OverlayError, match="no such file"):
        read_overlay(tmp_path / "agents.csv")


@pytest.mark.parametrize(
    ("text", "people", "message"),
    [
        ("agent_id,owner\nagentcore.support_triage,Priya\n", None, "column owner: not a valid ID"),
        (
            "agent_id,owner_team\nagentcore.support_triage,Customer Support\n",
            None,
            "column owner_team: not a valid ID",
        ),
        ("agent_id,owner\n,priya\n", None, "column agent_id: required"),
        (
            "agent_id,owner\nagentcore.support_triage,bob\n",
            "person_id\nalice\n",
            "'bob' is not in people.csv",
        ),
        ("agent_id,colour\nagentcore.support_triage,red\n", None, "unknown column"),
    ],
)
def test_invalid_overlay_writes_nothing(
    tmp_path: Path, text: str, people: str | None, message: str
) -> None:
    owners = _owners(tmp_path, text, people)
    out = tmp_path / "out.json"
    result = runner.invoke(app, ["inspect", *REGISTRY, "--owners", str(owners), "--out", str(out)])
    assert result.exit_code == 1
    assert message in result.output
    assert "Nothing was written." in result.output
    assert not out.exists()


def test_people_file_beside_is_read_and_listed(tmp_path: Path) -> None:
    owners = _owners(
        tmp_path,
        "agent_id,owner\nagentcore.support_triage,priya\n",
        "person_id,team\npriya,support\n",
    )
    out = tmp_path / "out.json"
    result = runner.invoke(app, ["inspect", *REGISTRY, "--owners", str(owners), "--out", str(out)])
    assert result.exit_code == 0, result.output
    manifest = load_inventory(out).manifest
    assert manifest.paths[-2:] == [str(owners), str(owners.parent / "people.csv")]
    assert manifest.files_read == 4


def test_only_owner_columns_are_merged(tmp_path: Path) -> None:
    owners = _owners(
        tmp_path,
        "agent_id,name,platform,owner,credential\n"
        "agentcore.support_triage,Renamed,custom,priya,other-credential\n",
    )
    inventory = inspect_registry(
        [("agentcore", EXAMPLES / "agentcore-runtimes.json")], tmp_path / "x.json", NOW
    ).inventory
    merged = apply_overlay(inventory, read_overlay(owners), NOW).inventory
    triage = next(agent for agent in merged.agents if agent.id == "agentcore.support_triage")
    assert (triage.name, triage.platform, triage.credential, triage.owner) == (
        "support_triage",
        "agentcore",
        None,
        "priya",
    )
    assert merged.generated_at == "2026-10-19T09:30:00Z"


# manifest.owner_overlays in the inventory reader


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"source_row": 0}, "manifest.owner_overlays.0.source_row"),
        ({"field": "name"}, "manifest.owner_overlays.0.field"),
        ({"previous": "Not An ID"}, "manifest.owner_overlays.0.previous"),
        ({"value": "priya"}, "manifest.owner_overlays.0.value: Extra inputs are not permitted"),
    ],
)
def test_reader_checks_overlay_entries(
    tmp_path: Path, change: dict[str, object], message: str
) -> None:
    path = _inventory(tmp_path)
    data = json.loads(path.read_text(encoding="utf-8"))
    entry = {"agent": "agentcore.support_triage", "field": "owner", "previous": None}
    data["manifest"]["owner_overlays"] = [entry | {"source_row": 2} | change]
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(InventoryError) as info:
        load_inventory(path)
    assert message in str(info.value)
    assert "Not An ID" not in str(info.value)


def test_reader_requires_owner_overlays(tmp_path: Path) -> None:
    path = _inventory(tmp_path)
    data = json.loads(path.read_text(encoding="utf-8"))
    del data["manifest"]["owner_overlays"]
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(InventoryError, match=r"manifest\.owner_overlays: Field required"):
        load_inventory(path)
