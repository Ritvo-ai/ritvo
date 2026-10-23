"""The inventory format, `inventory.json` (docs/SPEC.md section 4.8)."""

import json
import re
from pathlib import Path
from typing import Annotated, Literal, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictInt,
    StrictStr,
    ValidationError,
    model_validator,
)

SCHEMA_VERSION = 2

# The ID rule of docs/SPEC.md section 8.2.
ID_PATTERN = re.compile(r"[a-z0-9][a-z0-9._-]{0,63}")

Id = Annotated[StrictStr, Field(pattern=rf"^{ID_PATTERN.pattern}$")]
Sha256Ref = Annotated[StrictStr, Field(pattern=r"^sha256:[0-9a-f]{64}$")]
Count = Annotated[StrictInt, Field(ge=0)]
Rule = Literal[
    "agent.no_owner", "mcp.no_auth", "tool.destructive_unowned", "identity.shared_key_name"
]


class InventoryError(Exception):
    """An inventory file cannot be read or is not valid."""


class _Model(BaseModel):
    # Every field is required: a null is written out as null, never left to a default.
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


class RegistryExport(_Model):
    platform: StrictStr
    sha256: Annotated[StrictStr, Field(pattern=r"^[0-9a-f]{64}$")]
    agents: Count


class OwnerOverlay(_Model):
    agent: Id
    field: Literal["owner", "owner_team"]
    previous: Id | None
    source_row: Annotated[StrictInt, Field(ge=1)]


class Manifest(_Model):
    paths: list[StrictStr]
    files_read: Count
    unreadable: list[StrictStr]
    servers_contacted: list[Id]
    env_names_seen: list[StrictStr]
    registry_exports: list[RegistryExport]
    owner_overlays: list[OwnerOverlay]


class Agent(_Model):
    id: Id
    source: Literal["code", "config", "registry", "middleware"]
    name: StrictStr | None
    platform: StrictStr | None
    platform_ref: StrictStr | None
    framework: StrictStr | None
    owner: Id | None
    owner_team: Id | None
    credential: StrictStr | None


class Auth(_Model):
    protected_resource_metadata: StrictBool
    authorization_servers: list[StrictStr]
    scopes_supported: list[StrictStr]


class McpServer(_Model):
    id: Id
    source: Literal["config", "middleware"]
    transport: Literal["http", "stdio"]
    url: StrictStr | None
    command: StrictStr | None
    auth: Auth | None


class Annotations(_Model):
    readOnly: StrictBool | None
    destructive: StrictBool | None
    idempotent: StrictBool | None
    openWorld: StrictBool | None


class Tool(_Model):
    id: Annotated[StrictStr, Field(pattern=rf"^{ID_PATTERN.pattern}/.+$")]
    source: Literal["mcp", "code"]
    server: Id | None
    sdk: StrictStr | None
    platform_ref: StrictStr | None
    calls: list[StrictStr]
    description_hash: Sha256Ref | None
    input_schema_hash: Sha256Ref | None
    annotations: Annotations
    proposed_tier: Literal["low", "medium", "high"] | None
    reached_by: list[Id]

    @model_validator(mode="after")
    def _check(self) -> Self:
        if self.source == "mcp":
            if self.server is None or self.description_hash is None:
                raise ValueError("an mcp tool needs server and description_hash")
            if self.input_schema_hash is None or self.sdk or self.platform_ref or self.calls:
                raise ValueError("an mcp tool has input_schema_hash and no sdk, path or calls")
        elif self.server is not None or not self.sdk or not self.platform_ref or not self.calls:
            raise ValueError("a code tool has no server, and needs sdk, platform_ref and calls")
        return self


class Identity(_Model):
    issuers: list[StrictStr]


class Finding(_Model):
    rule: Rule
    subject: StrictStr
    agents: list[Id]


class Inventory(_Model):
    schema_version: StrictInt
    generated_at: Annotated[
        StrictStr, Field(pattern=r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?Z$")
    ]
    tool: Annotated[StrictStr, Field(pattern=r"^ritvo-inspect \S+$")]
    manifest: Manifest
    agents: list[Agent]
    mcp_servers: list[McpServer]
    tools: list[Tool]
    identity: Identity
    findings: list[Finding]

    @model_validator(mode="after")
    def _check(self) -> Self:
        if self.schema_version != SCHEMA_VERSION:
            raise ValueError(f"schema_version must be {SCHEMA_VERSION}")
        for field, ids in (
            ("agents", [agent.id for agent in self.agents]),
            ("mcp_servers", [server.id for server in self.mcp_servers]),
        ):
            seen: set[str] = set()
            for index, value in enumerate(ids):
                if value in seen:
                    raise ValueError(f"{field}.{index}.id: duplicate ID {value!r}")
                seen.add(value)
        return self


def _problems(exc: ValidationError) -> str:
    problems = []
    for error in exc.errors(include_url=False, include_input=False, include_context=False):
        where = ".".join(str(part) for part in error["loc"])
        message = error["msg"].removeprefix("Value error, ")
        problems.append(f"{where}: {message}" if where else message)
    return "; ".join(problems)


_OLD_VERSIONS = {0: "manifest.owner_overlays", 1: "tools found in code (tools[].source)"}


def load_inventory(path: Path) -> Inventory:
    try:
        data = json.loads(path.read_bytes().decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise InventoryError(f"{path.name} is not a UTF-8 JSON file") from None
    if not isinstance(data, dict):
        raise InventoryError(f"{path.name} must hold one JSON object")
    version = data.get("schema_version")
    if type(version) is int and version in _OLD_VERSIONS:
        raise InventoryError(
            f"{path.name} has schema_version {version}, written before {_OLD_VERSIONS[version]} "
            f"existed; run the inspector again to write a version {SCHEMA_VERSION} inventory"
        )
    if type(version) is not int or version != SCHEMA_VERSION:
        raise InventoryError(
            f"{path.name}: schema_version must be {SCHEMA_VERSION}; "
            "write the inventory again with this version of ritvo"
        )
    try:
        return Inventory.model_validate(data)
    except ValidationError as exc:
        raise InventoryError(f"{path.name}: {_problems(exc)}") from None


def write_inventory(inventory: Inventory, out: Path) -> None:
    text = json.dumps(inventory.model_dump(mode="json"), indent=2, ensure_ascii=False)
    out.write_text(text + "\n", encoding="utf-8", newline="\n")
