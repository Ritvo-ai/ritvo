"""The shape of ritvo.yaml (docs/SPEC.md section 4.1). Whole-file rules are in load."""

from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictFloat,
    StrictInt,
    StrictStr,
    field_validator,
)

ID_PATTERN = r"^[a-z0-9][a-z0-9._-]{0,63}$"

Id = Annotated[StrictStr, Field(pattern=ID_PATTERN)]
Minutes = Annotated[StrictInt, Field(ge=1)]
Number = StrictInt | StrictFloat
Tier = Literal["low", "medium", "high"]
DottedPath = Annotated[StrictStr, Field(pattern=r"^[^.\s]+(\.[^.\s]+)*$")]


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


class Enforcement(_Model):
    id: Id
    type: Annotated[
        StrictStr, Field(pattern=r"^(mcp_middleware|mock|adapter:[a-z0-9][a-z0-9._-]*)$")
    ]
    boundary: Literal["edge", "mcp", "data"]


class ClaimsMap(_Model):
    subject: DottedPath
    actor: DottedPath
    scopes: DottedPath
    actor_platform: DottedPath | None = None
    chain: DottedPath | None = None


class Issuer(_Model):
    name: Id
    # The `iss` claim of its tokens; the name if not given.
    issuer: StrictStr | None = None
    jwks_url: Annotated[StrictStr, Field(pattern=r"^(secret://\S+|https://\S+|file://\S+)$")]
    audience: StrictStr
    claims_map: ClaimsMap | None = None


class Identity(_Model):
    issuers: list[Issuer]
    claims_map: ClaimsMap | None = None
    unattended: Literal["require_owner", "allow"] = "require_owner"
    jwks_leeway_seconds: Annotated[StrictInt, Field(ge=0)] = 60


class Secrets(_Model):
    backend: Literal["keyring", "env_file", "memory"]


class Agent(_Model):
    id: Id
    owner: Id | None = None
    owner_team: Id | None = None
    mode: Literal["attended", "unattended", "both"] | None = None


class Match(_Model):
    method: StrictStr
    path: StrictStr


class Band(_Model):
    up_to: Number | None = None
    ladder: list[Id]


class Approvals(_Model):
    parameter: StrictStr
    currency: StrictStr | None = None
    bands: Annotated[list[Band], Field(min_length=1)]

    @field_validator("bands")
    @classmethod
    def _ascending(cls, bands: list[Band]) -> list[Band]:
        bounds = [band.up_to for band in bands]
        if None in bounds[:-1]:
            raise ValueError("only the last band has no up_to")
        known = [bound for bound in bounds if bound is not None]
        if known != sorted(known) or len(set(known)) != len(known):
            raise ValueError("up_to must rise from one band to the next")
        return bands


class Tool(_Model):
    id: Id
    match: Match | None = None
    tier: Tier | None = None
    approval_ttl_minutes: Minutes | None = None
    approvals: Approvals | None = None


class Binding(_Model):
    agent: Id
    tool: Id
    decision: Literal["allow", "ask", "deny"]
    limits: dict[StrictStr, Number] = Field(default_factory=dict)
    owner_team: Id | None = None


class PolicySection(_Model):
    id: Id
    signoff: list[Id]


class Roles(_Model):
    """Role names map to person IDs; `cool_off_minutes` sits in the same map."""

    model_config = ConfigDict(extra="allow", strict=True, frozen=True)

    cool_off_minutes: Minutes | None = None


class Ladders(_Model):
    low: list[Id] | None = None
    medium: list[Id] | None = None
    high: list[Id] | None = None
    timeout_minutes: Minutes | None = None


class LedgerConfig(_Model):
    path: StrictStr = "ritvo-ledger.db"


class BundleConfig(_Model):
    path: StrictStr = "ritvo-bundle.json"
    keys: StrictStr = "ritvo-keys.json"


class DecisionService(_Model):
    url: Annotated[
        StrictStr, Field(pattern=r"^(https://\S+|http://(localhost|127\.0\.0\.1)(:\d+)?(/\S*)?)$")
    ]
    key_id: Id
    key: Annotated[StrictStr, Field(pattern=r"^secret://\S+$")]


class Config(_Model):
    mode: Literal["observe", "enforce"] | None = None
    profile: Literal["local", "cloud"] | None = None
    enforcement: list[Enforcement] = Field(default_factory=list)
    identity: Identity | None = None
    secrets: Secrets | None = None
    agents: list[Agent] = Field(default_factory=list)
    tools: list[Tool] = Field(default_factory=list)
    bindings: list[Binding] = Field(default_factory=list)
    policy_sections: list[PolicySection] = Field(default_factory=list)
    roles: Roles | None = None
    ladders: Ladders | None = None
    ledger: LedgerConfig = Field(default_factory=LedgerConfig)
    bundle: BundleConfig = Field(default_factory=BundleConfig)
    decision_service: DecisionService | None = None
    fail_open_low: bool = False

    def role_holders(self) -> dict[str, list[str]]:
        """Role name to person IDs. A single ID is a list of one."""
        if self.roles is None:
            return {}
        extra = self.roles.model_extra or {}
        return {
            name: [value] if isinstance(value, str) else list(value)
            for name, value in extra.items()
        }
