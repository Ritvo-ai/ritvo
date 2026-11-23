"""Ask the Cedar engine (docs/SPEC.md sections 4.3 and 5.9)."""

import json
import math
import re
from dataclasses import dataclass
from typing import Any

import cedarpy

from ritvo.adapter_contract.bundle import Bundle
from ritvo.enforcement.bundles import canary

SETS = ("deny", "over_limit", "ask", "allow")
_ID = re.compile(r'@id\("([^"]+)"\)')


class EngineError(Exception):
    """The engine cannot be used, or gave an answer that is neither Allow nor Deny."""


@dataclass(frozen=True)
class EngineResult:
    outcome: str  # deny, over_limit, ask, allow, or none when no policy permits the request
    binding_id: str | None


def context_for(
    mode: str, scopes: list[str], widened: bool, parameters: dict[str, Any]
) -> dict[str, Any]:
    """The Cedar context: whole numbers as they are, floats rounded up, anything else left out."""
    params: dict[str, int] = {}
    for name, value in parameters.items():
        if isinstance(value, bool) or not isinstance(value, int | float):
            continue
        if isinstance(value, float):
            if not math.isfinite(value):
                continue
            value = math.ceil(value)
        params[name] = value
    return {"mode": mode, "scopes": scopes, "widened": widened, "params": params}


class Engine:
    """The four policy sets of a bundle, parsed once. It runs the canary before it is usable."""

    def __init__(self, bundle: Bundle) -> None:
        self.policy_version = bundle.policy_version
        self._sets, self._ids, self._entities = self._parse(
            bundle.cedar.model_dump(), bundle.entities
        )
        self._self_test()

    @staticmethod
    def _parse(
        texts: dict[str, str], entities: list[dict[str, Any]]
    ) -> tuple[dict[str, Any], dict[str, list[str]], Any]:
        try:
            sets = {name: cedarpy.PolicySet.from_str(texts[name]) for name in SETS}
            parsed = cedarpy.Entities.from_json_str(json.dumps(entities))
        except (ValueError, RuntimeError, KeyError) as exc:
            raise EngineError(f"the policy does not load: {type(exc).__name__}") from None
        return sets, {name: _ID.findall(texts[name]) for name in SETS}, parsed

    def _self_test(self) -> None:
        try:
            probe = Engine.__new__(Engine)
            probe._sets, probe._ids, probe._entities = self._parse(canary.POLICIES, canary.ENTITIES)
            for agent, tool, params, outcome, policy in canary.CASES:
                got = probe.decide(agent, tool, context_for("attended", [], False, params))
                if (got.outcome, got.binding_id) != (outcome, policy):
                    raise EngineError("the engine failed its self-test")
        except Exception:
            raise EngineError("the engine failed its self-test") from None

    def decide(self, agent: str, tool: str, context: dict[str, Any]) -> EngineResult:
        request = {
            "principal": f'Agent::"{agent}"',
            "action": 'Action::"call"',
            "resource": f'Tool::"{tool}"',
            "context": context,
        }
        for name in SETS:
            try:
                answer = cedarpy.is_authorized(request, self._sets[name], self._entities)
            except Exception:
                raise EngineError("the engine raised an error") from None
            if answer.decision == cedarpy.Decision.Allow:
                reasons = answer.diagnostics.reasons
                index = int(reasons[0].removeprefix("policy")) if reasons else -1
                ids = self._ids[name]
                return EngineResult(name, ids[index] if 0 <= index < len(ids) else None)
            if answer.decision != cedarpy.Decision.Deny:
                raise EngineError("the engine gave an answer that is neither Allow nor Deny")
        return EngineResult("none", None)
