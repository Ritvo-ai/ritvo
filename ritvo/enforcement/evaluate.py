"""The evaluation core: one function every enforcement point uses (docs/SPEC.md section 5.11)."""

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Literal

from ritvo.adapter_contract.fingerprint import request_fingerprint
from ritvo.adapter_contract.principal import Boundary, Principal
from ritvo.enforcement.bundles.engine import Engine, EngineError, context_for
from ritvo.enforcement.bundles.store import BundleStore
from ritvo.enforcement.identity.principal import build_principal
from ritvo.enforcement.identity.tokens import TokenError, TokenValidator
from ritvo.ledger.event import LedgerEvent

Verdict = Literal["allow", "ask", "deny"]


@dataclass(frozen=True)
class Result:
    """What to do with a call, and what the policy would have decided."""

    action: Literal["allow", "ask", "deny", "refuse"]
    would_decide: Verdict | None
    status: int
    reason: str
    mode: Literal["observe", "enforce"]
    principal: Principal | None = None
    fingerprint: str | None = None
    binding_id: str | None = None
    policy_version: int | None = None
    flags: tuple[str, ...] = ()
    triggers: tuple[str, ...] = ()
    tier: str | None = None


def _status(action: str) -> int:
    return {"allow": 200, "refuse": 401}.get(action, 403)


@dataclass
class Evaluator:
    mode: Literal["observe", "enforce"]
    boundary: Boundary
    validator: TokenValidator
    store: BundleStore
    owner_of: Callable[[str], str | None]
    require_owner: bool = True
    clock: Callable[[], float] = time.time
    _engine: Engine | None = field(default=None, init=False, repr=False)
    _engine_for: int | None = field(default=None, init=False, repr=False)
    _engine_failed: bool = field(default=False, init=False, repr=False)

    def _engine_now(self) -> Engine | None:
        """The engine for the bundle held, built once per version. None if it cannot be used."""
        bundle = self.store.bundle
        if bundle is None:
            return None
        if self._engine_for != bundle.policy_version:
            self._engine_for = bundle.policy_version
            try:
                self._engine, self._engine_failed = Engine(bundle), False
            except EngineError:
                self._engine, self._engine_failed = None, True
        return self._engine

    def _finish(self, would: Verdict, base: dict[str, Any], flags: list[str]) -> Result:
        action: Literal["allow", "ask", "deny"] = "allow" if self.mode == "observe" else would
        return Result(
            action=action,
            would_decide=would,
            status=_status(action),
            flags=tuple(flags),
            mode=self.mode,
            **base,
        )

    def evaluate(
        self, token: str, tool: str, action: str, parameters: dict[str, Any] | None
    ) -> Result:
        params = parameters or {}
        try:
            validated = self.validator.validate(token)
            principal, flags = build_principal(validated, self.boundary, self.owner_of)
        except TokenError as exc:
            return Result("refuse", None, 401, exc.code, self.mode)
        base: dict[str, Any] = {
            "principal": principal,
            "fingerprint": request_fingerprint(principal, tool, action, params),
        }
        bundle = self.store.bundle
        entry = None if bundle is None else next((t for t in bundle.tools if t.id == tool), None)
        tier = entry.tier if entry is not None else None
        base["tier"] = tier
        triggers: list[str] = []

        def failure(reason: str) -> Result:
            """No usable bundle or engine: the fail mode of the tool's tier decides (5.6)."""
            open_ = (
                bundle is not None
                and tier is not None
                and getattr(bundle.fail_modes, tier) == "open"
            )
            return self._finish(
                "allow" if open_ else "deny",
                {**base, "reason": reason, "policy_version": None, "triggers": (reason,)},
                [*flags, reason],
            )

        if bundle is None:
            return failure("no_bundle")
        base["policy_version"] = bundle.policy_version
        if self.store.is_stale():
            return failure("bundle_stale")
        engine = self._engine_now()
        if engine is None:
            return failure("engine_error")

        if entry is None:
            triggers.append("unknown_tool")
            return self._finish(
                "deny",
                {**base, "reason": "unknown_tool", "triggers": tuple(triggers)},
                [*flags, "unknown_tool"],
            )
        hop = principal.chain[-1]
        try:
            found = engine.decide(
                principal.actor.id,
                tool,
                context_for(principal.mode, hop.scopes, "scope_widened" in flags, params),
            )
        except EngineError:
            return failure("engine_error")
        base["binding_id"] = found.binding_id
        would: Verdict
        if found.outcome == "none":
            would = "deny"
            triggers.append("authority_gap")
            flags.append("authority_gap")
        elif found.outcome == "over_limit":
            would = "ask" if entry.tier in ("medium", "high") else "deny"
            triggers.append("limit")
        else:
            would = found.outcome  # type: ignore[assignment]
        tier_rule = next((r for r in bundle.ask_trigger_rules if r.id == "tier"), None)
        if (
            tier_rule is not None
            and tier_rule.enabled
            and entry.tier == "high"
            and would == "allow"
        ):
            would = "ask"
            triggers.append("tier")
        if "scope_widened" in flags and would != "deny":
            if entry.tier == "high":
                would = "deny"
                triggers.append("scope_widened")
            elif entry.tier == "medium" and would == "allow":
                would = "ask"
                triggers.append("scope_widened")
        if "no_owner" in flags and self.require_owner and would != "deny":
            would = "deny"
            triggers.append("no_owner")
        reason = triggers[-1] if triggers else would
        return self._finish(would, {**base, "reason": reason, "triggers": tuple(triggers)}, flags)


def ledger_event(
    result: Result, tool: str, action: str, boundary: Boundary, event_id: str, timestamp: str
) -> LedgerEvent:
    """The ledger entry for one evaluated call (docs/SPEC.md section 4.7)."""
    decision: Verdict = "deny" if result.action in ("deny", "refuse") else result.action  # type: ignore[assignment]
    return LedgerEvent(
        event_id=event_id,
        timestamp=timestamp,
        type="decision",
        principal=result.principal,
        boundary=boundary,
        tool=tool,
        action=action,
        parameter_fingerprint=result.fingerprint,
        policy_version=result.policy_version,
        binding_id=result.binding_id,
        mode=result.mode,
        decision=decision,
        would_decide=result.would_decide,
        triggering_rule=",".join(result.triggers) or None,
        flags=list(result.flags),
        outcome=result.reason,
    )
