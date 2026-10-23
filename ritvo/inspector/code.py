"""Find agents in Python source and the names of environment variables (docs/SPEC.md section 7.5).

Source is parsed with `ast` and never imported or run. `.env` files are never opened.
"""

import ast
import hashlib
import os
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from ritvo import __version__
from ritvo.inspector.inventory import (
    SCHEMA_VERSION,
    Agent,
    Identity,
    Inventory,
    Manifest,
)
from ritvo.inspector.registry import agent_findings

# Module prefix to framework name, in the order a framework is chosen when a file has several.
FRAMEWORKS = (
    ("langgraph", "langgraph"),
    ("crewai", "crewai"),
    ("autogen", "autogen"),
    ("autogen_agentchat", "autogen"),
    ("llama_index", "llama-index"),
    ("pydantic_ai", "pydantic-ai"),
    ("semantic_kernel", "semantic-kernel"),
    ("smolagents", "smolagents"),
    ("google.adk", "google-adk"),
    ("strands", "strands"),
    ("langchain", "langchain"),
    ("langchain_core", "langchain"),
    ("mcp", "mcp"),
    ("fastmcp", "mcp"),
)
SDKS = (
    "anthropic",
    "openai",
    "google.genai",
    "google.generativeai",
    "mistralai",
    "cohere",
    "groq",
)
EXAMPLE_ENV_FILES = (".env.example", ".env.sample", ".env.template")
SKIPPED_DIRS = {
    "node_modules",
    "__pycache__",
    "site-packages",
    "build",
    "dist",
    "venv",
    "env",
    "tests",
    "test",
}
MAX_BYTES = 1_000_000

_ENV_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_CREDENTIAL = re.compile(r"(_API_KEY|_KEY|_TOKEN|_SECRET|_PASSWORD|_CREDENTIALS)$")
_EXAMPLE_LINE = re.compile(r"\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=")
_UNSAFE = re.compile(r"[^a-z0-9._-]")


class CodeError(Exception):
    """An option is not valid. Nothing is written."""


@dataclass
class CodeRun:
    agents: list[Agent] = field(default_factory=list)
    paths: list[str] = field(default_factory=list)
    files_read: int = 0
    unreadable: list[str] = field(default_factory=list)
    env_names: set[str] = field(default_factory=set)
    # Per root: (root as given, Python files read, agents found).
    results: list[tuple[Path, int, int]] = field(default_factory=list)


def _skipped_dir(name: str) -> bool:
    return name.startswith(".") or name in SKIPPED_DIRS


def _skipped_file(name: str) -> bool:
    return name.startswith("test_") or name.endswith("_test.py") or name == "conftest.py"


def _matches(module: str, prefix: str) -> bool:
    return module == prefix or module.startswith(prefix + ".")


def _modules(tree: ast.AST) -> list[str]:
    modules = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules += [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            modules.append(node.module)
            modules += [f"{node.module}.{alias.name}" for alias in node.names]
    return modules


def _env_names(tree: ast.AST) -> list[str]:
    """Names read through os.environ[...], os.environ.get, os.getenv and their bare forms."""
    names = []
    for node in ast.walk(tree):
        target: ast.expr | None = None
        if isinstance(node, ast.Subscript) and _is_environ(node.value):
            target = node.slice
        elif isinstance(node, ast.Call) and node.args:
            func = node.func
            if (
                isinstance(func, ast.Attribute)
                and func.attr in ("get", "setdefault", "pop")
                and _is_environ(func.value)
            ) or _is_name(func, "getenv"):
                target = node.args[0]
        if (
            isinstance(target, ast.Constant)
            and isinstance(target.value, str)
            and _ENV_NAME.fullmatch(target.value)
        ):
            names.append(target.value)
    return names


def _is_name(node: ast.expr, name: str) -> bool:
    if isinstance(node, ast.Name):
        return node.id == name
    return isinstance(node, ast.Attribute) and node.attr == name and _is_name(node.value, "os")


def _is_environ(node: ast.expr) -> bool:
    return _is_name(node, "environ")


def _agent_id(root: Path, relative: Path, taken: set[str]) -> str:
    parts = [root.name or "root", *relative.with_suffix("").parts]
    base = _UNSAFE.sub("-", ".".join(["code", *parts]).lower())
    if len(base) > 64:
        digest = hashlib.sha256(relative.as_posix().encode("utf-8")).hexdigest()[:8]
        base = f"{base[:55]}-{digest}"
    candidate, count = base, 1
    while candidate in taken:
        count += 1
        suffix = f"-{count}"
        candidate = base[: 64 - len(suffix)] + suffix
    return candidate


def _framework(modules: list[str]) -> str | None:
    for prefix, name in FRAMEWORKS:
        if any(_matches(module, prefix) for module in modules):
            return name
    return None


def _uses_model(modules: list[str]) -> bool:
    prefixes = [prefix for prefix, _ in FRAMEWORKS] + list(SDKS)
    return any(_matches(module, prefix) for module in modules for prefix in prefixes)


def _walk(root: Path) -> list[Path]:
    files = []
    for directory, dirs, names in os.walk(root, followlinks=False):
        dirs[:] = sorted(name for name in dirs if not _skipped_dir(name))
        for name in sorted(names):
            path = Path(directory) / name
            if path.is_symlink():
                continue
            if name in EXAMPLE_ENV_FILES or (name.endswith(".py") and not _skipped_file(name)):
                files.append(path)
    return files


def scan_code(roots: list[Path]) -> CodeRun:
    run = CodeRun()
    taken: set[str] = set()
    seen: set[Path] = set()
    for root in roots:
        if not root.is_dir():
            raise CodeError(f"--code {root}: not a directory")
        resolved = root.resolve()
        if resolved in seen:
            raise CodeError(f"--code names {root} twice")
        seen.add(resolved)
        run.paths.append(str(resolved))
        read = found = 0
        for path in _walk(resolved):
            try:
                if path.stat().st_size > MAX_BYTES:
                    raise OSError
                raw = path.read_bytes()
            except OSError:
                run.unreadable.append(str(path))
                continue
            run.files_read += 1
            example = path.name in EXAMPLE_ENV_FILES
            if not example:
                read += 1
            try:
                text = raw.decode("utf-8-sig")
            except UnicodeDecodeError:
                run.unreadable.append(str(path))
                continue
            if example:
                for line in text.splitlines():
                    match = _EXAMPLE_LINE.match(line)
                    if match:
                        run.env_names.add(match.group(1))
                continue
            try:
                tree = ast.parse(text, filename=path.name)
            except (SyntaxError, ValueError):
                run.unreadable.append(str(path))
                continue
            names = _env_names(tree)
            run.env_names.update(names)
            modules = _modules(tree)
            if not _uses_model(modules):
                continue
            relative = path.relative_to(resolved)
            agent_id = _agent_id(resolved, relative, taken)
            taken.add(agent_id)
            credential = next((name for name in names if _CREDENTIAL.search(name)), None)
            run.agents.append(
                Agent(
                    id=agent_id,
                    source="code",
                    name=None,
                    platform=None,
                    platform_ref=relative.as_posix(),
                    framework=_framework(modules),
                    owner=None,
                    owner_team=None,
                    credential=credential,
                )
            )
            found += 1
        run.results.append((root, read, found))
    return run


def is_inside(path: Path, roots: list[Path]) -> bool:
    target = path.resolve()
    return any(target.is_relative_to(root.resolve()) for root in roots)


def add_agents(inventory: Inventory | None, run: CodeRun, now: datetime | None = None) -> Inventory:
    """Add the agents and what was read to an inventory, or start a new one. Findings are
    worked out again over every agent."""
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
    known = {agent.id for agent in inventory.agents}
    clashes = [agent.id for agent in run.agents if agent.id in known]
    if clashes:
        raise CodeError(f"agent ID {clashes[0]!r} is found twice; scan each folder once")
    agents = [*inventory.agents, *run.agents]
    manifest = inventory.manifest
    return Inventory.model_validate(
        inventory.model_dump()
        | {
            "manifest": manifest.model_dump()
            | {
                "paths": manifest.paths + run.paths,
                "files_read": manifest.files_read + run.files_read,
                "unreadable": manifest.unreadable + run.unreadable,
                "env_names_seen": sorted(set(manifest.env_names_seen) | run.env_names),
            },
            "agents": [agent.model_dump() for agent in agents],
            "findings": [finding.model_dump() for finding in agent_findings(agents)],
        }
    )
