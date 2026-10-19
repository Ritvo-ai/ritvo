"""Build an inventory from registry exports (docs/SPEC.md section 7.3)."""

from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path

from ritvo import __version__
from ritvo.connectors.registry import agentcore, vertex
from ritvo.connectors.registry.common import ExportRead, RegistryAgent, RegistryError
from ritvo.inspector.inventory import (
    SCHEMA_VERSION,
    Agent,
    Finding,
    Identity,
    Inventory,
    Manifest,
    RegistryExport,
)

READERS: dict[str, Callable[[Path], ExportRead]] = {
    agentcore.PLATFORM: agentcore.read,
    vertex.PLATFORM: vertex.read,
}

EXPORTS = {
    agentcore.PLATFORM: (agentcore.EXPORT_NOTE, agentcore.EXPORT_COMMANDS),
    vertex.PLATFORM: (vertex.EXPORT_NOTE, vertex.EXPORT_COMMANDS),
}


@dataclass(frozen=True)
class FileResult:
    platform: str
    path: Path
    agents: int


@dataclass(frozen=True)
class RegistryRun:
    inventory: Inventory
    files: tuple[FileResult, ...]
    warnings: tuple[str, ...]


def parse_source(value: str) -> tuple[str, Path]:
    """Split `<platform>:<file>` at the first colon, so a Windows drive letter survives."""
    platform, sep, file = value.partition(":")
    if not sep or not file:
        raise RegistryError(f"--registry takes <platform>:<file>, for example agentcore:{value}")
    if platform not in READERS:
        raise RegistryError(
            f"unknown platform {platform!r}; valid platforms: {', '.join(READERS)}"
            if len(platform) <= 40
            else f"unknown platform; valid platforms: {', '.join(READERS)}"
        )
    return platform, Path(file)


def _same_file(a: Path, b: Path) -> bool:
    if a.exists() and b.exists():
        return a.samefile(b)
    return a.resolve() == b.resolve()


def _merge(first: RegistryAgent, other: RegistryAgent) -> RegistryAgent:
    """A field that is null in one file takes the value from another."""
    return replace(
        first,
        name=first.name or other.name,
        framework=first.framework or other.framework,
        owner=first.owner or other.owner,
        owner_team=first.owner_team or other.owner_team,
        credential=first.credential or other.credential,
    )


def no_owner_findings(agents: list[Agent]) -> list[Finding]:
    return [
        Finding(rule="agent.no_owner", subject=agent.id, agents=[agent.id])
        for agent in agents
        if agent.owner is None
    ]


def _findings(agents: list[Agent]) -> list[Finding]:
    findings = no_owner_findings(agents)
    by_credential: dict[str, list[str]] = {}
    for agent in agents:
        if agent.credential is not None:
            by_credential.setdefault(agent.credential, []).append(agent.id)
    findings += [
        Finding(rule="identity.shared_key_name", subject=credential, agents=sorted(ids))
        for credential, ids in sorted(by_credential.items())
        if len(ids) > 1
    ]
    return findings


def inspect_registry(
    sources: list[tuple[str, Path]], out: Path, now: datetime | None = None
) -> RegistryRun:
    for _, path in sources:
        if _same_file(path, out):
            raise RegistryError(f"--out {out} is one of the export files; choose another path")

    merged: dict[str, RegistryAgent] = {}
    where: dict[str, tuple[Path, int]] = {}
    ref_of_id: dict[str, str] = {}
    files: list[FileResult] = []
    exports: list[RegistryExport] = []
    warnings: list[str] = []
    for platform, path in sources:
        read = READERS[platform](path)
        warnings += read.warnings
        files.append(FileResult(platform=platform, path=path, agents=len(read.agents)))
        exports.append(
            RegistryExport(platform=platform, sha256=read.sha256, agents=len(read.agents))
        )
        for position, agent in read.agents:
            known_ref = ref_of_id.get(agent.id)
            if known_ref is not None and known_ref != agent.platform_ref:
                first_path, first_position = where[known_ref]
                raise RegistryError(
                    f"{path}: agent {position} and {first_path}: agent {first_position} are "
                    f"different agents that both give the ID {agent.id!r}"
                )
            ref_of_id[agent.id] = agent.platform_ref
            if agent.platform_ref in merged:
                merged[agent.platform_ref] = _merge(merged[agent.platform_ref], agent)
            else:
                merged[agent.platform_ref] = agent
                where[agent.platform_ref] = (path, position)

    agents = sorted(
        (
            Agent(
                id=agent.id,
                source="registry",
                name=agent.name,
                platform=agent.platform,
                platform_ref=agent.platform_ref,
                framework=agent.framework,
                owner=agent.owner,
                owner_team=agent.owner_team,
                credential=agent.credential,
            )
            for agent in merged.values()
        ),
        key=lambda agent: agent.id,
    )
    generated = (now or datetime.now(UTC)).astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    inventory = Inventory(
        schema_version=SCHEMA_VERSION,
        generated_at=generated,
        tool=f"ritvo-inspect {__version__}",
        manifest=Manifest(
            paths=[str(path) for _, path in sources],
            files_read=len(sources),
            # Deliberately unused here: a named export that cannot be read is an error.
            unreadable=[],
            servers_contacted=[],
            env_names_seen=[],
            registry_exports=exports,
            owner_overlays=[],
        ),
        agents=agents,
        mcp_servers=[],
        tools=[],
        identity=Identity(issuers=[]),
        findings=_findings(agents),
    )
    return RegistryRun(inventory=inventory, files=tuple(files), warnings=tuple(warnings))
