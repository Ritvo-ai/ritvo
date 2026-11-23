import json
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from ritvo.cli.main import app
from ritvo.cli.onboarding import OnboardingError, apply_config, check_config, init_config
from ritvo.ledger.store import Ledger
from ritvo.secrets.providers import EnvFileProvider

runner = CliRunner()
ROOT = Path(__file__).resolve().parents[1]
JWKS = "file:///keys/jwks.json"


@pytest.fixture
def work(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A folder with a draft ritvo.yaml and an env_file secrets store (development only)."""
    monkeypatch.setenv("RITVO_ENV_FILE", str(tmp_path / "secrets.env"))
    out = tmp_path / "q"
    questionnaire = ROOT / "examples" / "init" / "questionnaire.yaml"
    out.mkdir()
    (out / "questionnaire.yaml").write_text(
        questionnaire.read_text(encoding="utf-8"), encoding="utf-8"
    )
    result = runner.invoke(app, ["init", "--questionnaire", "--out", str(out), "--no-input"])
    assert result.exit_code == 0, result.output
    return out / "ritvo.draft.yaml"


def _init(work: Path, *extra: str) -> object:
    out = work.parent / "ritvo.yaml"
    return runner.invoke(
        app,
        [
            "init",
            "--draft",
            str(work),
            "--issuer",
            "https://idp.example.com",
            "--audience",
            "demo-mcp",
            "--jwks-url",
            JWKS,
            "--secrets",
            "env_file",
            "--out",
            str(out),
            *extra,
        ],
    )


def _secrets(work: Path) -> Path:
    return work.parent.parent / "secrets.env"


def _config(work: Path) -> Path:
    return work.parent / "ritvo.yaml"


# init


def test_init_completes_a_draft_and_makes_the_key(work: Path) -> None:
    result = _init(work)
    assert result.exit_code == 0, result.output  # type: ignore[attr-defined]
    assert "Made the bundle signing key, key ID gos-" in result.output  # type: ignore[attr-defined]
    data = yaml.safe_load(_config(work).read_text(encoding="utf-8"))
    assert (data["mode"], data["profile"]) == ("observe", "local")
    assert data["enforcement"] == [
        {"id": "mcp-server", "type": "mcp_middleware", "boundary": "mcp"}
    ]
    assert data["identity"]["issuers"][0] == {
        "name": "idp",
        "issuer": "https://idp.example.com",
        "jwks_url": JWKS,
        "audience": "demo-mcp",
    }
    assert data["identity"]["claims_map"] == {
        "subject": "sub",
        "actor": "act.sub",
        "scopes": "scope",
    }
    assert data["secrets"] == {"backend": "env_file"} and data["ladders"]["timeout_minutes"] == 30
    assert data["agents"] and data["tools"] and data["bindings"] and data["roles"]


def test_init_keeps_what_the_draft_has_and_does_not_remake_the_key(work: Path) -> None:
    _init(work)
    key = _secrets(work).read_text(encoding="utf-8")
    _config(work).unlink()
    again = _init(work)
    assert "Found the bundle signing key" in again.output  # type: ignore[attr-defined]
    assert _secrets(work).read_text(encoding="utf-8") == key
    assert "BUNDLE" not in again.output  # type: ignore[attr-defined]


def test_the_key_is_never_printed(work: Path) -> None:
    result = _init(work)
    seed = next(iter(_secrets(work).read_text(encoding="utf-8").split("=", 1)[1:]))
    assert seed.strip().strip('"') not in result.output  # type: ignore[attr-defined]


def test_init_never_writes_over_a_file(work: Path) -> None:
    _init(work)
    before = _config(work).read_bytes()
    again = _init(work)
    assert again.exit_code == 1 and _config(work).read_bytes() == before  # type: ignore[attr-defined]


def test_a_draft_that_cannot_be_completed_writes_nothing_and_makes_no_key(work: Path) -> None:
    data = yaml.safe_load(work.read_text(encoding="utf-8"))
    data["tools"][0]["tier"] = None
    work.write_text(yaml.safe_dump(data), encoding="utf-8")
    result = _init(work)
    assert result.exit_code == 1 and "tier" in result.output  # type: ignore[attr-defined]
    assert not _config(work).exists() and not _secrets(work).exists()


def test_init_needs_its_options() -> None:
    assert runner.invoke(app, ["init", "--draft", __file__]).exit_code == 1
    assert runner.invoke(app, ["init"]).exit_code == 1


def test_init_refuses_a_draft_that_is_not_yaml(tmp_path: Path) -> None:
    bad = tmp_path / "bad.yaml"
    bad.write_text("a: [1", encoding="utf-8")
    with pytest.raises(OnboardingError):
        init_config(bad, tmp_path / "out.yaml", "i", "a", "https://x/k")


# apply


def test_apply_writes_a_bundle_keys_and_a_ledger_entry(work: Path) -> None:
    _init(work)
    result = runner.invoke(app, ["apply", "--config", str(_config(work))])
    assert result.exit_code == 0, result.output
    assert (
        "Applied policy version 1, signed by gos-" in result.output
        and "no sign-off flow" in result.output
    )
    base = work.parent
    bundle = json.loads((base / "ritvo-bundle.json").read_text(encoding="utf-8"))
    assert bundle["bundle"]["policy_version"] == 1 and bundle["signature"]["alg"] == "EdDSA"
    keys = json.loads((base / "ritvo-keys.json").read_text(encoding="utf-8"))["keys"]
    assert [k["kid"] for k in keys] == [bundle["signature"]["key_id"]]
    ledger = Ledger(base / "ritvo-ledger.db")
    entry = ledger.entries()[0].entry
    assert (entry.type, entry.policy_version, entry.outcome, entry.approver) == (
        "policy_change",
        1,
        "applied",
        None,
    )
    assert ledger.verify().ok


def test_each_apply_is_one_version_higher_and_keeps_the_keys(work: Path) -> None:
    _init(work)
    first = apply_config(_config(work))
    second = apply_config(_config(work))
    assert (first.version, second.version) == (1, 2)
    keys = json.loads(second.keys_path.read_text(encoding="utf-8"))["keys"]
    assert len(keys) == 1
    assert len(Ledger(work.parent / "ritvo-ledger.db").entries()) == 2


def test_a_new_signing_key_is_added_and_old_bundles_still_verify(work: Path) -> None:
    from ritvo.adapter_contract.bundle import new_seed

    _init(work)
    apply_config(_config(work))
    _secrets(work).write_text("", encoding="utf-8")

    EnvFileProvider(_secrets(work)).set("ritvo/bundle-signing-key", new_seed())
    done = apply_config(_config(work))
    assert done.version == 2  # the old bundle still verifies with the old key, so the count goes on
    keys = json.loads(done.keys_path.read_text(encoding="utf-8"))["keys"]
    assert len(keys) == 2


def test_apply_needs_a_signing_key_and_a_complete_config(work: Path) -> None:
    _init(work)
    _secrets(work).write_text("", encoding="utf-8")
    with pytest.raises(OnboardingError, match="ritvo init"):
        apply_config(_config(work))
    assert not (work.parent / "ritvo-bundle.json").exists()
    result = runner.invoke(app, ["apply", "--config", str(work)])  # the draft is not complete
    assert result.exit_code == 1 and "Nothing was written" in result.output


# check


def _check(work: Path) -> dict[str, str]:
    return {c.name: c.status for c in check_config(_config(work))}


def test_a_good_setup_checks_out(work: Path) -> None:
    _init(work)
    apply_config(_config(work))
    result = runner.invoke(app, ["check", "--config", str(_config(work))])
    assert result.exit_code == 0, result.output
    statuses = _check(work)
    assert statuses["config"] == "ok" and statuses["bundle"] == "ok" and statuses["engine"] == "ok"
    assert statuses["policy"] == "ok" and statuses["ledger"] == "ok" and statuses["keys"] == "ok"
    assert statuses["secrets"] == "warn"  # env_file is for development only
    assert "FAIL" not in result.output


def test_check_never_prints_a_secret(work: Path) -> None:
    _init(work)
    apply_config(_config(work))
    seed = _secrets(work).read_text(encoding="utf-8").split("=", 1)[1].strip().strip('"')
    result = runner.invoke(app, ["check", "--config", str(_config(work))])
    assert seed not in result.output


def test_a_changed_config_is_a_warning(work: Path) -> None:
    _init(work)
    apply_config(_config(work))
    data = yaml.safe_load(_config(work).read_text(encoding="utf-8"))
    data["bindings"][0]["decision"] = "deny"
    data["bindings"][0].pop("limits", None)
    _config(work).write_text(yaml.safe_dump(data), encoding="utf-8")
    assert _check(work)["policy"] == "warn"


def test_a_missing_or_tampered_bundle_fails(work: Path) -> None:
    _init(work)
    apply_config(_config(work))
    bundle = work.parent / "ritvo-bundle.json"
    text = bundle.read_text(encoding="utf-8")
    bundle.write_text(text.replace('"allow"', '"deny"', 1), encoding="utf-8")
    assert _check(work)["bundle"] == "FAIL"
    assert runner.invoke(app, ["check", "--config", str(_config(work))]).exit_code == 1
    bundle.unlink()
    assert _check(work)["bundle"] == "FAIL"


def test_no_keys_fails(work: Path) -> None:
    _init(work)
    apply_config(_config(work))
    (work.parent / "ritvo-keys.json").unlink()
    statuses = _check(work)
    assert statuses["keys"] == "FAIL" and statuses["bundle"] == "FAIL"


def test_a_tampered_ledger_fails(work: Path) -> None:
    import sqlite3

    _init(work)
    apply_config(_config(work))
    db = sqlite3.connect(work.parent / "ritvo-ledger.db", isolation_level=None)
    db.execute("DROP TRIGGER ledger_no_update")
    db.execute("UPDATE ledger SET entry = replace(entry, 'applied', 'forged') WHERE seq = 1")
    db.close()
    assert _check(work)["ledger"] == "FAIL"


def test_a_secret_reference_that_cannot_be_read_fails(work: Path) -> None:
    _init(work)
    apply_config(_config(work))
    data = yaml.safe_load(_config(work).read_text(encoding="utf-8"))
    data["identity"]["issuers"][0]["jwks_url"] = "secret://corp-idp/jwks"
    _config(work).write_text(yaml.safe_dump(data), encoding="utf-8")
    assert {c.name: c.status for c in check_config(_config(work))}["secret"] == "FAIL"


def test_an_invalid_config_fails_with_every_problem(work: Path) -> None:
    result = runner.invoke(app, ["check", "--config", str(work)])
    assert result.exit_code == 1 and result.output.count("FAIL") >= 1
    assert runner.invoke(app, ["check", "--config", str(work.parent / "none.yaml")]).exit_code == 1


def test_stale_bundles_warn(work: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import time

    _init(work)
    apply_config(_config(work))
    monkeypatch.setattr(time, "time", lambda: time.monotonic() * 0 + 4_000_000_000)
    assert _check(work)["bundle age"] == "warn"
