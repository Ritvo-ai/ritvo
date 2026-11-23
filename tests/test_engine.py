import copy

import pytest
from conftest import Stack

from ritvo.adapter_contract.bundle import Bundle
from ritvo.enforcement.bundles import canary
from ritvo.enforcement.bundles.engine import Engine, EngineError, context_for

_PERMIT = (
    '{id}permit(principal {who}, action == Action::"call", resource == Tool::"broker.quote");\n'
)


def _engine(stack: Stack) -> Engine:
    return Engine(stack.signed.bundle)


def _ctx(**params: object) -> dict[str, object]:
    return context_for("attended", ["orders:write"], False, dict(params))


def test_the_engine_passes_its_canary_and_decides(stack: Stack) -> None:
    engine = _engine(stack)
    assert engine.decide("acme.research", "broker.order", _ctx(order_value=100)).outcome == "ask"
    assert engine.decide("acme.research", "broker.quote", _ctx()).outcome == "allow"
    assert (
        engine.decide("acme.research", "broker.order", _ctx(order_value=60000)).outcome
        == "over_limit"
    )
    assert engine.decide("acme.research", "broker.order", _ctx()).outcome == "over_limit"
    assert engine.decide("acme.reporting", "broker.order", _ctx()).outcome == "none"
    assert engine.decide("ghost", "broker.quote", _ctx()).outcome == "none"


def test_the_binding_that_decided_is_named(stack: Stack) -> None:
    engine = _engine(stack)
    ids = {b.tool: b.id for b in stack.signed.bundle.bindings if b.agent == "acme.research"}
    assert engine.decide("acme.research", "broker.quote", _ctx()).binding_id == ids["broker.quote"]
    assert (
        engine.decide("acme.research", "broker.order", _ctx(order_value=1)).binding_id
        == ids["broker.order"]
    )


def test_deny_is_checked_before_everything_else(stack: Stack) -> None:
    bundle = stack.signed.bundle.model_copy(
        update={
            "cedar": stack.signed.bundle.cedar.model_copy(
                update={"deny": _PERMIT.format(id='@id("b-9")\n', who='== Agent::"acme.research"')}
            )
        }
    )
    result = Engine(bundle).decide("acme.research", "broker.quote", _ctx())
    assert (result.outcome, result.binding_id) == ("deny", "b-9")


def test_the_parent_team_is_part_of_the_principal(stack: Stack) -> None:
    bundle = stack.signed.bundle.model_copy(
        update={
            "cedar": stack.signed.bundle.cedar.model_copy(
                update={"allow": _PERMIT.format(id="", who='in Team::"research"')}
            )
        }
    )
    assert Engine(bundle).decide("acme.research", "broker.quote", _ctx()).outcome == "allow"
    assert Engine(bundle).decide("ghost", "broker.quote", _ctx()).outcome == "none"


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (5, "allow"),
        (5.0, "allow"),
        (5.01, "over_limit"),
        (5.5, "over_limit"),
        (-1, "allow"),
        (True, "over_limit"),
        ("5", "over_limit"),
        (None, "over_limit"),
        (float("nan"), "over_limit"),
        (float("inf"), "over_limit"),
    ],
)
def test_parameters_are_read_conservatively(value: object, expected: str) -> None:
    engine = Engine.__new__(Engine)
    engine._sets, engine._ids, engine._entities = Engine._parse(canary.POLICIES, canary.ENTITIES)
    assert (
        engine.decide("a1", "t.low", context_for("attended", [], False, {"v": value})).outcome
        == expected
    )


def test_context_for_rounds_floats_up_and_drops_the_rest() -> None:
    context = context_for(
        "unattended", ["a"], True, {"i": 3, "f": 2.1, "s": "x", "b": True, "n": None, "l": [1]}
    )
    assert context == {
        "mode": "unattended",
        "scopes": ["a"],
        "widened": True,
        "params": {"i": 3, "f": 3},
    }


def _bundle_with(stack: Stack, **sets: str) -> Bundle:
    cedar = stack.signed.bundle.cedar.model_copy(update=sets)
    return stack.signed.bundle.model_copy(update={"cedar": cedar})


def test_a_policy_that_does_not_parse_is_an_engine_error(stack: Stack) -> None:
    with pytest.raises(EngineError, match="does not load"):
        Engine(_bundle_with(stack, allow="permit(;"))


def test_bad_entities_are_an_engine_error(stack: Stack) -> None:
    bundle = stack.signed.bundle.model_copy(update={"entities": [{"uid": "nonsense"}]})
    with pytest.raises(EngineError):
        Engine(bundle)


def test_an_engine_that_gives_wrong_answers_fails_its_canary(
    monkeypatch: pytest.MonkeyPatch, stack: Stack
) -> None:
    """A compromised or broken engine that allows everything is caught before it is used."""
    import cedarpy

    real = cedarpy.is_authorized
    monkeypatch.setattr(cedarpy, "is_authorized", lambda *a, **k: _allow_all(real, *a, **k))
    with pytest.raises(EngineError, match="self-test"):
        _engine(stack)


def _allow_all(
    real: object, request: dict[str, object], policies: object, entities: object, **kwargs: object
) -> object:
    from cedarpy import AuthzResult

    result = real(request, "permit(principal, action, resource);", entities, **kwargs)  # type: ignore[operator]
    assert isinstance(result, AuthzResult)
    return result


def test_an_engine_that_raises_fails_its_canary(
    monkeypatch: pytest.MonkeyPatch, stack: Stack
) -> None:
    import cedarpy

    def boom(*args: object, **kwargs: object) -> None:
        raise RuntimeError("native crash")

    monkeypatch.setattr(cedarpy, "is_authorized", boom)
    with pytest.raises(EngineError, match="self-test"):
        _engine(stack)


def test_no_decision_is_an_engine_error_not_an_answer(
    monkeypatch: pytest.MonkeyPatch, stack: Stack
) -> None:
    import cedarpy

    engine = _engine(stack)
    real = cedarpy.is_authorized

    class _NoDecision:
        decision = cedarpy.Decision.NoDecision

    monkeypatch.setattr(cedarpy, "is_authorized", lambda *a, **k: _NoDecision())
    with pytest.raises(EngineError, match="neither Allow nor Deny"):
        engine.decide("acme.research", "broker.quote", _ctx())
    monkeypatch.setattr(cedarpy, "is_authorized", real)
    assert engine.decide("acme.research", "broker.quote", _ctx()).outcome == "allow"


def test_the_canary_covers_every_outcome() -> None:
    assert {case[3] for case in canary.CASES} == {"allow", "over_limit", "ask", "deny", "none"}
    assert copy.deepcopy(canary.CASES) == canary.CASES
