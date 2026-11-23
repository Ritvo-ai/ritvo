import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
import yaml
from conftest import EXAMPLE, NOW, Stack, make_stack

from ritvo.adapter_contract.bundle import (
    Bundle,
    BundleError,
    SignedBundle,
    encode_signed_bundle,
    jwks_of,
    new_seed,
    private_key,
    read_signed_bundle,
    sign_bundle,
    trusted_keys,
    verify_bundle,
)
from ritvo.config.load import load_config
from ritvo.enforcement.bundles.store import BundleStore
from ritvo.governance_os.routing.compile import CompileError, compile_bundle
from ritvo.governance_os.routing.publish import publish_bundle
from ritvo.secrets.access import Secrets
from ritvo.secrets.base import SecretNotFound
from ritvo.secrets.providers import MemoryProvider


def _trusted(stack: Stack) -> dict[str, object]:
    return dict(trusted_keys(jwks_of(private_key(stack.seed).public_key())))


# Compile


def test_the_compile_follows_the_spec(stack: Stack) -> None:
    bundle = stack.signed.bundle
    assert bundle.policy_version == 1
    assert [b.id for b in bundle.bindings] == [f"b-{n}" for n in range(1, len(bundle.bindings) + 1)]
    assert [(r.id, r.enabled) for r in bundle.ask_trigger_rules] == [
        ("tier", True),
        ("novelty", False),
    ]
    assert bundle.fail_modes.model_dump() == {"low": "closed", "medium": "closed", "high": "closed"}
    assert 'Agent::"acme.research"' in bundle.cedar.ask
    assert '@id("b-1")' in bundle.cedar.ask
    assert "context.params.max" not in bundle.cedar.allow
    assert "context.params.depth <= 10" in bundle.cedar.allow
    assert "unless" in bundle.cedar.over_limit
    assert bundle.cedar.deny == ""


def test_limits_become_conditions_on_allow_and_ask_and_an_over_limit_policy(tmp_path: Path) -> None:
    config = load_config(EXAMPLE)
    bundle = compile_bundle(config, 7, datetime.fromtimestamp(NOW, UTC))
    assert (
        "context.params has order_value && context.params.order_value <= 50000" in bundle.cedar.ask
    )
    assert "unless" in bundle.cedar.over_limit and "50000" in bundle.cedar.over_limit
    assert bundle.issued_at.endswith("Z")


def test_entities_hold_agents_teams_and_tiers(stack: Stack) -> None:
    entities = {(e["uid"]["type"], e["uid"]["id"]): e for e in stack.signed.bundle.entities}
    assert entities[("Agent", "acme.research")]["parents"] == [{"type": "Team", "id": "research"}]
    assert ("Team", "research") in entities
    assert entities[("Tool", "broker.order")]["attrs"] == {"tier": "high"}


def _config(tmp_path: Path, change: object) -> object:
    data = yaml.safe_load(EXAMPLE.read_text(encoding="utf-8"))
    change(data)  # type: ignore[operator]
    path = tmp_path / "c.yaml"
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    return load_config(path)


def test_fail_open_low_is_only_on_when_configured(tmp_path: Path) -> None:
    config = _config(tmp_path, lambda d: d.update(fail_open_low=True))
    assert compile_bundle(config, 1).fail_modes.low == "open"  # type: ignore[arg-type]
    assert compile_bundle(config, 1).fail_modes.high == "closed"  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("change", "message"),
    [
        (lambda d: d["bindings"][0].update(limits={"order_value": 5}), "max_<parameter>"),
        (lambda d: d["bindings"][0].update(limits={"max_Order": 5}), "max_<parameter>"),
        (lambda d: d["bindings"][0].update(decision="deny"), "no limits"),
    ],
)
def test_bad_limits_stop_the_compile(tmp_path: Path, change: object, message: str) -> None:
    config = _config(tmp_path, change)
    with pytest.raises(CompileError, match=message):
        compile_bundle(config, 1)  # type: ignore[arg-type]


def test_a_draft_with_a_null_tier_does_not_compile(tmp_path: Path) -> None:
    path = tmp_path / "d.yaml"
    path.write_text(yaml.safe_dump({"tools": [{"id": "t", "tier": None}]}), encoding="utf-8")
    with pytest.raises(CompileError, match="tier"):
        compile_bundle(load_config(path, draft=True), 1)


def test_publishing_needs_the_signing_key_in_the_store(stack: Stack) -> None:
    with pytest.raises(SecretNotFound):
        publish_bundle(stack.config, Secrets(MemoryProvider()), 2)


# Signature


def test_a_signed_bundle_verifies(stack: Stack) -> None:
    assert verify_bundle(stack.signed, _trusted(stack)) == stack.signed.bundle  # type: ignore[arg-type]


def test_any_change_to_the_bundle_breaks_the_signature(stack: Stack) -> None:
    data = json.loads(encode_signed_bundle(stack.signed))
    data["bundle"]["bindings"][0]["decision"] = "allow"
    forged = SignedBundle.model_validate(data)
    with pytest.raises(BundleError, match="signature"):
        verify_bundle(forged, _trusted(stack))  # type: ignore[arg-type]


def test_a_key_that_is_not_trusted_is_refused(stack: Stack) -> None:
    other = sign_bundle(stack.signed.bundle, new_seed())
    with pytest.raises(BundleError, match="not trusted"):
        verify_bundle(other, _trusted(stack))  # type: ignore[arg-type]


def test_a_known_key_id_with_another_key_is_refused(stack: Stack) -> None:
    forged = sign_bundle(stack.signed.bundle, new_seed())
    forged = SignedBundle(
        bundle=forged.bundle,
        signature=forged.signature.model_copy(update={"key_id": stack.signed.signature.key_id}),
    )
    with pytest.raises(BundleError, match="does not verify"):
        verify_bundle(forged, _trusted(stack))  # type: ignore[arg-type]


def test_the_signing_key_must_be_a_seed() -> None:
    with pytest.raises(BundleError, match="seed"):
        private_key("not-a-key")


def test_a_bundle_round_trips_as_canonical_bytes(stack: Stack) -> None:
    data = encode_signed_bundle(stack.signed)
    assert read_signed_bundle(data) == stack.signed
    with pytest.raises(BundleError):
        read_signed_bundle(b"{}")


# The store


def test_the_store_refuses_an_older_bundle(stack: Stack) -> None:
    store = stack.store(
        publish_bundle(stack.config, stack.secrets, 5, datetime.fromtimestamp(NOW, UTC))
    )
    assert store.bundle is not None and store.bundle.policy_version == 5
    with pytest.raises(BundleError, match="older"):
        store.install(stack.signed)
    assert store.bundle.policy_version == 5


def test_the_same_version_must_be_the_same_bundle(stack: Stack) -> None:
    store = stack.store()
    store.install(stack.signed)
    changed = stack.signed.bundle.model_copy(update={"max_age_minutes": 5})
    with pytest.raises(BundleError, match="same policy_version"):
        store.install(sign_bundle(changed, stack.seed))


def test_the_store_keeps_the_bundle_and_starts_from_it(stack: Stack, tmp_path: Path) -> None:
    path = tmp_path / "bundle.json"
    keys = trusted_keys(jwks_of(private_key(stack.seed).public_key()))
    BundleStore(keys, path).install(stack.signed)
    assert BundleStore(keys, path).bundle == stack.signed.bundle
    path.write_text(path.read_text().replace('"deny"', '"allow"', 1), encoding="utf-8")
    assert BundleStore(keys, path).bundle is None
    assert BundleStore({}, path).bundle is None


def test_staleness(stack: Stack) -> None:
    store = stack.store()
    assert not store.is_stale()
    stack.clock[0] = NOW + 60 * 60 + 1
    assert store.is_stale()
    assert BundleStore({}).is_stale()


def test_make_stack_is_repeatable(tmp_path: Path) -> None:
    assert make_stack(tmp_path).signed.bundle.cedar == make_stack(tmp_path).signed.bundle.cedar
    assert isinstance(make_stack(tmp_path).signed.bundle, Bundle)
