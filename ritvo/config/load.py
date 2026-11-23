"""Load and validate ritvo.yaml (docs/SPEC.md section 4.1)."""

import re
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from ritvo.config.schema import ID_PATTERN, Config

_SECRET_KEY = re.compile(r"token|secret|password|passwd|api_?key|private_?key", re.IGNORECASE)
_ID = re.compile(ID_PATTERN)
_REQUIRED = ("mode", "profile", "identity", "secrets", "roles", "ladders")


class ConfigError(Exception):
    """ritvo.yaml cannot be read or is not valid. Every problem is listed; nothing is used."""

    def __init__(self, problems: list[str]) -> None:
        super().__init__("; ".join(problems))
        self.problems = problems


def _short(value: object) -> str:
    text = repr(value)
    return text if len(text) <= 40 else text[:37] + "..."


def _plain_secrets(node: Any, where: str, problems: list[str]) -> None:
    """A secret-looking key must hold a secret:// reference. Only the key path is reported."""
    if isinstance(node, dict):
        for key, value in node.items():
            here = f"{where}.{key}" if where else str(key)
            if (
                isinstance(key, str)
                and _SECRET_KEY.search(key)
                and not key.lower().endswith("_ref")
                and isinstance(value, str)
                and value
                and not value.startswith("secret://")
            ):
                problems.append(
                    f"{here}: a secret is written as a secret:// reference, not a value"
                )
            _plain_secrets(value, here, problems)
    elif isinstance(node, list):
        for index, value in enumerate(node):
            _plain_secrets(value, f"{where}.{index}", problems)


def _check(config: Config, draft: bool) -> list[str]:
    problems: list[str] = []
    if not draft:
        for name in _REQUIRED:
            if getattr(config, name) is None:
                problems.append(f"{name}: required in a complete file")
        if not config.enforcement:
            problems.append("enforcement: at least one entry is required in a complete file")
        for index, tool in enumerate(config.tools):
            if tool.tier is None:
                problems.append(f"tools.{index}.tier: required in a complete file")
    for field, ids in (
        ("enforcement", [entry.id for entry in config.enforcement]),
        ("agents", [agent.id for agent in config.agents]),
        ("tools", [tool.id for tool in config.tools]),
    ):
        seen: set[str] = set()
        for index, value in enumerate(ids):
            if value in seen:
                problems.append(f"{field}.{index}.id: repeated ID {_short(value)}")
            seen.add(value)
    agents = {agent.id for agent in config.agents}
    tools = {tool.id for tool in config.tools}
    for index, binding in enumerate(config.bindings):
        if binding.agent not in agents:
            problems.append(f"bindings.{index}.agent: {_short(binding.agent)} is not an agent")
        if binding.tool not in tools:
            problems.append(f"bindings.{index}.tool: {_short(binding.tool)} is not a tool")
    if config.mode == "enforce" or (config.mode is None and not draft):
        for index, agent in enumerate(config.agents):
            if config.mode == "enforce" and agent.owner is None:
                problems.append(f"agents.{index}.owner: required when mode is enforce")
    for name, holders in config.role_holders().items():
        if not _ID.fullmatch(name):
            problems.append(f"roles: {_short(name)} is not a valid role name")
        for person in holders:
            if not isinstance(person, str) or not _ID.fullmatch(person):
                problems.append(f"roles.{_short(name)}: holders are person IDs")
    if config.identity is not None:
        for index, issuer in enumerate(config.identity.issuers):
            if issuer.claims_map is None and config.identity.claims_map is None:
                problems.append(f"identity.issuers.{index}.claims_map: none here or in identity")
    return problems


def parse_config(data: Any, draft: bool = False) -> Config:
    """Validate a config already read as data. `draft` accepts a file that leaves things out."""
    if not isinstance(data, dict):
        raise ConfigError(["the config must be a YAML mapping"])
    problems: list[str] = []
    _plain_secrets(data, "", problems)
    try:
        config = Config.model_validate(data)
    except ValidationError as exc:
        problems += [
            f"{'.'.join(str(part) for part in error['loc'])}: {error['msg']}"
            for error in exc.errors(include_url=False, include_input=False, include_context=False)
        ]
        raise ConfigError(problems) from None
    problems += _check(config, draft)
    if problems:
        raise ConfigError(problems)
    return config


def load_config(path: Path, draft: bool = False) -> Config:
    """Read and validate a ritvo.yaml. `draft` accepts a file that leaves things out."""
    try:
        data = yaml.safe_load(path.read_bytes().decode("utf-8"))
    except UnicodeDecodeError:
        raise ConfigError([f"{path.name} is not UTF-8 text"]) from None
    except yaml.YAMLError:
        raise ConfigError([f"{path.name} is not valid YAML"]) from None
    if not isinstance(data, dict):
        raise ConfigError([f"{path.name} must be a YAML mapping"])
    return parse_config(data, draft)
