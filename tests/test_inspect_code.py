import json
import os
from pathlib import Path

import pytest
from typer.testing import CliRunner

from ritvo.cli.main import app
from ritvo.inspector import code
from ritvo.inspector.code import CodeError, add_agents, scan_code
from ritvo.inspector.inventory import load_inventory

runner = CliRunner()

# A fictional project, written to a temporary folder by each test. Files under skipped
# folders or with test names import SDKs too, and must not become agents.
PROJECT = {
    "pipeline.py": (
        'import os\nfrom langgraph.graph import StateGraph\nMODE = os.environ["APP_MODE"]\n'
    ),
    "agents/__init__.py": "",
    "agents/research.py": (
        "import os\nimport anthropic\n"
        'client = anthropic.Anthropic(api_key=os.getenv("RESEARCH_API_KEY"))\n'
    ),
    "agents/review.py": (
        "import os\nfrom openai import OpenAI\n"
        'client = OpenAI(api_key=os.environ.get("SHARED_API_KEY",'
        ' "fixture-default-never-copied"))\n'
    ),
    "agents/summary.py": (
        "from os import environ\nfrom google import genai\n"
        'client = genai.Client(api_key=environ["SHARED_API_KEY"])\n'
    ),
    "util/helpers.py": (
        "from os import getenv\n"
        'open("FIXTURE_IMPORTED", "w").close()\n'
        'TIMEOUT = getenv("HELPER_TIMEOUT")\n'
    ),
    "broken.py": "def broken(:\n    import anthropic\n",
    "tests/test_research.py": "import anthropic\n",
    "test_top.py": "import openai\n",
    "agents/review_test.py": "import openai\n",
    "conftest.py": "import openai\n",
    ".venv/lib/vendored.py": "import openai\n",
    "node_modules/pkg/shim.py": "import openai\n",
    "build/lib/copy.py": "import openai\n",
    ".env.example": (
        "# Fictional\nEXAMPLE_ONLY_KEY=\nexport OTHER_NAME=placeholder\n  # COMMENTED=x\n"
    ),
    ".env": "FIXTURE_SECRET_NAME=fixture-secret-never-copied\n",
    ".env.local": "LOCAL_SECRET_NAME=fixture-local-never-copied\n",
}


def _project(tmp_path: Path) -> Path:
    root = tmp_path / "fictional-app"
    for name, text in PROJECT.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return root


def test_agents_found(tmp_path: Path) -> None:
    root = _project(tmp_path)
    run = scan_code([root])
    agents = {agent.id: agent for agent in run.agents}
    assert set(agents) == {
        "code.fictional-app.pipeline",
        "code.fictional-app.agents.research",
        "code.fictional-app.agents.review",
        "code.fictional-app.agents.summary",
    }
    pipeline = agents["code.fictional-app.pipeline"]
    assert (pipeline.source, pipeline.platform_ref, pipeline.framework) == (
        "code",
        "pipeline.py",
        "langgraph",
    )
    assert pipeline.credential is None
    research = agents["code.fictional-app.agents.research"]
    assert (research.platform_ref, research.framework, research.credential) == (
        "agents/research.py",
        None,
        "RESEARCH_API_KEY",
    )
    assert agents["code.fictional-app.agents.summary"].credential == "SHARED_API_KEY"
    assert all(agent.owner is None and agent.owner_team is None for agent in run.agents)


def test_environment_names(tmp_path: Path) -> None:
    run = scan_code([_project(tmp_path)])
    assert run.env_names == {
        "APP_MODE",
        "RESEARCH_API_KEY",
        "SHARED_API_KEY",
        "HELPER_TIMEOUT",
        "EXAMPLE_ONLY_KEY",
        "OTHER_NAME",
    }


def test_env_files_are_never_opened(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = _project(tmp_path)
    opened: list[str] = []
    real = Path.read_bytes

    def spy(self: Path) -> bytes:
        opened.append(self.name)
        return real(self)

    monkeypatch.setattr(Path, "read_bytes", spy)
    scan_code([root])
    assert ".env" not in opened and ".env.local" not in opened
    assert ".env.example" in opened


def test_code_is_never_run(tmp_path: Path) -> None:
    root = _project(tmp_path)
    cwd = Path.cwd()
    os.chdir(tmp_path)
    try:
        scan_code([root])
    finally:
        os.chdir(cwd)
    assert not (tmp_path / "FIXTURE_IMPORTED").exists()
    assert not (root / "FIXTURE_IMPORTED").exists()


def test_manifest_and_unreadable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = _project(tmp_path)
    (root / "latin1.py").write_bytes("# caf\xe9\n".encode("latin-1"))
    (root / "big.py").write_text("x = 1\n" * 100, encoding="utf-8")
    monkeypatch.setattr(code, "MAX_BYTES", 400)
    run = scan_code([root])
    assert run.paths == [str(root.resolve())]
    assert sorted(Path(path).name for path in run.unreadable) == [
        "big.py",
        "broken.py",
        "latin1.py",
    ]
    # pipeline, agents/*, util/helpers, broken, latin1 and .env.example; big.py is not read.
    assert run.files_read == 9
    assert run.results == [(root, 8, 4, 0)]


@pytest.mark.skipif(os.name == "nt", reason="symlinks need extra rights on Windows")
def test_symlinks_are_not_followed(tmp_path: Path) -> None:
    root = _project(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "agent.py").write_text("import anthropic\n", encoding="utf-8")
    (root / "linked").symlink_to(outside, target_is_directory=True)
    (root / "linked_file.py").symlink_to(outside / "agent.py")
    ids = {agent.id for agent in scan_code([root]).agents}
    assert not any("linked" in agent_id for agent_id in ids)


def test_long_ids_and_collisions(tmp_path: Path) -> None:
    root = tmp_path / "app"
    deep = root / ("a" * 30) / ("b" * 30)
    deep.mkdir(parents=True)
    (deep / "agent_one.py").write_text("import openai\n", encoding="utf-8")
    (deep / "agent_two.py").write_text("import openai\n", encoding="utf-8")
    (root / "My Agent.py").write_text("import openai\n", encoding="utf-8")
    (root / "my-agent.py").write_text("import openai\n", encoding="utf-8")
    ids = [agent.id for agent in scan_code([root]).agents]
    assert all(len(agent_id) <= 64 for agent_id in ids)
    assert len(set(ids)) == len(ids) == 4
    assert "code.app.my-agent" in ids and "code.app.my-agent-2" in ids


def test_same_folder_twice(tmp_path: Path) -> None:
    root = _project(tmp_path)
    with pytest.raises(CodeError, match="twice"):
        scan_code([root, root])


def test_shared_key_finding(tmp_path: Path) -> None:
    inventory = add_agents(None, scan_code([_project(tmp_path)]))
    shared = [f for f in inventory.findings if f.rule == "identity.shared_key_name"]
    assert [(f.subject, f.agents) for f in shared] == [
        (
            "SHARED_API_KEY",
            ["code.fictional-app.agents.review", "code.fictional-app.agents.summary"],
        )
    ]
    assert sum(1 for f in inventory.findings if f.rule == "agent.no_owner") == 4


def test_cli_code(tmp_path: Path) -> None:
    root = _project(tmp_path)
    out = tmp_path / "out" / "inventory.json"
    out.parent.mkdir()
    result = runner.invoke(app, ["inspect", "--code", str(root), "--out", str(out)])
    assert result.exit_code == 0, result.output
    assert f"code  7 Python files, 4 agents, 0 tools  {root}" in result.output
    assert "identity.shared_key_name: 1" in result.output
    inventory = load_inventory(out)
    assert inventory.manifest.env_names_seen == sorted(
        [
            "APP_MODE",
            "RESEARCH_API_KEY",
            "SHARED_API_KEY",
            "HELPER_TIMEOUT",
            "EXAMPLE_ONLY_KEY",
            "OTHER_NAME",
        ]
    )
    written = out.read_text(encoding="utf-8")
    for value in ("never-copied", "FIXTURE_SECRET_NAME", "LOCAL_SECRET_NAME", "placeholder"):
        assert value not in written
        assert value not in result.output


def test_cli_code_with_owners(tmp_path: Path) -> None:
    root = _project(tmp_path)
    intake = tmp_path / "intake"
    intake.mkdir()
    (intake / "agents.csv").write_text(
        "agent_id,owner,owner_team\ncode.fictional-app.agents.research,amara,research\n",
        encoding="utf-8",
    )
    out = tmp_path / "inventory.json"
    result = runner.invoke(
        app,
        ["inspect", "--code", str(root), "--owners", str(intake / "agents.csv"), "--out", str(out)],
    )
    assert result.exit_code == 0, result.output
    agents = {agent.id: agent for agent in load_inventory(out).agents}
    assert agents["code.fictional-app.agents.research"].owner == "amara"
    assert "agent.no_owner: 3" in result.output


def test_cli_out_inside_code_folder(tmp_path: Path) -> None:
    root = _project(tmp_path)
    result = runner.invoke(
        app, ["inspect", "--code", str(root), "--out", str(root / "inventory.json")]
    )
    assert result.exit_code == 1
    assert "inside a --code folder" in result.output
    assert not (root / "inventory.json").exists()


def test_cli_code_not_with_inventory(tmp_path: Path) -> None:
    root = _project(tmp_path)
    out = tmp_path / "inventory.json"
    out.write_text(json.dumps({}), encoding="utf-8")
    result = runner.invoke(
        app,
        ["inspect", "--code", str(root), "--inventory", str(out), "--owners", "x.csv"],
    )
    assert result.exit_code == 1
    assert "cannot be combined" in result.output


# Tools, reach and model APIs over HTTP

ORDERS = {
    "app/pipeline.py": (
        "from langgraph.graph import StateGraph\n"
        "def run():\n"
        "    from app.execution.orders import send\n"
        "    send()\n"
    ),
    "app/__init__.py": "",
    "app/execution/__init__.py": "",
    "app/execution/broker.py": (
        "from kiteconnect import KiteConnect\ndef client():\n    return KiteConnect(api_key='x')\n"
    ),
    "app/execution/orders.py": (
        "from .broker import client\n"
        "def send():\n"
        "    kite = client()\n"
        "    kite.place_order(variety='regular')\n"
        "    kite.place_gtt(trigger=1)\n"
        "def unrelated(x):\n"
        "    x.place_order()\n"
    ),
    "app/billing.py": (
        "import stripe\n"
        "import smtplib\n"
        "stripe.Refund.create(charge='ch')\n"
        "stripe.Customer.create(email='e')\n"
        "smtplib.SMTP('h').send_message(None)\n"
    ),
    "app/validators/news.py": (
        "import httpx\n"
        "BASE = 'https://api.perplexity.ai'\n"
        "def ask():\n"
        "    return httpx.post(BASE + '/chat')\n"
    ),
    "app/validators/wrapper.py": "from app.validators.news import ask\n",
    "app/notes.py": "URL = 'https://api.openai.com/v1'\n",
    "app/lookalike.py": "def place_order(x):\n    x.place_order()\n",
}


def _orders(tmp_path: Path) -> Path:
    root = tmp_path / "proj"
    for name, text in ORDERS.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return root


def test_tools_from_the_catalogue(tmp_path: Path) -> None:
    run = scan_code([_orders(tmp_path)])
    tools = {tool.id: tool for tool in run.tools}
    assert set(tools) == {
        "code.proj.app.execution.orders/kiteconnect",
        "code.proj.app.billing/smtplib",
        "code.proj.app.billing/stripe",
    }
    orders = tools["code.proj.app.execution.orders/kiteconnect"]
    # kiteconnect is imported by broker.py, which orders.py imports.
    assert (orders.source, orders.server, orders.sdk, orders.platform_ref) == (
        "code",
        None,
        "kiteconnect",
        "app/execution/orders.py",
    )
    assert orders.calls == ["place_gtt", "place_order"]
    assert orders.proposed_tier == "high"
    assert (orders.description_hash, orders.input_schema_hash) == (None, None)
    assert tools["code.proj.app.billing/stripe"].calls == ["Refund.create"]
    assert tools["code.proj.app.billing/smtplib"].proposed_tier == "medium"
    # broker.py creates the client but places nothing; lookalike.py never imports the SDK.
    assert not any("broker" in tool_id or "lookalike" in tool_id for tool_id in tools)


def test_reach_follows_project_imports(tmp_path: Path) -> None:
    run = scan_code([_orders(tmp_path)])
    tools = {tool.id: tool for tool in run.tools}
    # The pipeline imports orders.py inside a function.
    assert tools["code.proj.app.execution.orders/kiteconnect"].reached_by == [
        "code.proj.app.pipeline"
    ]
    assert tools["code.proj.app.billing/stripe"].reached_by == []


def test_model_api_over_http(tmp_path: Path) -> None:
    ids = {agent.id for agent in scan_code([_orders(tmp_path)]).agents}
    assert "code.proj.app.validators.news" in ids
    # Importing the HTTP agent does not make an agent, and a host without a client does not.
    assert "code.proj.app.validators.wrapper" not in ids
    assert "code.proj.app.notes" not in ids


def test_unowned_high_tier_tool_finding(tmp_path: Path) -> None:
    inventory = add_agents(None, scan_code([_orders(tmp_path)]))
    unowned = {
        f.subject: f.agents for f in inventory.findings if f.rule == "tool.destructive_unowned"
    }
    assert unowned == {
        "code.proj.app.execution.orders/kiteconnect": ["code.proj.app.pipeline"],
        # No agent is known to reach it.
        "code.proj.app.billing/stripe": [],
    }


def test_owner_clears_the_tool_finding(tmp_path: Path) -> None:
    root = _orders(tmp_path)
    intake = tmp_path / "intake"
    intake.mkdir()
    (intake / "agents.csv").write_text(
        "agent_id,owner\ncode.proj.app.pipeline,amara\n", encoding="utf-8"
    )
    out = tmp_path / "inventory.json"
    result = runner.invoke(
        app,
        ["inspect", "--code", str(root), "--owners", str(intake / "agents.csv"), "--out", str(out)],
    )
    assert result.exit_code == 0, result.output
    assert "code  10 Python files, 2 agents, 3 tools" in result.output
    assert "tool.destructive_unowned: 1" in result.output
    findings = load_inventory(out).findings
    assert [f.subject for f in findings if f.rule == "tool.destructive_unowned"] == [
        "code.proj.app.billing/stripe"
    ]


def test_code_tool_model_rules() -> None:
    from pydantic import ValidationError

    from ritvo.inspector.inventory import Annotations, Tool

    empty = Annotations(readOnly=None, destructive=None, idempotent=None, openWorld=None)
    base = {
        "id": "s/t",
        "server": None,
        "sdk": "kiteconnect",
        "platform_ref": "a.py",
        "calls": ["place_order"],
        "description_hash": None,
        "input_schema_hash": None,
        "annotations": empty,
        "proposed_tier": "high",
        "reached_by": [],
    }
    Tool.model_validate({**base, "source": "code"})
    with pytest.raises(ValidationError, match="code tool"):
        Tool.model_validate({**base, "source": "code", "calls": []})
    with pytest.raises(ValidationError, match="mcp tool"):
        Tool.model_validate({**base, "source": "mcp"})
