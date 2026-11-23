from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from conftest import NOW, Stack, make_stack

from ritvo.adapter_contract.bundle import sign_bundle
from ritvo.enforcement.bundles import engine as engine_module
from ritvo.enforcement.evaluate import Evaluator, Result, ledger_event
from ritvo.governance_os.routing.compile import compile_bundle
from ritvo.governance_os.routing.publish import publish_bundle
from ritvo.ledger.store import Ledger

ORDER = "broker.order"
QUOTE = "broker.quote"
ADJUST = "ledger.adjust"
HOP = {
    "boundary": "edge",
    "audience": "agent-platform",
    "scopes": ["orders:read"],
    "token_ref": "sha256:" + "ab" * 32,
}


def _eval(
    stack: Stack,
    tool: str,
    params: dict[str, Any] | None = None,
    mode: str = "enforce",
    **claims: Any,
) -> Result:
    token = stack.idp.mint(**claims)
    return stack.evaluator(mode).evaluate(token, tool, f"call {tool}", params or {})


def _widening(data: dict[str, Any]) -> None:
    data["identity"]["claims_map"]["chain"] = "ritvo_chain"


# The outcomes


def test_a_binding_that_allows(stack: Stack) -> None:
    result = _eval(stack, QUOTE)
    assert (result.action, result.would_decide, result.status) == ("allow", "allow", 200)
    assert result.binding_id and result.fingerprint and result.policy_version == 1
    assert result.principal is not None and result.principal.mode == "attended"


def test_a_binding_that_asks(stack: Stack) -> None:
    result = _eval(stack, ORDER, {"order_value": 100})
    assert (result.action, result.would_decide, result.status) == ("ask", "ask", 403)
    assert result.triggers == ()


def test_a_parameter_beyond_its_limit(stack: Stack) -> None:
    high = _eval(stack, ORDER, {"order_value": 60000})
    assert (high.action, high.triggers) == ("ask", ("limit",))
    low = _eval(stack, QUOTE, {"depth": 20}, sub="u-1", act={"sub": "acme.reporting"})
    assert (low.action, low.would_decide, low.triggers) == ("deny", "deny", ("limit",))
    ok = _eval(stack, QUOTE, {"depth": 5}, act={"sub": "acme.reporting"})
    assert ok.action == "allow"
    missing = _eval(stack, QUOTE, {}, act={"sub": "acme.reporting"})
    assert missing.action == "deny"


def test_high_tier_asks_even_if_the_policy_allows(tmp_path: Path) -> None:
    stack = make_stack(
        tmp_path, edit=lambda d: d["bindings"][0].update(decision="allow", limits={})
    )
    result = _eval(stack, ORDER)
    assert (result.action, result.would_decide, result.triggers) == ("ask", "ask", ("tier",))


def test_no_binding_is_an_authority_gap(stack: Stack) -> None:
    result = _eval(stack, ORDER, act={"sub": "acme.reporting"})
    assert (result.action, result.reason, result.triggers) == (
        "deny",
        "authority_gap",
        ("authority_gap",),
    )
    assert "authority_gap" in result.flags


def test_an_unknown_tool(stack: Stack) -> None:
    result = _eval(stack, "no.such.tool")
    assert (result.action, result.reason) == ("deny", "unknown_tool")
    assert "unknown_tool" in result.flags


# Observe mode


def test_observe_passes_everything_and_records_what_enforce_would_do(stack: Stack) -> None:
    gap = _eval(stack, ORDER, mode="observe", act={"sub": "acme.reporting"})
    assert (gap.action, gap.would_decide, gap.status, gap.mode) == ("allow", "deny", 200, "observe")
    ask = _eval(stack, ORDER, {"order_value": 1}, mode="observe")
    assert (ask.action, ask.would_decide) == ("allow", "ask")
    unknown = _eval(stack, "no.such.tool", mode="observe")
    assert (unknown.action, unknown.would_decide) == ("allow", "deny")


def test_a_token_failure_is_refused_in_both_modes(stack: Stack) -> None:
    for mode in ("observe", "enforce"):
        result = _eval(stack, QUOTE, mode=mode, exp=int(NOW) - 1000)
        assert (result.action, result.status, result.reason, result.principal) == (
            "refuse",
            401,
            "expired",
            None,
        )


def test_another_audience_and_a_forwarded_upstream_token_are_refused(stack: Stack) -> None:
    assert _eval(stack, QUOTE, aud="other-mcp").reason == "wrong_audience"
    assert _eval(stack, QUOTE, aud="agent-platform").reason == "wrong_audience"


# Identity


def test_the_same_agent_for_two_users_is_two_requests(stack: Stack) -> None:
    one = _eval(stack, ORDER, {"order_value": 1}, sub="u-1")
    two = _eval(stack, ORDER, {"order_value": 1}, sub="u-2")
    again = _eval(stack, ORDER, {"order_value": 1}, sub="u-1")
    assert one.fingerprint != two.fingerprint
    assert one.fingerprint == again.fingerprint


def test_changed_parameters_or_tool_change_the_fingerprint(stack: Stack) -> None:
    base = _eval(stack, ORDER, {"order_value": 1})
    assert base.fingerprint != _eval(stack, ORDER, {"order_value": 2}).fingerprint
    assert base.fingerprint != _eval(stack, QUOTE, {"order_value": 1}).fingerprint


def test_an_unattended_agent_with_no_owner(stack: Stack) -> None:
    kwargs: dict[str, Any] = {"sub": "acme.reporting", "act": None}
    enforce = _eval(stack, QUOTE, {"depth": 1}, **kwargs)
    assert (enforce.action, enforce.reason) == ("deny", "no_owner")
    observe = _eval(stack, QUOTE, {"depth": 1}, mode="observe", **kwargs)
    assert (observe.action, observe.would_decide) == ("allow", "deny")
    assert {"no_owner", "no_accountable_human"} <= set(observe.flags)


def test_an_unattended_agent_with_an_owner_goes_on_to_the_policy(stack: Stack) -> None:
    result = _eval(stack, QUOTE, sub="acme.research", act=None)
    assert (result.action, result.principal.mode) == ("allow", "unattended")  # type: ignore[union-attr]


def test_unattended_allow_in_config_lets_an_ownerless_agent_through(tmp_path: Path) -> None:
    stack = make_stack(tmp_path)
    evaluator = stack.evaluator()
    evaluator.require_owner = False
    token = stack.idp.mint(sub="acme.reporting", act=None)
    result = evaluator.evaluate(token, QUOTE, "a", {"depth": 1})
    assert (result.action, result.reason) == ("allow", "allow")


# Widening scopes, by tier


@pytest.mark.parametrize(
    ("tool", "params", "expected"),
    [(QUOTE, {}, "allow"), (ADJUST, {}, "ask"), (ORDER, {"order_value": 1}, "deny")],
)
def test_a_widening_hop_is_handled_by_tier(
    tmp_path: Path, tool: str, params: dict[str, Any], expected: str
) -> None:
    stack = make_stack(tmp_path, edit=_widening)
    result = _eval(stack, tool, params, ritvo_chain=[HOP])
    assert result.action == expected
    assert "scope_widened" in result.flags
    if expected != "allow":
        assert "scope_widened" in result.triggers


def test_a_narrowing_chain_is_untouched(tmp_path: Path) -> None:
    stack = make_stack(tmp_path, edit=_widening)
    wide = {**HOP, "scopes": ["orders:read", "orders:write", "admin"]}
    result = _eval(stack, ADJUST, ritvo_chain=[wide])
    assert (result.action, result.flags) == ("allow", ())


# Fail modes


def _empty_store(stack: Stack) -> Any:
    from ritvo.enforcement.bundles.store import BundleStore

    return BundleStore({}, clock=lambda: stack.clock[0])


def test_no_bundle_fails_closed_for_every_tier(stack: Stack) -> None:
    evaluator = stack.evaluator(store=_empty_store(stack))
    for tool in (QUOTE, ADJUST, ORDER):
        result = evaluator.evaluate(stack.idp.mint(), tool, "a", {})
        assert (result.action, result.reason) == ("deny", "no_bundle")


def test_no_bundle_in_observe_mode_passes(stack: Stack) -> None:
    result = stack.evaluator("observe", _empty_store(stack)).evaluate(
        stack.idp.mint(), ORDER, "a", {}
    )
    assert (result.action, result.would_decide, result.reason) == ("allow", "deny", "no_bundle")


def test_a_stale_bundle_fails_per_tier(tmp_path: Path) -> None:
    stack = make_stack(tmp_path, fail_open_low=True)
    store = stack.store()
    stack.clock[0] = NOW + 61 * 60
    evaluator = stack.evaluator(store=store)
    token = stack.idp.mint(exp=int(stack.clock[0]) + 3600)
    results = {tool: evaluator.evaluate(token, tool, "a", {}) for tool in (QUOTE, ADJUST, ORDER)}
    assert results[QUOTE].action == "allow" and results[QUOTE].would_decide == "allow"
    assert results[ADJUST].action == "deny" and results[ORDER].action == "deny"
    assert all(r.reason == "bundle_stale" for r in results.values() if r.action == "deny")


def test_low_tier_is_closed_unless_configured(stack: Stack) -> None:
    stack.clock[0] = NOW + 61 * 60
    token = stack.idp.mint(exp=int(stack.clock[0]) + 3600)
    assert stack.evaluator().evaluate(token, QUOTE, "a", {}).action == "deny"


def _broken_bundle(stack: Stack) -> Any:
    bundle = compile_bundle(stack.config, 2, datetime.fromtimestamp(NOW, UTC))
    bundle = bundle.model_copy(
        update={"cedar": bundle.cedar.model_copy(update={"allow": "permit(;"})}
    )
    return sign_bundle(bundle, stack.seed)


def test_an_engine_that_cannot_load_fails_by_tier(tmp_path: Path) -> None:
    stack = make_stack(tmp_path, fail_open_low=True)
    signed = _broken_bundle(stack)
    store = stack.store(signed)
    # the broken bundle announces the low tier as open
    bundle = signed.bundle.model_copy(
        update={"fail_modes": signed.bundle.fail_modes.model_copy(update={"low": "open"})}
    )
    store = stack.store(sign_bundle(bundle, stack.seed))
    evaluator = stack.evaluator(store=store)
    token = stack.idp.mint()
    assert evaluator.evaluate(token, QUOTE, "a", {}).action == "allow"
    medium = evaluator.evaluate(token, ADJUST, "a", {})
    assert (medium.action, medium.reason) == ("deny", "engine_error")
    assert evaluator.evaluate(token, ORDER, "a", {}).action == "deny"


def test_an_engine_that_fails_its_canary_is_not_used(
    stack: Stack, monkeypatch: pytest.MonkeyPatch
) -> None:
    def failing(self: object) -> None:
        raise engine_module.EngineError("the engine failed its self-test")

    monkeypatch.setattr(engine_module.Engine, "_self_test", failing)
    result = _eval(stack, QUOTE)
    assert (result.action, result.reason) == ("deny", "engine_error")
    observe = _eval(stack, QUOTE, mode="observe")
    assert (observe.action, observe.would_decide) == ("allow", "deny")


def test_a_new_bundle_version_gets_a_new_engine(stack: Stack) -> None:
    evaluator = stack.evaluator()
    token = stack.idp.mint()
    assert evaluator.evaluate(token, QUOTE, "a", {}).action == "allow"
    stack.config = (
        make_stack(stack.idp.file.parent / "x", edit=lambda d: d["bindings"].pop(1)).config
        if False
        else stack.config
    )
    revoked = compile_bundle(stack.config, 2, datetime.fromtimestamp(NOW, UTC))
    revoked = revoked.model_copy(update={"cedar": revoked.cedar.model_copy(update={"allow": ""})})
    evaluator.store.install(sign_bundle(revoked, stack.seed))
    again = evaluator.evaluate(token, QUOTE, "a", {})
    assert (again.action, again.policy_version, again.reason) == ("deny", 2, "authority_gap")


# The ledger


def test_the_ledger_entry_for_an_evaluation(stack: Stack, tmp_path: Path) -> None:
    result = _eval(stack, ORDER, {"order_value": 1}, mode="observe")
    event = ledger_event(result, ORDER, "POST /orders", "mcp", "evt-1", "2026-11-23T10:00:00Z")
    assert event.parameter_fingerprint == result.fingerprint
    assert (event.mode, event.decision, event.would_decide) == ("observe", "allow", "ask")
    assert event.principal == result.principal and event.policy_version == 1
    ledger = Ledger(tmp_path / "l.db")
    ledger.append(event)
    assert ledger.verify().ok
    assert "u-1" in ledger.export().__next__()


def test_a_refusal_is_written_without_a_principal(stack: Stack) -> None:
    result = _eval(stack, QUOTE, aud="x")
    event = ledger_event(result, QUOTE, "a", "mcp", "evt-2", "2026-11-23T10:00:00Z")
    assert (event.decision, event.outcome, event.principal) == ("deny", "wrong_audience", None)


def test_the_ledger_never_holds_the_token_or_parameters(stack: Stack, tmp_path: Path) -> None:
    token = stack.idp.mint()
    result = stack.evaluator().evaluate(
        token, ORDER, "a", {"order_value": 31415, "memo": "private-note"}
    )
    ledger = Ledger(tmp_path / "l.db")
    ledger.append(ledger_event(result, ORDER, "a", "mcp", "evt-3", "2026-11-23T10:00:00Z"))
    text = next(ledger.export())
    assert token not in text and "31415" not in text and "private-note" not in text
    assert isinstance(stack.evaluator(), Evaluator) and publish_bundle is not None
