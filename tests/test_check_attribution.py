import importlib.util
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / ".github" / "scripts" / "check_attribution.py"


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("check_attribution", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


ca = _load()

SIGNOFF = "Signed-off-by: Jo Example <jo@example.org>"


@pytest.mark.parametrize(
    "text",
    [
        "Co-Authored-By: Claude <someone@example.org>",
        "co-authored-by: GitHub Copilot <x@example.org>",
        "Co-authored-by: renovate[bot] <bot@example.org>",
        "Generated with SomeTool",
        "see https://claude.ai/code/session_abc",
        "see https://chatgpt.com/share/abc",
        "Co-Authored-By: Someone <noreply@anthropic.com>",
        "done \N{ROBOT FACE}",
    ],
)
def test_forbidden_patterns_are_found(text: str) -> None:
    assert ca.find_attribution(text)


@pytest.mark.parametrize(
    "text",
    [
        "feat(inspector): read Claude Desktop and Cursor MCP config",
        "Co-authored-by: Jo Example <jo@example.org>",
        "Co-authored-by: Devin Example <devin@example.org>",
        "docs: link https://github.com/Ritvo-ai/ritvo",
    ],
)
def test_clean_text_passes(text: str) -> None:
    assert ca.find_attribution(text) == []


def test_message_requires_signoff() -> None:
    assert ca.check_message("feat: add thing", require_signoff=True)
    assert ca.check_message(f"feat: add thing\n\n{SIGNOFF}", require_signoff=True) == []


def test_comments_and_scissors_are_ignored(tmp_path: Path) -> None:
    raw = (
        f"feat: add thing\n\n{SIGNOFF}\n"
        "# Please enter the commit message. generated with\n"
        f"{ca.SCISSORS}\n"
        "+Co-Authored-By: Claude <noreply@anthropic.com>\n"
    )
    message_file = tmp_path / "COMMIT_EDITMSG"
    message_file.write_text(raw, encoding="utf-8")
    assert ca.main(["--message-file", str(message_file)]) == 0


def test_message_file_failure(tmp_path: Path) -> None:
    message_file = tmp_path / "COMMIT_EDITMSG"
    message_file.write_text("feat: add thing\n\nGenerated with X\n", encoding="utf-8")
    assert ca.main(["--message-file", str(message_file)]) == 1


def test_env_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PR_TITLE", "feat: add thing")
    monkeypatch.setenv("PR_BODY", "Body\n\nhttps://claude.ai/code/abc")
    assert ca.main(["--env", "PR_TITLE", "PR_BODY"]) == 1
    monkeypatch.setenv("PR_BODY", "Body")
    assert ca.main(["--env", "PR_TITLE", "PR_BODY"]) == 0


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)  # noqa: S603, S607


def test_commits_mode(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.name", "Jo Example")
    _git(tmp_path, "config", "user.email", "jo@example.org")
    _git(tmp_path, "config", "commit.gpgsign", "false")
    _git(tmp_path, "commit", "-q", "--allow-empty", "-s", "-m", "chore: signed")
    monkeypatch.chdir(tmp_path)
    assert ca.main(["--commits", "HEAD"]) == 0
    _git(tmp_path, "commit", "-q", "--allow-empty", "-m", "chore: unsigned")
    assert ca.main(["--commits", "HEAD"]) == 1


def test_runs_as_script() -> None:
    result = subprocess.run(  # noqa: S603
        [sys.executable, str(SCRIPT), "--help"], capture_output=True, text=True, check=False
    )
    assert result.returncode == 0
