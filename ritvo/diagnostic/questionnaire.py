"""The init questionnaire and the first draft ritvo.yaml it gives (docs/SPEC.md section 8.7)."""

import hashlib
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, StrictInt, StrictStr, ValidationError

from ritvo.diagnostic.gaps.model import Input
from ritvo.diagnostic.gaps.rules import Inputs, diagnose
from ritvo.diagnostic.intake.columns import Mode, Tier
from ritvo.diagnostic.intake.reader import Agent, Intake, Person, Tool
from ritvo.diagnostic.report.draft import draft_data
from ritvo.inspector.inventory import ID_PATTERN

QUESTIONNAIRE_VERSION = 1
QUESTIONNAIRE_FILE = "questionnaire.yaml"
DRAFT_FILE = "ritvo.draft.yaml"
DEFAULT_COOL_OFF = 60
DEFAULT_TIMEOUT = 30
DEFAULT_HIGH_LADDER = ("escalation_reviewer",)
# Approval expiry proposed for each tier; low tier acts with no approval, so none.
TTL_BY_TIER = {"high": 15, "medium": 30}

Id = Annotated[StrictStr, Field(pattern=rf"^{ID_PATTERN.pattern}$")]
Minutes = Annotated[StrictInt, Field(ge=1)]


class QuestionnaireError(Exception):
    """The questionnaire file cannot be read or is not valid. Nothing is written."""


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


class QAgent(_Model):
    id: Id
    owner: Id
    owner_team: Id | None = None
    mode: Literal["attended", "unattended", "both"]
    tools: list[Id]


class QTool(_Model):
    id: Id
    tier: Literal["low", "medium", "high"]
    owner_team: Id | None = None


class Questionnaire(_Model):
    questionnaire_version: StrictInt
    agents: Annotated[list[QAgent], Field(min_length=1)]
    tools: Annotated[list[QTool], Field(min_length=1)]
    roles: dict[Id, Annotated[list[Id], Field(min_length=1)]]
    cool_off_minutes: Minutes = DEFAULT_COOL_OFF
    ladder_timeout_minutes: Minutes = DEFAULT_TIMEOUT
    high_ladder: Annotated[list[Id], Field(min_length=1)] = list(DEFAULT_HIGH_LADDER)


def _short(value: object) -> str:
    text = repr(value)
    return text if len(text) <= 40 else text[:37] + "..."


def _problems(exc: ValidationError) -> list[str]:
    return [
        f"{'.'.join(str(part) for part in error['loc'])}: {error['msg']}"
        for error in exc.errors(include_url=False, include_input=False, include_context=False)
    ]


def parse_questionnaire(data: Any) -> Questionnaire:
    if not isinstance(data, dict):
        raise QuestionnaireError("the questionnaire must be a YAML mapping")
    try:
        questionnaire = Questionnaire.model_validate(data)
    except ValidationError as exc:
        raise QuestionnaireError("; ".join(_problems(exc))) from None
    problems = []
    if questionnaire.questionnaire_version != QUESTIONNAIRE_VERSION:
        problems.append(f"questionnaire_version must be {QUESTIONNAIRE_VERSION}")
    for field, ids in (
        ("agents", [agent.id for agent in questionnaire.agents]),
        ("tools", [tool.id for tool in questionnaire.tools]),
    ):
        seen: set[str] = set()
        for index, value in enumerate(ids):
            if value in seen:
                problems.append(f"{field}.{index}.id: repeated ID {_short(value)}")
            seen.add(value)
    tool_ids = {tool.id for tool in questionnaire.tools}
    for index, agent in enumerate(questionnaire.agents):
        for tool in agent.tools:
            if tool not in tool_ids:
                problems.append(f"agents.{index}.tools: {_short(tool)} is not in tools")
    for role in ("policy_author", "escalation_reviewer"):
        if role not in questionnaire.roles:
            problems.append(f"roles.{role}: required")
    for role in questionnaire.high_ladder:
        if role not in questionnaire.roles:
            problems.append(f"high_ladder: {_short(role)} is not a role in roles")
    if problems:
        raise QuestionnaireError("; ".join(problems))
    return questionnaire


def load_questionnaire(path: Path) -> tuple[Questionnaire, str]:
    """The questionnaire and the SHA-256 of the file."""
    raw = path.read_bytes()
    try:
        data = yaml.safe_load(raw.decode("utf-8"))
    except UnicodeDecodeError:
        raise QuestionnaireError(f"{path.name} is not UTF-8 text") from None
    except yaml.YAMLError:
        raise QuestionnaireError(f"{path.name} is not valid YAML") from None
    return parse_questionnaire(data), hashlib.sha256(raw).hexdigest()


def _intake(questionnaire: Questionnaire) -> Intake:
    people: dict[str, list[str]] = {}
    for agent in questionnaire.agents:
        people.setdefault(agent.owner, [])
    for role, holders in questionnaire.roles.items():
        for person in holders:
            if role not in people.setdefault(person, []):
                people[person].append(role)
    return Intake(
        agents=tuple(
            Agent(
                agent_id=agent.id,
                name=None,
                platform=None,
                built_by_team=None,
                owner=agent.owner,
                owner_team=agent.owner_team,
                mode=Mode(agent.mode),
                credential=None,
                tools=tuple(agent.tools),
                line=0,
            )
            for agent in questionnaire.agents
        ),
        tools=tuple(
            Tool(
                tool_id=tool.id,
                system=None,
                access=None,
                description=None,
                tier=Tier(tool.tier),
                owner_team=tool.owner_team,
                approval_ttl_minutes=None,
            )
            for tool in questionnaire.tools
        ),
        people=tuple(
            Person(person_id=person, team=None, roles=tuple(roles), deputy=None)
            for person, roles in people.items()
        ),
    )


def draft_text(questionnaire: Questionnaire, sha256: str) -> str:
    """The first draft ritvo.yaml: the diagnose draft (section 8.5) plus the defaults per tier."""
    inputs = Inputs(_intake(questionnaire), None, (Input(file=QUESTIONNAIRE_FILE, sha256=sha256),))
    diagnosis = diagnose(inputs, datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"))
    data = draft_data(diagnosis)
    tools: list[dict[str, Any]] = data["tools"]  # type: ignore[assignment]
    for entry in tools:
        ttl = TTL_BY_TIER.get(str(entry["tier"]))
        if ttl is not None:
            entry["approval_ttl_minutes"] = ttl
    roles: dict[str, Any] = dict(data["roles"])  # type: ignore[call-overload]
    roles["cool_off_minutes"] = questionnaire.cool_off_minutes
    data["roles"] = roles
    data["ladders"] = {
        "high": list(questionnaire.high_ladder),
        "timeout_minutes": questionnaire.ladder_timeout_minutes,
    }
    return yaml.safe_dump(data, sort_keys=False, allow_unicode=True)


def questionnaire_text(data: dict[str, Any]) -> str:
    return yaml.safe_dump(data, sort_keys=False, allow_unicode=True)
