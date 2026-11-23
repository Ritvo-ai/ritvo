import shutil
from pathlib import Path

import pytest
from typer.testing import CliRunner

from ritvo.cli.main import app
from ritvo.config.load import load_config
from ritvo.demo.runner import DemoError, run_demo

runner = CliRunner()
DEMO = Path(__file__).resolve().parents[1] / "examples" / "mcp-demo"


def test_the_example_config_loads_as_a_complete_file() -> None:
    config = load_config(DEMO / "ritvo.yaml")
    assert config.mode == "observe" and config.secrets is not None
    assert config.secrets.backend == "env_file"


def test_the_demo_runs_offline_and_shows_what_enforce_would_do() -> None:
    result = run_demo(DEMO)
    verdicts = [(o.tool, o.passed, o.would_decide, o.reason) for o in result.outcomes]
    assert verdicts == [
        ("broker.quote", True, "allow", "allow"),
        ("broker.order", True, "ask", "ask"),
        ("broker.order", True, "ask", "limit"),
        ("ledger.adjust", True, "allow", "allow"),
        ("broker.quote", True, "deny", "authority_gap"),
        ("broker.order", True, "deny", "authority_gap"),
    ]
    assert result.verified and result.entries == 7 and result.decisions == 6
    assert dict((q, a) for q, a, _ in result.answers) == {
        "record.decisions": "partly",
        "record.identity": "both",
    }
    assert result.workspace is None


def test_the_workspace_is_removed_unless_kept() -> None:
    kept = run_demo(DEMO, keep=True)
    try:
        assert kept.workspace is not None and (kept.workspace / "ritvo-bundle.json").is_file()
        assert "bundle-signing" not in (kept.workspace / "ritvo.yaml").read_text(encoding="utf-8")
    finally:
        if kept.workspace is not None:
            shutil.rmtree(kept.workspace, ignore_errors=True)


def test_the_command_prints_the_table_the_ledger_and_the_answers() -> None:
    result = runner.invoke(app, ["demo", str(DEMO)])
    assert result.exit_code == 0, result.output
    assert "passed, enforce would ask" in result.output and "[authority_gap]" in result.output
    assert "Ledger: 7 entries (6 decisions, 1 policy change), verifies." in result.output
    assert "record.identity: both" in result.output


def test_a_folder_that_is_not_a_demo_is_refused(tmp_path: Path) -> None:
    with pytest.raises(DemoError, match=r"no server\.py"):
        run_demo(tmp_path)
    assert runner.invoke(app, ["demo", str(tmp_path)]).exit_code == 1


def test_the_demo_only_runs_observe_mode(tmp_path: Path) -> None:
    shutil.copytree(DEMO, tmp_path / "d")
    config = tmp_path / "d" / "ritvo.yaml"
    config.write_text(
        config.read_text(encoding="utf-8").replace("mode: observe", "mode: enforce", 1),
        encoding="utf-8",
    )
    with pytest.raises(DemoError, match=r"observe"):
        run_demo(tmp_path / "d")


def test_the_demo_needs_its_key_set_placeholder(tmp_path: Path) -> None:
    shutil.copytree(DEMO, tmp_path / "d")
    config = tmp_path / "d" / "ritvo.yaml"
    config.write_text(
        config.read_text(encoding="utf-8").replace("file://__JWKS_URL__", "file:///x/y.json"),
        encoding="utf-8",
    )
    with pytest.raises(DemoError, match=r"__JWKS_URL__"):
        run_demo(tmp_path / "d")
