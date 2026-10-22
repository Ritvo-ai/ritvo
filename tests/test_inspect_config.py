import hashlib
import json
import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from ritvo.cli.main import app
from ritvo.inspector.config import (
    ConfigError,
    add_servers,
    config_location,
    default_locations,
    normalise_name,
    parse_config,
    read_configs,
    system_name,
)
from ritvo.inspector.inventory import load_inventory

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures" / "config"
AGENTCORE = ROOT / "tests" / "fixtures" / "registry" / "agentcore"
NOW = datetime(2026, 10, 22, 9, 30, tzinfo=UTC)
PROFILE = "1a2b3c"
PROJECT_PATH = "/fixture/project-path-never-copied"

# Values planted in the fixtures that must never be copied anywhere.
NEVER_COPIED = (
    "never-copied",
    "fixture-input",
    "server.py",
    "mcp-server-fetch",
    "#frag",
)

# The files of docs/SPEC.md section 7.1 per OS, relative to a root holding home, appdata and
# project. Written out here rather than taken from the code, so the test checks the table.
LAYOUT = {
    "macos": {
        "claude_desktop_config.json": "home/Library/Application Support/Claude/"
        "claude_desktop_config.json",
        "vscode-user.json": "home/Library/Application Support/Code/User/mcp.json",
        "vscode-profile.json": f"home/Library/Application Support/Code/User/profiles/{PROFILE}/"
        "mcp.json",
    },
    "linux": {
        "vscode-user.json": "home/.config/Code/User/mcp.json",
        "vscode-profile.json": f"home/.config/Code/User/profiles/{PROFILE}/mcp.json",
    },
    "windows": {
        "claude_desktop_config.json": "appdata/Claude/claude_desktop_config.json",
        "vscode-user.json": "appdata/Code/User/mcp.json",
        "vscode-profile.json": f"appdata/Code/User/profiles/{PROFILE}/mcp.json",
    },
}
COMMON = {
    "project-mcp.json": "project/.mcp.json",
    "claude.json": "home/.claude.json",
    "cursor-user.json": "home/.cursor/mcp.json",
    "cursor-project.json": "project/.cursor/mcp.json",
    "vscode-workspace.json": "project/.vscode/mcp.json",
}

runner = CliRunner()


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:8]


LOCAL = f"claude-code-local-{_hash(PROJECT_PATH)}"
VSCODE_PROFILE = f"vscode-profile-{_hash(PROFILE)}"

# (transport, url, command) per server ID, found on every OS.
EXPECTED: dict[str, tuple[str, str | None, str | None]] = {
    "claude-code-project.demo-mcp": ("stdio", None, "npx"),
    "claude-code-project.remote": ("http", "https://mcp.example.com:8443", None),
    "claude-code-user.github": ("http", "https://api.example.com", None),
    f"{LOCAL}.db": ("stdio", None, "db-server.exe"),
    "cursor-user.linear": ("http", "https://mcp.example.org", None),
    "cursor-project.local": ("stdio", None, "python3"),
    "vscode-user.fetch": ("stdio", None, "uvx"),
    f"{VSCODE_PROFILE}.memory": ("http", "http://localhost:3000", None),
    "vscode-workspace.api": ("http", "https://[2001:db8::1]:9000", None),
}
DESKTOP = {"claude-desktop.filesystem": ("stdio", None, "npx")}


def _lay_out(root: Path, system: str) -> tuple[Path, Path, Path]:
    for fixture, target in {**COMMON, **LAYOUT[system]}.items():
        path = root / target
        path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(FIXTURES / fixture, path)
    for folder in ("home", "appdata", "project"):
        (root / folder).mkdir(exist_ok=True)
    return root / "home", root / "appdata", root / "project"


def _expected(system: str) -> dict[str, tuple[str, str | None, str | None]]:
    return {**EXPECTED, **(DESKTOP if system != "linux" else {})}


def _servers(run: Any) -> dict[str, tuple[str, str | None, str | None]]:
    return {server.id: (server.transport, server.url, server.command) for server in run.servers}


def _assert_never_copied(text: str) -> None:
    for value in NEVER_COPIED:
        assert value not in text


# Locations per OS


@pytest.mark.parametrize("system", ["macos", "linux", "windows"])
def test_defaults_read_every_location(tmp_path: Path, system: str) -> None:
    home, appdata, project = _lay_out(tmp_path, system)
    locations, unresolved = default_locations(system, home, appdata, project)
    run = read_configs(locations)
    assert unresolved == []
    assert _servers(run) == _expected(system)
    statuses = {result.key: result.status for result in run.results}
    assert set(statuses.values()) == {"found"}
    keys = {
        "claude-code-project",
        "claude-code-user",
        "claude-code-local",
        "cursor-user",
        "cursor-project",
        "vscode-user",
        f"{VSCODE_PROFILE}",
        "vscode-workspace",
    }
    if system != "linux":
        keys.add("claude-desktop")
    assert set(statuses) == keys
    # ~/.claude.json is read once for claude-code-user and claude-code-local.
    assert run.files_read == len(keys) - 1
    assert run.unreadable == []
    assert sorted(run.env_names) == (
        ["FIXTURE_ENV_NAME", "FIXTURE_SECRET_NAME"] if system != "linux" else ["FIXTURE_ENV_NAME"]
    )
    assert "skipped: unknown type" in run.warnings[0]
    assert run.warnings == [
        "vscode-workspace: entry 2 skipped: unknown type",
        "vscode-workspace: entry 3 skipped: neither command nor url",
    ]


@pytest.mark.parametrize("system", ["macos", "linux", "windows"])
def test_defaults_missing_files_are_listed_not_errors(tmp_path: Path, system: str) -> None:
    home, appdata, project = (tmp_path / name for name in ("home", "appdata", "project"))
    locations, _ = default_locations(system, home, appdata, project)
    run = read_configs(locations)
    assert run.servers == []
    assert run.files_read == 0
    assert run.unreadable == []
    assert {result.status for result in run.results} == {"missing"}
    expected = [
        project / ".mcp.json",
        home / ".claude.json",
        *(
            []
            if system == "linux"
            else [Path(tmp_path / LAYOUT[system]["claude_desktop_config.json"])]
        ),
        home / ".cursor" / "mcp.json",
        project / ".cursor" / "mcp.json",
        (tmp_path / LAYOUT[system]["vscode-user.json"]),
        (tmp_path / LAYOUT[system]["vscode-user.json"]).parent / "profiles",
        project / ".vscode" / "mcp.json",
    ]
    assert run.paths == [str(path.resolve()) for path in expected]


def test_linux_has_no_claude_desktop(tmp_path: Path) -> None:
    locations, _ = default_locations("linux", tmp_path, None, tmp_path)
    assert "claude-desktop" not in {location.key for location in locations}


def test_windows_without_appdata(tmp_path: Path) -> None:
    locations, unresolved = default_locations("windows", tmp_path, None, tmp_path)
    assert unresolved == ["claude-desktop", "vscode-user", "vscode-profile"]
    assert {location.key for location in locations} == {
        "claude-code-project",
        "claude-code-user",
        "cursor-user",
        "cursor-project",
        "vscode-workspace",
    }


def test_every_profile_is_read(tmp_path: Path) -> None:
    home, appdata, project = _lay_out(tmp_path, "linux")
    profiles = home / ".config" / "Code" / "User" / "profiles"
    (profiles / "-7f00").mkdir()
    shutil.copyfile(FIXTURES / "cursor-project.json", profiles / "-7f00" / "mcp.json")
    (profiles / "empty").mkdir()
    run = read_configs(default_locations("linux", home, appdata, project)[0])
    ids = set(_servers(run))
    assert f"{VSCODE_PROFILE}.memory" in ids
    # A profile file with mcpServers but no servers section holds no servers.
    assert not any(server_id.startswith(f"vscode-profile-{_hash('-7f00')}") for server_id in ids)
    statuses = {result.key: result.status for result in run.results}
    assert statuses[f"vscode-profile-{_hash('empty')}"] == "missing"


# The real environment, on whichever OS runs the test


def test_cli_defaults_with_overridden_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    system = system_name()
    home, appdata, project = _lay_out(tmp_path, system)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("APPDATA", str(appdata))
    out = tmp_path / "out" / "inventory.json"
    out.parent.mkdir()
    result = runner.invoke(
        app, ["inspect", "--defaults", "--project", str(project), "--out", str(out)]
    )
    assert result.exit_code == 0, result.output
    inventory = load_inventory(out)
    servers = {
        server.id: (server.transport, server.url, server.command)
        for server in inventory.mcp_servers
    }
    assert servers == _expected(system)
    assert all(
        server.source == "config" and server.auth is None for server in inventory.mcp_servers
    )
    assert inventory.agents == [] and inventory.findings == [] and inventory.tools == []
    assert inventory.manifest.servers_contacted == []
    assert "claude-code-local" in result.output
    written = out.read_text(encoding="utf-8")
    _assert_never_copied(written)
    _assert_never_copied(result.output)
    assert "fixture" not in written.replace("FIXTURE_ENV_NAME", "").replace(
        "FIXTURE_SECRET_NAME", ""
    ).replace(str(tmp_path), "")


# Server IDs


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("GitHub", "github"),
        ("Demo MCP", "demo-mcp"),
        ("--my_server.v2", "my_server.v2"),
        ("ünïcode", "n-code"),
        ("@@@", "server"),
        ("", "server"),
    ],
)
def test_normalise_name(name: str, expected: str) -> None:
    assert normalise_name(name) == expected


def test_collisions_and_length(tmp_path: Path) -> None:
    long = "x" * 80
    path = tmp_path / "mcp.json"
    path.write_text(
        json.dumps(
            {
                "mcpServers": {
                    "A b": {"command": "a"},
                    "a-b": {"command": "b"},
                    "A_B": {"command": "c"},
                    "a B": {"command": "d"},
                    long: {"command": "e"},
                    long + "y": {"command": "f"},
                }
            }
        ),
        encoding="utf-8",
    )
    run = read_configs([config_location("cursor", str(path))])
    key = f"cursor-config-{_hash(str(path))}"
    ids = [server.id for server in run.servers]
    assert ids[:4] == [f"{key}.a-b", f"{key}.a-b-2", f"{key}.a_b", f"{key}.a-b-3"]
    assert ids[4] == f"{key}.{long}"[:64]
    assert ids[5] == f"{key}.{long}"[:62] + "-2"
    assert all(len(server_id) <= 64 for server_id in ids)


# Fields


@pytest.mark.parametrize(
    ("entry", "expected"),
    [
        ({"type": "stdio", "command": "node"}, ("stdio", None, "node")),
        (
            {"type": "streamable-http", "url": "https://a.example"},
            ("http", "https://a.example", None),
        ),
        ({"type": "ws", "url": "WSS://A.Example:443/x"}, ("http", "wss://a.example:443", None)),
        ({"url": "http://h.example:80/p?q=1"}, ("http", "http://h.example:80", None)),
        ({"command": "/opt/bin/tool --flag", "url": "https://u.example"}, ("stdio", None, "tool")),
        ({"url": "not a url"}, ("http", None, None)),
        ({"url": "https://h.example:99999"}, ("http", None, None)),
        ({"type": "http", "command": "node"}, ("http", None, None)),
    ],
)
def test_fields(tmp_path: Path, entry: dict[str, object], expected: tuple[object, ...]) -> None:
    path = tmp_path / "mcp.json"
    path.write_text(json.dumps({"servers": {"s": entry}}), encoding="utf-8")
    run = read_configs([config_location("vscode", str(path))])
    assert [(s.transport, s.url, s.command) for s in run.servers] == [expected]


def test_env_values_never_read(tmp_path: Path) -> None:
    path = tmp_path / "mcp.json"
    path.write_text(
        json.dumps({"mcpServers": {"s": {"command": "x", "env": {"API_KEY": "value-x"}}}}),
        encoding="utf-8",
    )
    run = read_configs([config_location("cursor", str(path))])
    assert run.env_names == {"API_KEY"}
    inventory = add_servers(None, run, NOW)
    assert inventory.manifest.env_names_seen == ["API_KEY"]
    assert "value-x" not in json.dumps(inventory.model_dump())


# Unreadable files


@pytest.mark.parametrize(
    "content",
    [
        b"{ not json fixture-content-never-copied",
        b"\xff\xfe fixture-content-never-copied",
        b'["fixture-content-never-copied"]',
        b'{"mcpServers": ["fixture-content-never-copied"]}',
    ],
)
def test_unreadable_file_is_listed_and_run_continues(tmp_path: Path, content: bytes) -> None:
    bad = tmp_path / "bad.json"
    bad.write_bytes(content)
    run = read_configs(
        [
            config_location("cursor", str(bad)),
            config_location("cursor", str(FIXTURES / "cursor-project.json")),
        ]
    )
    assert run.unreadable == [str(bad.resolve())]
    assert [result.status for result in run.results] == ["unreadable", "found"]
    assert len(run.servers) == 1
    assert run.files_read == 2
    _assert_never_copied(" ".join(run.warnings))


def test_claude_code_projects_not_an_object(tmp_path: Path) -> None:
    path = tmp_path / ".claude.json"
    path.write_text(
        json.dumps({"mcpServers": {"a": {"command": "a"}}, "projects": ["x"]}), encoding="utf-8"
    )
    locations, _ = default_locations("linux", tmp_path, None, tmp_path / "p")
    run = read_configs(locations)
    assert run.unreadable == [str(path.resolve())]
    assert [s.id for s in run.servers] == ["claude-code-user.a"]


def test_directory_in_place_of_file_is_unreadable(tmp_path: Path) -> None:
    (tmp_path / "p" / ".mcp.json").mkdir(parents=True)
    locations, _ = default_locations("linux", tmp_path, None, tmp_path / "p")
    run = read_configs(locations)
    assert run.unreadable == [str((tmp_path / "p" / ".mcp.json").resolve())]


# --config


def test_parse_config() -> None:
    assert parse_config("vscode:C:\\Users\\x\\mcp.json") == ("vscode", "C:\\Users\\x\\mcp.json")
    with pytest.raises(ConfigError, match="valid clients"):
        parse_config("windsurf:x.json")
    with pytest.raises(ConfigError, match="<client>:<file>"):
        parse_config("cursor")


def test_config_claude_code_file_is_one_location(tmp_path: Path) -> None:
    path = tmp_path / "claude.json"
    path.write_text(
        json.dumps(
            {
                "mcpServers": {"db": {"command": "a"}},
                "projects": {"/p": {"mcpServers": {"db": {"command": "b"}}}},
            }
        ),
        encoding="utf-8",
    )
    run = read_configs([config_location("claude-code", str(path))])
    key = f"claude-code-config-{_hash(str(path))}"
    assert [s.id for s in run.servers] == [f"{key}.db", f"{key}.db-2"]
    assert [(r.key, r.servers) for r in run.results] == [(key, 2)]


def test_cli_config_combined_with_registry(tmp_path: Path) -> None:
    out = tmp_path / "inventory.json"
    config = str(FIXTURES / "claude_desktop_config.json")
    result = runner.invoke(
        app,
        [
            "inspect",
            "--registry",
            f"agentcore:{AGENTCORE / 'list.json'}",
            "--config",
            f"claude-desktop:{config}",
            "--out",
            str(out),
        ],
    )
    assert result.exit_code == 0, result.output
    inventory = load_inventory(out)
    assert len(inventory.agents) == 2
    assert [s.id for s in inventory.mcp_servers] == [
        f"claude-desktop-config-{_hash(config)}.filesystem"
    ]
    assert inventory.manifest.files_read == 2
    assert inventory.manifest.paths[-1] == str(Path(config).resolve())
    assert len(inventory.manifest.registry_exports) == 1
    assert inventory.manifest.env_names_seen == ["FIXTURE_SECRET_NAME"]
    _assert_never_copied(out.read_text(encoding="utf-8"))


def test_cli_missing_config_file_is_not_an_error(tmp_path: Path) -> None:
    out = tmp_path / "inventory.json"
    missing = tmp_path / "nope.json"
    result = runner.invoke(app, ["inspect", "--config", f"cursor:{missing}", "--out", str(out)])
    assert result.exit_code == 0, result.output
    assert "missing" in result.output
    assert load_inventory(out).manifest.paths == [str(missing.resolve())]


@pytest.mark.parametrize(
    ("args", "message"),
    [
        (["--defaults", "--inventory", "i.json", "--owners", "o.csv"], "cannot be combined"),
        (["--config", "cursor:a.json", "--config", "vscode:a.json"], "names a.json twice"),
        (["--config", "zed:a.json"], "unknown client"),
        (["--project", "."], "--project needs --defaults"),
    ],
)
def test_cli_option_errors(tmp_path: Path, args: list[str], message: str) -> None:
    out = tmp_path / "inventory.json"
    result = runner.invoke(app, ["inspect", *args, "--out", str(out)])
    assert result.exit_code == 1
    assert message in result.output
    assert not out.exists()


def test_cli_out_cannot_be_a_config_file(tmp_path: Path) -> None:
    path = tmp_path / "mcp.json"
    shutil.copyfile(FIXTURES / "cursor-project.json", path)
    before = path.read_bytes()
    result = runner.invoke(app, ["inspect", "--config", f"cursor:{path}", "--out", str(path)])
    assert result.exit_code == 1
    assert "one of the config files read" in result.output
    assert path.read_bytes() == before
