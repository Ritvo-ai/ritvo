"""Read MCP servers from client configuration files (docs/SPEC.md section 7.1).

Files only: no server is started or contacted, and no environment value is read.
"""

import hashlib
import json
import re
import sys
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from ritvo import __version__
from ritvo.inspector.inventory import (
    SCHEMA_VERSION,
    Identity,
    Inventory,
    Manifest,
    McpServer,
)

CLIENTS = ("claude-code", "claude-desktop", "cursor", "vscode")

_HTTP_TYPES = {"http", "streamable-http", "sse", "ws"}
_NAME_UNSAFE = re.compile(r"[^a-z0-9._-]")
_LEADING = re.compile(r"^[^a-z0-9]+")


class ConfigError(Exception):
    """An option is not valid. Nothing is written."""


@dataclass(frozen=True)
class Location:
    """One well-known file and the section of it that holds servers."""

    key: str
    path: Path
    # mcpServers, servers, or the two sections of a Claude Code user file.
    section: str


@dataclass(frozen=True)
class LocationResult:
    key: str
    path: Path
    status: str  # found, missing or unreadable
    servers: int


@dataclass
class ConfigRun:
    servers: list[McpServer] = field(default_factory=list)
    paths: list[str] = field(default_factory=list)
    files_read: int = 0
    unreadable: list[str] = field(default_factory=list)
    env_names: set[str] = field(default_factory=set)
    results: list[LocationResult] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def short_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:8]


def system_name() -> str:
    if sys.platform == "darwin":
        return "macos"
    if sys.platform == "win32":
        return "windows"
    return "linux"


def _vscode_user(system: str, home: Path, appdata: Path | None) -> Path | None:
    if system == "macos":
        return home / "Library" / "Application Support" / "Code" / "User"
    if system == "windows":
        return appdata / "Code" / "User" if appdata is not None else None
    return home / ".config" / "Code" / "User"


def default_locations(
    system: str, home: Path, appdata: Path | None, project: Path
) -> tuple[list[Location], list[str]]:
    """The locations `--defaults` reads, in the order of the table in section 7.1.

    Returns the locations and the keys that could not be resolved because APPDATA is unset.
    """
    unresolved: list[str] = []
    locations = [
        Location("claude-code-project", project / ".mcp.json", "mcpServers"),
        Location("claude-code-user", home / ".claude.json", "claude-code-user"),
    ]
    if system == "macos":
        desktop = home / "Library" / "Application Support" / "Claude"
        locations.append(
            Location("claude-desktop", desktop / "claude_desktop_config.json", "mcpServers")
        )
    elif system == "windows":
        if appdata is None:
            unresolved.append("claude-desktop")
        else:
            locations.append(
                Location(
                    "claude-desktop",
                    appdata / "Claude" / "claude_desktop_config.json",
                    "mcpServers",
                )
            )
    locations += [
        Location("cursor-user", home / ".cursor" / "mcp.json", "mcpServers"),
        Location("cursor-project", project / ".cursor" / "mcp.json", "mcpServers"),
    ]
    user = _vscode_user(system, home, appdata)
    if user is None:
        unresolved += ["vscode-user", "vscode-profile"]
    else:
        locations.append(Location("vscode-user", user / "mcp.json", "servers"))
        locations.append(Location("vscode-profile", user / "profiles", "servers"))
    locations.append(Location("vscode-workspace", project / ".vscode" / "mcp.json", "servers"))
    return locations, unresolved


def parse_config(value: str) -> tuple[str, str]:
    """Split `<client>:<file>` at the first colon, so a Windows drive letter survives."""
    client, sep, file = value.partition(":")
    if not sep or not file:
        raise ConfigError(f"--config takes <client>:<file>, for example cursor:{value}")
    if client not in CLIENTS:
        raise ConfigError(
            f"unknown client {client!r}; valid clients: {', '.join(CLIENTS)}"
            if len(client) <= 40
            else f"unknown client; valid clients: {', '.join(CLIENTS)}"
        )
    return client, file


def config_location(client: str, file: str) -> Location:
    section = {"claude-code": "claude-code-user", "vscode": "servers"}.get(client, "mcpServers")
    return Location(f"{client}-config-{short_hash(file)}", Path(file), section)


def normalise_name(name: str) -> str:
    value = _LEADING.sub("", _NAME_UNSAFE.sub("-", name.lower()))
    return value or "server"


def _command_name(command: str) -> str | None:
    words = command.split()
    if not words:
        return None
    return re.split(r"[/\\]", words[0])[-1] or None


def _host_url(value: str) -> str | None:
    try:
        parts = urlsplit(value)
        port = parts.port
    except ValueError:
        return None
    host = parts.hostname
    if not parts.scheme or not host:
        return None
    if ":" in host:
        host = f"[{host}]"
    return f"{parts.scheme.lower()}://{host}" + (f":{port}" if port is not None else "")


class _Unreadable(Exception):
    pass


def _entries(section: object) -> Iterator[tuple[str, object]]:
    if not isinstance(section, dict):
        raise _Unreadable
    yield from section.items()


class _Reader:
    def __init__(self, run: ConfigRun) -> None:
        self.run = run
        self.read: dict[Path, dict[str, object] | None] = {}
        self.broken: set[Path] = set()

    def _note_path(self, path: Path) -> None:
        text = str(path)
        if text not in self.run.paths:
            self.run.paths.append(text)

    def _note_unreadable(self, path: Path) -> None:
        text = str(path)
        if text not in self.run.unreadable:
            self.run.unreadable.append(text)

    def _load(self, path: Path) -> dict[str, object] | None:
        """The parsed file, None when missing. Raises _Unreadable; contents are never echoed."""
        if path not in self.read and path not in self.broken:
            try:
                self.read[path] = self._parse(path)
            except _Unreadable:
                self.broken.add(path)
        if path in self.broken:
            raise _Unreadable
        return self.read[path]

    def _parse(self, path: Path) -> dict[str, object] | None:
        if not path.exists():
            return None
        if not path.is_file():
            raise _Unreadable
        try:
            raw = path.read_bytes()
        except OSError:
            raise _Unreadable from None
        self.run.files_read += 1
        try:
            data = json.loads(raw.decode("utf-8-sig"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise _Unreadable from None
        if not isinstance(data, dict):
            raise _Unreadable
        return data

    def _servers(self, key: str, section: object, seen: set[str]) -> list[McpServer]:
        servers: list[McpServer] = []
        for position, (name, entry) in enumerate(_entries(section), start=1):
            server = self._server(key, position, name, entry, seen)
            if server is not None:
                seen.add(server.id)
                servers.append(server)
        return servers

    def _server(
        self, key: str, position: int, name: str, entry: object, seen: set[str]
    ) -> McpServer | None:
        if not isinstance(entry, dict):
            self.run.warnings.append(f"{key}: entry {position} skipped: not an object")
            return None
        command = entry.get("command") if isinstance(entry.get("command"), str) else None
        url = entry.get("url") if isinstance(entry.get("url"), str) else None
        kind = entry.get("type")
        if command is None and url is None:
            self.run.warnings.append(f"{key}: entry {position} skipped: neither command nor url")
            return None
        transport: Literal["http", "stdio"]
        if kind is None:
            transport = "stdio" if command is not None else "http"
        elif kind == "stdio":
            transport = "stdio"
        elif kind in _HTTP_TYPES:
            transport = "http"
        else:
            self.run.warnings.append(f"{key}: entry {position} skipped: unknown type")
            return None
        env = entry.get("env")
        if isinstance(env, dict):
            self.run.env_names.update(env)
        base = f"{key}.{normalise_name(name)}"
        server_id = base[:64]
        count = 1
        while server_id in seen:
            count += 1
            suffix = f"-{count}"
            server_id = base[: 64 - len(suffix)] + suffix
        return McpServer(
            id=server_id,
            source="config",
            transport=transport,
            url=_host_url(url) if transport == "http" and url is not None else None,
            command=_command_name(command)
            if transport == "stdio" and command is not None
            else None,
            auth=None,
        )

    def read_location(self, location: Location) -> None:
        if location.key == "vscode-profile":
            self._read_profiles(location)
            return
        if location.section == "claude-code-user":
            self._read_claude_code(location)
            return
        self._read_file(location.key, location.path, location.section)

    def _read_file(self, key: str, path: Path, section: str) -> None:
        resolved = path.resolve()
        self._note_path(resolved)
        try:
            data = self._load(resolved)
            if data is None:
                self.run.results.append(LocationResult(key, path, "missing", 0))
                return
            servers = self._servers(key, data[section], set()) if section in data else []
        except _Unreadable:
            self._note_unreadable(resolved)
            self.run.results.append(LocationResult(key, path, "unreadable", 0))
            return
        self.run.servers += servers
        self.run.results.append(LocationResult(key, path, "found", len(servers)))

    def _read_claude_code(self, location: Location) -> None:
        """Top-level mcpServers and every projects.<path>.mcpServers.

        In ~/.claude.json the projects are the location claude-code-local, one key per project.
        A file named with --config is one location.
        """
        path = location.path
        resolved = path.resolve()
        self._note_path(resolved)
        local_key = "claude-code-local" if location.key == "claude-code-user" else None
        keys = [location.key] + ([local_key] if local_key is not None else [])
        try:
            data = self._load(resolved)
        except _Unreadable:
            self._note_unreadable(resolved)
            self.run.results += [LocationResult(key, path, "unreadable", 0) for key in keys]
            return
        if data is None:
            self.run.results += [LocationResult(key, path, "missing", 0) for key in keys]
            return
        seen: set[str] = set()
        top: list[McpServer] = []
        local: list[McpServer] = []
        broken = False
        try:
            if "mcpServers" in data:
                top = self._servers(location.key, data["mcpServers"], seen)
        except _Unreadable:
            broken = True
        projects = data.get("projects", {})
        if not isinstance(projects, dict):
            broken = True
            projects = {}
        for project_path, entry in projects.items():
            if not isinstance(entry, dict):
                broken = True
                continue
            if "mcpServers" not in entry:
                continue
            if local_key is not None:
                key, ids = f"{local_key}-{short_hash(project_path)}", set()
            else:
                key, ids = location.key, seen
            try:
                local += self._servers(key, entry["mcpServers"], ids)
            except _Unreadable:
                broken = True
        if broken:
            self._note_unreadable(resolved)
        status = "unreadable" if broken else "found"
        self.run.servers += top + local
        if local_key is None:
            self.run.results.append(
                LocationResult(location.key, path, status, len(top) + len(local))
            )
        else:
            self.run.results.append(LocationResult(location.key, path, status, len(top)))
            self.run.results.append(LocationResult(local_key, path, status, len(local)))

    def _read_profiles(self, location: Location) -> None:
        folder = location.path
        if not folder.is_dir():
            self._note_path(folder.resolve())
            self.run.results.append(LocationResult(location.key, folder, "missing", 0))
            return
        profiles = sorted(entry.name for entry in folder.iterdir() if entry.is_dir())
        if not profiles:
            self._note_path(folder.resolve())
            self.run.results.append(LocationResult(location.key, folder, "missing", 0))
        for profile in profiles:
            key = f"{location.key}-{short_hash(profile)}"
            self._read_file(key, folder / profile / "mcp.json", location.section)


def read_configs(locations: list[Location]) -> ConfigRun:
    run = ConfigRun()
    reader = _Reader(run)
    for location in locations:
        reader.read_location(location)
    return run


def add_servers(
    inventory: Inventory | None, run: ConfigRun, now: datetime | None = None
) -> Inventory:
    """Add the servers and what was read to an inventory, or start a new one."""
    if inventory is None:
        generated = (now or datetime.now(UTC)).astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        inventory = Inventory(
            schema_version=SCHEMA_VERSION,
            generated_at=generated,
            tool=f"ritvo-inspect {__version__}",
            manifest=Manifest(
                paths=[],
                files_read=0,
                unreadable=[],
                servers_contacted=[],
                env_names_seen=[],
                registry_exports=[],
                owner_overlays=[],
            ),
            agents=[],
            mcp_servers=[],
            tools=[],
            identity=Identity(issuers=[]),
            findings=[],
        )
    manifest = inventory.manifest
    return Inventory.model_validate(
        {
            **inventory.model_dump(),
            "manifest": {
                **manifest.model_dump(),
                "paths": manifest.paths + run.paths,
                "files_read": manifest.files_read + run.files_read,
                "unreadable": manifest.unreadable + run.unreadable,
                "env_names_seen": sorted(set(manifest.env_names_seen) | run.env_names),
            },
            "mcp_servers": [server.model_dump() for server in inventory.mcp_servers + run.servers],
        }
    )
