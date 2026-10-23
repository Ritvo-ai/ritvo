"""Find agents, tools and environment variable names in Python source (docs/SPEC.md 7.5).

Source is parsed with `ast` and never imported or run. `.env` files are never opened.
"""

import ast
import hashlib
import os
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from ritvo import __version__
from ritvo.inspector.inventory import (
    SCHEMA_VERSION,
    Agent,
    Annotations,
    Identity,
    Inventory,
    Manifest,
    Tool,
)
from ritvo.inspector.registry import agent_findings, unowned_tool_findings

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
Tier = Literal["low", "medium", "high"]

# Model API hosts. A file that imports an HTTP client and names one of them is an agent.
HTTP_CLIENTS = ("httpx", "requests", "aiohttp")
MODEL_HOSTS = (
    "api.perplexity.ai",
    "api.anthropic.com",
    "api.openai.com",
    "generativelanguage.googleapis.com",
    "api.mistral.ai",
    "api.groq.com",
    "api.deepseek.com",
    "api.cohere.com",
)


@dataclass(frozen=True)
class Action:
    """Calls to an SDK that act on the world. `objects`, when set, must appear in the call
    chain before the method: `stripe.Refund.create`."""

    sdk: str
    module: str
    tier: Tier
    methods: frozenset[str]
    objects: frozenset[str] = frozenset()


CATALOGUE = (
    Action(
        "kiteconnect",
        "kiteconnect",
        "high",
        frozenset(
            {
                "place_order",
                "modify_order",
                "cancel_order",
                "exit_order",
                "place_gtt",
                "modify_gtt",
                "delete_gtt",
            }
        ),
    ),
    Action(
        "stripe",
        "stripe",
        "high",
        frozenset({"create", "confirm", "capture"}),
        frozenset(
            {
                "PaymentIntent",
                "Charge",
                "Refund",
                "Transfer",
                "Payout",
                "payment_intents",
                "charges",
                "refunds",
                "transfers",
                "payouts",
            }
        ),
    ),
    Action("smtplib", "smtplib", "medium", frozenset({"send_message", "sendmail"})),
    Action("slack_sdk", "slack_sdk", "medium", frozenset({"chat_postMessage"})),
    Action("twilio", "twilio", "medium", frozenset({"create"}), frozenset({"messages"})),
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
    tools: list[Tool] = field(default_factory=list)
    paths: list[str] = field(default_factory=list)
    files_read: int = 0
    unreadable: list[str] = field(default_factory=list)
    env_names: set[str] = field(default_factory=set)
    # Per root: (root as given, Python files read, agents found, tools found).
    results: list[tuple[Path, int, int, int]] = field(default_factory=list)


def _skipped_dir(name: str) -> bool:
    return name.startswith(".") or name in SKIPPED_DIRS


def _skipped_file(name: str) -> bool:
    return name.startswith("test_") or name.endswith("_test.py") or name == "conftest.py"


def _matches(module: str, prefix: str) -> bool:
    return module == prefix or module.startswith(prefix + ".")


def _modules(tree: ast.AST, package: tuple[str, ...] = ()) -> list[str]:
    """Every module a file imports, at any depth. Relative imports resolve against `package`."""
    modules = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules += [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                if node.level - 1 > len(package):
                    continue
                base = list(package[: len(package) - (node.level - 1)])
                module = ".".join([*base, *([node.module] if node.module else [])])
            else:
                module = node.module or ""
            if module:
                modules.append(module)
            modules += [f"{module}.{alias.name}" if module else alias.name for alias in node.names]
    return modules


def _chain(node: ast.expr) -> list[str]:
    """The names along a call target: `smtplib.SMTP(h).send_message` gives three."""
    names: list[str] = []
    while isinstance(node, ast.Attribute | ast.Call):
        if isinstance(node, ast.Call):
            node = node.func
            continue
        names.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        names.append(node.id)
    return names[::-1]


def _actions(tree: ast.AST, sdks: set[str]) -> dict[str, tuple[Tier, set[str]]]:
    """For each catalogue SDK in `sdks`, its tier and the calls the file makes to it."""
    found: dict[str, tuple[Tier, set[str]]] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        chain = _chain(node.func)
        if len(chain) < 2:
            continue
        for action in CATALOGUE:
            if action.sdk not in sdks or chain[-1] not in action.methods:
                continue
            if action.objects:
                matched = next((name for name in chain[:-1] if name in action.objects), None)
                if matched is None:
                    continue
                call = f"{matched}.{chain[-1]}"
            else:
                call = chain[-1]
            found.setdefault(action.sdk, (action.tier, set()))[1].add(call)
    return found


def _names_model_host(tree: ast.AST) -> bool:
    return any(
        isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and any(host in node.value for host in MODEL_HOSTS)
        for node in ast.walk(tree)
    )


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


def _sdks(modules: list[str]) -> set[str]:
    return {
        action.sdk
        for action in CATALOGUE
        if any(_matches(module, action.module) for module in modules)
    }


def _module_name(relative: Path) -> tuple[str, ...]:
    parts = relative.with_suffix("").parts
    return parts[:-1] if parts and parts[-1] == "__init__" else parts


@dataclass
class _File:
    path: Path
    relative: Path
    tree: ast.Module
    modules: list[str]
    env_names: list[str]


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


def _read_root(resolved: Path, run: CodeRun) -> tuple[list[_File], int]:
    files: list[_File] = []
    read = 0
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
        relative = path.relative_to(resolved)
        module = _module_name(relative)
        package = module if relative.stem == "__init__" else module[:-1]
        names = _env_names(tree)
        run.env_names.update(names)
        files.append(_File(path, relative, tree, _modules(tree, package), names))
    return files, read


def _project_imports(files: list[_File]) -> dict[Path, set[Path]]:
    """For each file, the files of the same project it imports."""
    by_module = {".".join(_module_name(file.relative)): file.relative for file in files}
    graph: dict[Path, set[Path]] = {}
    for file in files:
        own = file.relative
        graph[own] = {
            by_module[module]
            for module in file.modules
            if module in by_module and by_module[module] != own
        }
    return graph


def _reach(graph: dict[Path, set[Path]], start: Path) -> set[Path]:
    seen = {start}
    stack = [start]
    while stack:
        for target in graph.get(stack.pop(), ()):
            if target not in seen:
                seen.add(target)
                stack.append(target)
    return seen


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
        files, read = _read_root(resolved, run)
        graph = _project_imports(files)
        modules_of = {file.relative: file.modules for file in files}

        agents: dict[Path, Agent] = {}
        file_ids: dict[Path, str] = {}
        for file in files:
            over_http = any(
                _matches(module, client) for module in file.modules for client in HTTP_CLIENTS
            ) and _names_model_host(file.tree)
            if not (_uses_model(file.modules) or over_http):
                continue
            file_ids[file.relative] = _agent_id(resolved, file.relative, taken)
            taken.add(file_ids[file.relative])
            credential = next((name for name in file.env_names if _CREDENTIAL.search(name)), None)
            agents[file.relative] = Agent(
                id=file_ids[file.relative],
                source="code",
                name=None,
                platform=None,
                platform_ref=file.relative.as_posix(),
                framework=_framework(file.modules),
                owner=None,
                owner_team=None,
                credential=credential,
            )

        tools: list[Tool] = []
        for file in files:
            # The SDK is imported by the file, or by a project module the file imports.
            sdks = _sdks(file.modules)
            for imported in graph[file.relative]:
                sdks |= _sdks(modules_of[imported])
            actions = _actions(file.tree, sdks)
            if not actions:
                continue
            if file.relative not in file_ids:
                file_ids[file.relative] = _agent_id(resolved, file.relative, taken)
                taken.add(file_ids[file.relative])
            reached_by = sorted(
                agent.id
                for relative, agent in agents.items()
                if file.relative in _reach(graph, relative)
            )
            for sdk, (tier, calls) in sorted(actions.items()):
                tools.append(
                    Tool(
                        id=f"{file_ids[file.relative]}/{sdk}",
                        source="code",
                        server=None,
                        sdk=sdk,
                        platform_ref=file.relative.as_posix(),
                        calls=sorted(calls),
                        description_hash=None,
                        input_schema_hash=None,
                        annotations=Annotations(
                            readOnly=None, destructive=None, idempotent=None, openWorld=None
                        ),
                        proposed_tier=tier,
                        reached_by=reached_by,
                    )
                )
        run.agents += agents.values()
        run.tools += tools
        run.results.append((root, read, len(agents), len(tools)))
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
    tools = [*inventory.tools, *run.tools]
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
            "tools": [tool.model_dump() for tool in tools],
            "findings": [
                *(finding.model_dump() for finding in agent_findings(agents)),
                *(finding.model_dump() for finding in unowned_tool_findings(tools, agents)),
            ],
        }
    )
