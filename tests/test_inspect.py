import ast
import hashlib
import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError
from typer.testing import CliRunner

from ritvo.cli.main import app
from ritvo.connectors.registry import agentcore, vertex
from ritvo.connectors.registry.common import RegistryError
from ritvo.inspector.inventory import (
    Agent,
    Inventory,
    InventoryError,
    load_inventory,
    write_inventory,
)
from ritvo.inspector.registry import inspect_registry, parse_source

ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = ROOT / "examples" / "inspect"
AGENTCORE = ROOT / "tests" / "fixtures" / "registry" / "agentcore"
VERTEX = ROOT / "tests" / "fixtures" / "registry" / "vertex"
NOW = datetime(2026, 10, 19, 9, 30, tzinfo=UTC)

# Values planted in the fixtures that must never be copied anywhere.
NEVER_COPIED = (
    "FIXTURE_ENV_NAME",
    "fixture-env-value-never-copied",
    "FIXTURE_SECRET_NAME",
    "fixture-secret-never-copied",
    "fixture-page-token",
)

runner = CliRunner()


def _run(tmp_path: Path, *sources: str) -> Any:
    args = ["inspect"]
    for source in sources:
        args += ["--registry", source]
    return runner.invoke(app, [*args, "--out", str(tmp_path / "inventory.json")])


def _agents(sources: list[tuple[str, Path]], tmp_path: Path) -> dict[str, Agent]:
    run = inspect_registry(sources, tmp_path / "inventory.json", NOW)
    return {agent.id: agent for agent in run.inventory.agents}


def _write_json(tmp_path: Path, name: str, data: object) -> Path:
    path = tmp_path / name
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


# AgentCore reader


def test_agentcore_list_happy_path() -> None:
    read = agentcore.read(AGENTCORE / "list.json")
    assert [position for position, _ in read.agents] == [1, 2]
    agents = {agent.id: agent for _, agent in read.agents}
    assert set(agents) == {"agentcore.support_triage", "agentcore.invoice_matcher"}
    triage = agents["agentcore.support_triage"]
    assert triage.name == "support_triage"
    assert triage.platform == "agentcore"
    assert triage.platform_ref.endswith(":runtime/support_triage-a1B2c3D4e5")
    # The list output carries no role, and AgentCore exports carry no owners.
    assert (triage.owner, triage.owner_team, triage.credential, triage.framework) == (
        None,
        None,
        None,
        None,
    )


def test_agentcore_get_output_adds_the_role(tmp_path: Path) -> None:
    agents = _agents(
        [
            ("agentcore", AGENTCORE / "list.json"),
            ("agentcore", AGENTCORE / "get-support-triage.json"),
            ("agentcore", AGENTCORE / "get-invoice-matcher.json"),
        ],
        tmp_path,
    )
    assert len(agents) == 2
    role = "arn:aws:iam::123456789012:role/example-agent-runtime"
    assert {agent.credential for agent in agents.values()} == {role}
    assert all(agent.owner is None for agent in agents.values())


def test_agentcore_role_only_from_get_output(tmp_path: Path) -> None:
    runtime = json.loads((AGENTCORE / "get-support-triage.json").read_text(encoding="utf-8"))
    path = _write_json(tmp_path, "list.json", {"agentRuntimes": [runtime]})
    [(_, agent)] = agentcore.read(path).agents
    assert agent.credential is None


# Vertex reader


def test_vertex_list_happy_path() -> None:
    read = vertex.read(VERTEX / "list.json")
    agents = {agent.id: agent for _, agent in read.agents}
    assert set(agents) == {
        "vertex.4711000000000000001",
        "vertex.4711000000000000002",
        "vertex.4711000000000000003",
    }
    planner = agents["vertex.4711000000000000001"]
    assert (planner.name, planner.framework, planner.owner, planner.owner_team) == (
        "Dispatch planner",
        "langgraph",
        "jonas",
        "operations",
    )
    assert planner.credential == "agents-shared@example-logistics.iam.gserviceaccount.com"
    assert planner.platform_ref.startswith("projects/example-logistics/")
    customs = agents["vertex.4711000000000000002"]
    assert (customs.owner, customs.owner_team) == (None, None)
    research = agents["vertex.4711000000000000003"]
    assert research.credential == "research-agent@example-logistics.iam.gserviceaccount.com"
    assert read.warnings == ()


def test_vertex_array_and_single_forms() -> None:
    array = vertex.read(VERTEX / "array.json")
    assert len(array.agents) == 3
    single = vertex.read(VERTEX / "single.json")
    assert [agent.id for _, agent in single.agents] == ["vertex.4711000000000000003"]


def test_vertex_effective_identity_comes_first(tmp_path: Path) -> None:
    path = _write_json(
        tmp_path,
        "engine.json",
        {
            "name": "projects/p/locations/l/reasoningEngines/42",
            "spec": {
                "serviceAccount": "configured@p.example",
                "effectiveIdentity": "used@p.example",
            },
        },
    )
    [(_, agent)] = vertex.read(path).agents
    assert agent.credential == "used@p.example"


def test_vertex_owner_only_from_the_two_labels(tmp_path: Path) -> None:
    path = _write_json(
        tmp_path,
        "engine.json",
        {
            "name": "projects/p/locations/l/reasoningEngines/42",
            "displayName": "owner: alice",
            "description": "Owned by bob in the finance team",
            "labels": {"owner": "carol", "team": "finance", "ritvo-owner-team": "risk"},
        },
    )
    [(_, agent)] = vertex.read(path).agents
    assert agent.owner is None
    assert agent.owner_team == "risk"


def test_vertex_invalid_label_is_ignored_with_a_warning(tmp_path: Path) -> None:
    path = _write_json(
        tmp_path,
        "engine.json",
        {
            "name": "projects/p/locations/l/reasoningEngines/42",
            "labels": {"ritvo-owner": "Not An ID", "ritvo-owner-team": "ops"},
        },
    )
    read = vertex.read(path)
    [(_, agent)] = read.agents
    assert agent.owner is None
    assert agent.owner_team == "ops"
    [warning] = read.warnings
    assert "agent 1 (vertex.42): label ritvo-owner is not a valid ID" in warning
    assert "Not An ID" not in warning


# Refusals


@pytest.mark.parametrize(
    ("source", "remedy"),
    [
        (f"agentcore:{AGENTCORE / 'partial.json'}", "re-export without --max-items"),
        (f"vertex:{VERTEX / 'partial.json'}", "combine the reasoningEngines of every page"),
    ],
)
def test_partial_page_is_refused(tmp_path: Path, source: str, remedy: str) -> None:
    result = _run(tmp_path, source)
    assert result.exit_code == 1
    assert "holds only one page" in result.output
    assert "whole list is in one file" in result.output
    assert remedy in result.output
    assert "Nothing was written." in result.output
    assert "fixture-page-token" not in result.output
    assert not (tmp_path / "inventory.json").exists()


def test_partial_page_in_one_file_writes_nothing_for_any(tmp_path: Path) -> None:
    result = _run(
        tmp_path,
        f"vertex:{VERTEX / 'list.json'}",
        f"agentcore:{AGENTCORE / 'partial.json'}",
    )
    assert result.exit_code == 1
    assert not (tmp_path / "inventory.json").exists()


@pytest.mark.parametrize(
    ("platform", "data", "message"),
    [
        (
            "agentcore",
            {"agentRuntimes": [{"agentRuntimeName": "a"}]},
            "agent 1: agentRuntimeArn is missing",
        ),
        (
            "agentcore",
            {
                "agentRuntimes": [
                    {"agentRuntimeArn": "arn:x", "agentRuntimeName": "a"},
                    {"agentRuntimeArn": "arn:y"},
                ]
            },
            "agent 2: agentRuntimeName is missing",
        ),
        (
            "agentcore",
            {"agentRuntimes": [{"agentRuntimeArn": "arn:x", "agentRuntimeName": 7}]},
            "agentRuntimeName is not a string",
        ),
        ("agentcore", {"agentRuntimes": "nope"}, "agentRuntimes is not a list"),
        ("agentcore", {"agentRuntimes": ["nope"]}, "agent 1 is not a JSON object"),
        ("agentcore", {"something": []}, "not an AgentCore list-agent-runtimes"),
        ("vertex", {"reasoningEngines": [{"displayName": "x"}]}, "agent 1: name is missing"),
        (
            "vertex",
            [{"name": "projects/p/locations/l/reasoningEngines/Bad Name"}],
            "name does not end in a valid ID",
        ),
        (
            "vertex",
            {"name": "projects/p/locations/l/reasoningEngines/1", "spec": []},
            "spec is not a JSON object",
        ),
        ("vertex", {"other": 1}, "not a Vertex AI reasoningEngines list"),
    ],
)
def test_invalid_exports(tmp_path: Path, platform: str, data: object, message: str) -> None:
    path = _write_json(tmp_path, "export.json", data)
    result = _run(tmp_path, f"{platform}:{path}")
    assert result.exit_code == 1
    assert message in result.output
    assert not (tmp_path / "inventory.json").exists()


def test_wrong_shape_error_gives_the_export_command(tmp_path: Path) -> None:
    path = _write_json(tmp_path, "export.json", {"something": []})
    result = _run(tmp_path, f"agentcore:{path}")
    assert "aws bedrock-agentcore-control list-agent-runtimes --output json" in result.output


def test_errors_name_position_not_values(tmp_path: Path) -> None:
    hidden = "Do-Not-Echo-This-Value"
    path = _write_json(
        tmp_path,
        "export.json",
        {
            "agentRuntimes": [
                {"agentRuntimeArn": f"arn:{hidden}", "agentRuntimeName": f"{hidden}!"},
            ]
        },
    )
    result = _run(tmp_path, f"agentcore:{path}")
    assert result.exit_code == 1
    assert "agent 1: agentRuntimeName does not give a valid agent ID" in result.output
    assert hidden not in result.output


@pytest.mark.parametrize(
    ("content", "message"),
    [
        (None, "no such file"),
        (b"{not json", "not a UTF-8 JSON file"),
        (b"\xff\xfe", "not a UTF-8 JSON file"),
    ],
)
def test_missing_or_unreadable_file_is_an_error(
    tmp_path: Path, content: bytes | None, message: str
) -> None:
    path = tmp_path / "export.json"
    if content is not None:
        path.write_bytes(content)
    result = _run(tmp_path, f"vertex:{path}")
    assert result.exit_code == 1
    assert message in result.output
    assert "reasoningEngines.list REST method" in result.output
    assert not (tmp_path / "inventory.json").exists()


def test_out_cannot_be_an_export_file(tmp_path: Path) -> None:
    path = tmp_path / "runtimes.json"
    path.write_bytes((AGENTCORE / "list.json").read_bytes())
    before = path.read_bytes()
    result = runner.invoke(app, ["inspect", "--registry", f"agentcore:{path}", "--out", str(path)])
    assert result.exit_code == 1
    assert "is one of the export files" in result.output
    assert path.read_bytes() == before


def test_two_agents_with_one_id_is_an_error(tmp_path: Path) -> None:
    first = _write_json(
        tmp_path, "a.json", [{"name": "projects/one/locations/l/reasoningEngines/42"}]
    )
    second = _write_json(
        tmp_path, "b.json", [{"name": "projects/two/locations/l/reasoningEngines/42"}]
    )
    with pytest.raises(RegistryError, match=r"different agents that both give the ID 'vertex\.42'"):
        inspect_registry([("vertex", first), ("vertex", second)], tmp_path / "out.json", NOW)


def test_same_agent_in_two_files_is_merged(tmp_path: Path) -> None:
    name = "projects/p/locations/l/reasoningEngines/42"
    first = _write_json(tmp_path, "a.json", [{"name": name, "displayName": "Planner"}])
    second = _write_json(
        tmp_path, "b.json", [{"name": name, "spec": {"serviceAccount": "sa@p.example"}}]
    )
    agents = _agents([("vertex", first), ("vertex", second)], tmp_path)
    assert list(agents) == ["vertex.42"]
    assert agents["vertex.42"].name == "Planner"
    assert agents["vertex.42"].credential == "sa@p.example"


@pytest.mark.parametrize(
    ("value", "message"),
    [
        ("agentcore", "takes <platform>:<file>"),
        ("bedrock:file.json", "unknown platform 'bedrock'; valid platforms: agentcore, vertex"),
    ],
)
def test_bad_registry_option(tmp_path: Path, value: str, message: str) -> None:
    result = _run(tmp_path, value)
    assert result.exit_code == 1
    assert message in result.output


def test_a_source_is_required() -> None:
    result = runner.invoke(app, ["inspect"])
    assert result.exit_code == 1
    assert "give at least one --registry" in result.output


def test_windows_drive_letter_survives() -> None:
    assert parse_source(r"vertex:C:\exports\engines.json") == (
        "vertex",
        Path(r"C:\exports\engines.json"),
    )


# Environment variables are never copied


def test_environment_variables_never_copied(tmp_path: Path) -> None:
    result = _run(
        tmp_path,
        f"agentcore:{AGENTCORE / 'list.json'}",
        f"agentcore:{AGENTCORE / 'get-support-triage.json'}",
        f"vertex:{VERTEX / 'list.json'}",
    )
    assert result.exit_code == 0, result.output
    written = (tmp_path / "inventory.json").read_text(encoding="utf-8")
    for value in NEVER_COPIED:
        assert value not in written
        assert value not in result.output
    assert "deploymentSpec" not in written
    assert "environmentVariables" not in written


# The inventory


def test_examples_run(tmp_path: Path) -> None:
    result = _run(
        tmp_path,
        f"agentcore:{EXAMPLES / 'agentcore-runtimes.json'}",
        f"vertex:{EXAMPLES / 'vertex-reasoning-engines.json'}",
    )
    assert result.exit_code == 0, result.output
    assert "agent.no_owner: 3 (agents that need an owner)" in result.output
    assert "identity.shared_key_name: 1 (credentials used by more than one agent)" in result.output
    assert "Wrote " in result.output
    inventory = load_inventory(tmp_path / "inventory.json")
    assert len(inventory.agents) == 5
    assert all(agent.source == "registry" for agent in inventory.agents)
    assert inventory.tools == []
    assert inventory.mcp_servers == []
    assert inventory.manifest.unreadable == []
    assert inventory.manifest.files_read == 2
    assert [entry.agents for entry in inventory.manifest.registry_exports] == [2, 3]


def test_manifest(tmp_path: Path) -> None:
    sources = [("agentcore", AGENTCORE / "list.json"), ("vertex", VERTEX / "list.json")]
    inventory = inspect_registry(sources, tmp_path / "out.json", NOW).inventory
    assert inventory.manifest.paths == [str(path) for _, path in sources]
    assert inventory.manifest.files_read == 2
    for (platform, path), entry in zip(sources, inventory.manifest.registry_exports, strict=True):
        assert entry.platform == platform
        assert entry.sha256 == hashlib.sha256(path.read_bytes()).hexdigest()
    assert inventory.generated_at == "2026-10-19T09:30:00Z"
    assert inventory.tool.startswith("ritvo-inspect ")


def test_findings(tmp_path: Path) -> None:
    sources = [
        ("agentcore", EXAMPLES / "agentcore-runtimes.json"),
        ("vertex", EXAMPLES / "vertex-reasoning-engines.json"),
    ]
    findings = inspect_registry(sources, tmp_path / "out.json", NOW).inventory.findings
    no_owner = [f for f in findings if f.rule == "agent.no_owner"]
    assert [f.subject for f in no_owner] == [
        "agentcore.invoice_matcher",
        "agentcore.support_triage",
        "vertex.4711000000000000002",
    ]
    assert all(f.agents == [f.subject] for f in no_owner)
    [shared] = [f for f in findings if f.rule == "identity.shared_key_name"]
    assert shared.subject == "agents-shared@example-logistics.iam.gserviceaccount.com"
    assert shared.agents == ["vertex.4711000000000000001", "vertex.4711000000000000002"]


def test_null_is_never_defaulted(tmp_path: Path) -> None:
    path = _write_json(
        tmp_path,
        "engine.json",
        {
            "name": "projects/p/locations/l/reasoningEngines/42",
            "displayName": "",
            "spec": {"agentFramework": None, "serviceAccount": ""},
            "labels": {"ritvo-owner": ""},
        },
    )
    out = tmp_path / "inventory.json"
    write_inventory(inspect_registry([("vertex", path)], out, NOW).inventory, out)
    [agent] = json.loads(out.read_text(encoding="utf-8"))["agents"]
    for field in ("name", "framework", "owner", "owner_team", "credential"):
        assert field in agent
        assert agent[field] is None
    [loaded] = load_inventory(out).agents
    assert (loaded.name, loaded.framework, loaded.owner, loaded.owner_team, loaded.credential) == (
        None,
        None,
        None,
        None,
        None,
    )
    # A field cannot be left out to get a default.
    with pytest.raises(ValidationError):
        Agent.model_validate({"id": "a1", "source": "registry"})


def _inventory_data(tmp_path: Path) -> dict[str, Any]:
    out = tmp_path / "inventory.json"
    run = inspect_registry([("vertex", VERTEX / "list.json")], out, NOW)
    write_inventory(run.inventory, out)
    data: dict[str, Any] = json.loads(out.read_text(encoding="utf-8"))
    return data


def _load(tmp_path: Path, data: dict[str, Any]) -> str:
    path = _write_json(tmp_path, "edited.json", data)
    with pytest.raises(InventoryError) as info:
        load_inventory(path)
    return str(info.value)


def test_inventory_round_trip(tmp_path: Path) -> None:
    data = _inventory_data(tmp_path)
    inventory = load_inventory(tmp_path / "inventory.json")
    assert inventory.model_dump(mode="json") == data


@pytest.mark.parametrize("version", [2, "1", True, 1.0, None])
def test_inventory_rejects_other_schema_version(tmp_path: Path, version: object) -> None:
    data = _inventory_data(tmp_path)
    data["schema_version"] = version
    assert "schema_version must be 1" in _load(tmp_path, data)


def test_inventory_rejects_version_0_with_a_rerun_message(tmp_path: Path) -> None:
    data = _inventory_data(tmp_path)
    data["schema_version"] = 0
    del data["manifest"]["owner_overlays"]
    message = _load(tmp_path, data)
    assert "has schema_version 0" in message
    assert "run the inspector again" in message


def test_inventory_model_rejects_other_schema_version(tmp_path: Path) -> None:
    data = _inventory_data(tmp_path)
    data["schema_version"] = 0
    with pytest.raises(ValidationError, match="schema_version must be 1"):
        Inventory.model_validate(data)


def test_inventory_rejects_unknown_field(tmp_path: Path) -> None:
    data = _inventory_data(tmp_path)
    data["agents"][0]["provenance"] = "derived"
    assert "agents.0.provenance: Extra inputs are not permitted" in _load(tmp_path, data)


def test_inventory_rejects_unknown_top_level_field(tmp_path: Path) -> None:
    data = _inventory_data(tmp_path)
    data["notes"] = []
    assert "notes: Extra inputs are not permitted" in _load(tmp_path, data)


@pytest.mark.parametrize(
    ("path", "name"),
    [
        (("agents", 0, "owner"), "agents.0.owner"),
        (("manifest", "unreadable"), "manifest.unreadable"),
        (("manifest", "registry_exports", 0, "sha256"), "manifest.registry_exports.0.sha256"),
        (("findings",), "findings"),
    ],
)
def test_inventory_rejects_missing_field(
    tmp_path: Path, path: tuple[str | int, ...], name: str
) -> None:
    data = _inventory_data(tmp_path)
    target: Any = data
    for part in path[:-1]:
        target = target[part]
    del target[path[-1]]
    assert f"{name}: Field required" in _load(tmp_path, data)


def test_inventory_rejects_bad_values(tmp_path: Path) -> None:
    data = _inventory_data(tmp_path)
    data["agents"][0]["owner"] = "Not An ID"
    data["agents"][1]["source"] = "guess"
    data["agents"][2]["id"] = data["agents"][0]["id"]
    message = _load(tmp_path, data)
    assert "agents.0.owner" in message
    assert "agents.1.source" in message
    assert "Not An ID" not in message


def test_inventory_rejects_duplicate_ids(tmp_path: Path) -> None:
    data = _inventory_data(tmp_path)
    data["agents"][1]["id"] = data["agents"][0]["id"]
    assert "agents.1.id: duplicate ID" in _load(tmp_path, data)


def test_inventory_not_json(tmp_path: Path) -> None:
    path = tmp_path / "inventory.json"
    path.write_text("{", encoding="utf-8")
    with pytest.raises(InventoryError, match="not a UTF-8 JSON file"):
        load_inventory(path)


# Help


def test_help_shows_the_export_commands() -> None:
    result = runner.invoke(app, ["inspect", "--help"], env={"COLUMNS": "250"})
    assert result.exit_code == 0
    # CI forces colour; drop the escape codes and the wrapping before matching.
    output = " ".join(re.sub(r"\x1b\[[0-9;]*m", "", result.output).split())
    for command in (*agentcore.EXPORT_COMMANDS, *vertex.EXPORT_COMMANDS):
        assert command.split(" > ")[0] in output


# Boundaries (docs/SPEC.md sections 3 and 7)

NETWORK_MODULES = {"socket", "ssl", "http", "urllib", "httpx", "requests", "aiohttp", "subprocess"}
# Parses URL text only; it opens no connection.
ALLOWED_MODULES = {"urllib.parse"}


@pytest.mark.parametrize("package", ["inspector", "connectors"])
def test_no_network_no_governance_imports(package: str) -> None:
    for source in (ROOT / "ritvo" / package).rglob("*.py"):
        tree = ast.parse(source.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            names: list[str] = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            for name in names:
                if name in ALLOWED_MODULES:
                    continue
                assert name.split(".")[0] not in NETWORK_MODULES, f"{source}: {name}"
                assert "governance_os" not in name, f"{source}: {name}"
        # Never read environment values (docs/SPEC.md section 7). The code scan names these
        # functions as patterns to look for in other code, so the check is on the syntax tree.
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute):
                assert node.attr not in ("environ", "getenv"), f"{source}: os.{node.attr}"
            if isinstance(node, ast.ImportFrom) and node.module == "os":
                names = [alias.name for alias in node.names]
                assert not {"environ", "getenv"} & set(names), source
