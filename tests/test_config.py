from pathlib import Path
from typing import Any

import pytest
import yaml

from ritvo.config.load import ConfigError, _plain_secrets, load_config

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / "examples" / "trading-demo" / "ritvo.yaml"


def _data() -> dict[str, Any]:
    data: dict[str, Any] = yaml.safe_load(EXAMPLE.read_text(encoding="utf-8"))
    return data


def _load(tmp_path: Path, data: dict[str, Any], draft: bool = False) -> Any:
    path = tmp_path / "ritvo.yaml"
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    return load_config(path, draft=draft)


def _problems(tmp_path: Path, data: dict[str, Any], draft: bool = False) -> list[str]:
    with pytest.raises(ConfigError) as error:
        _load(tmp_path, data, draft)
    return error.value.problems


def test_the_example_loads() -> None:
    config = load_config(EXAMPLE)
    assert config.mode == "observe"
    assert config.ledger.path == "ritvo-ledger.db"
    assert config.role_holders() == {
        "policy_author": ["alice"],
        "escalation_reviewer": ["bob"],
    }
    assert config.roles is not None and config.roles.cool_off_minutes == 60
    tool = config.tools[0]
    assert tool.approvals is not None and len(tool.approvals.bands) == 3


def test_roles_take_a_list_or_a_single_id(tmp_path: Path) -> None:
    data = _data()
    data["roles"]["finance"] = ["carol", "dan"]
    assert _load(tmp_path, data).role_holders()["finance"] == ["carol", "dan"]


def test_a_complete_file_needs_its_sections(tmp_path: Path) -> None:
    problems = _problems(tmp_path, {"mode": "observe"})
    for name in ("profile", "identity", "secrets", "roles", "ladders", "enforcement"):
        assert any(problem.startswith(name) for problem in problems)


def test_a_draft_may_leave_things_out_and_have_null_tiers(tmp_path: Path) -> None:
    draft = {
        "mode": "observe",
        "agents": [{"id": "a", "owner": None, "owner_team": None, "mode": "attended"}],
        "tools": [{"id": "t", "tier": None}],
        "bindings": [{"agent": "a", "tool": "t", "decision": "ask", "owner_team": None}],
        "roles": {"policy_author": ["alice"], "cool_off_minutes": 60},
    }
    assert _load(tmp_path, draft, draft=True).tools[0].tier is None
    assert any(
        "tier" in problem
        for problem in _problems(
            tmp_path,
            {
                **_data(),
                "tools": draft["tools"],
                "agents": draft["agents"],
                "bindings": draft["bindings"],
            },
        )
    )


def test_enforce_mode_needs_an_owner_for_every_agent(tmp_path: Path) -> None:
    data = _data()
    data["mode"] = "enforce"
    data["agents"].append({"id": "orphan"})
    assert any("agents.1.owner" in problem for problem in _problems(tmp_path, data))
    data["mode"] = "observe"
    _load(tmp_path, data)


@pytest.mark.parametrize(
    ("change", "message"),
    [
        (lambda d: d.update(mode="strict"), "mode"),
        (lambda d: d.update(surprise=1), "surprise"),
        (lambda d: d["agents"].append(d["agents"][0]), "repeated ID"),
        (lambda d: d["tools"][0].update(tier="critical"), "tier"),
        (lambda d: d["tools"][0].update(approval_ttl_minutes=0), "approval_ttl_minutes"),
        (lambda d: d["bindings"][0].update(agent="ghost"), "is not an agent"),
        (lambda d: d["bindings"][0].update(tool="ghost"), "is not a tool"),
        (lambda d: d["bindings"][0].update(decision="maybe"), "decision"),
        (lambda d: d["bindings"][0].update(limits={"x": "lots"}), "limits"),
        (lambda d: d["enforcement"][0].update(type="proxy"), "type"),
        (lambda d: d["enforcement"][0].update(boundary="cloud"), "boundary"),
        (lambda d: d["roles"].update(cool_off_minutes=0), "cool_off_minutes"),
        (lambda d: d["ladders"].update(timeout_minutes=0), "timeout_minutes"),
        (lambda d: d["ladders"].update(urgent=["x"]), "urgent"),
        (lambda d: d["identity"].update(unattended="sometimes"), "unattended"),
        (lambda d: d["secrets"].update(backend="vault_kv"), "backend"),
        (lambda d: d["agents"][0].update(owner="Alice Smith"), "agents.0.owner"),
    ],
)
def test_invalid_files(tmp_path: Path, change: Any, message: str) -> None:
    data = _data()
    change(data)
    assert any(message in problem for problem in _problems(tmp_path, data))


def test_approval_bands(tmp_path: Path) -> None:
    for bands in (
        [{"ladder": ["a"]}, {"up_to": 5, "ladder": ["b"]}],
        [{"up_to": 5, "ladder": ["a"]}, {"up_to": 5, "ladder": ["b"]}],
        [{"up_to": 9, "ladder": ["a"]}, {"up_to": 5, "ladder": ["b"]}],
        [],
    ):
        data = _data()
        data["tools"][0]["approvals"]["bands"] = bands
        assert _problems(tmp_path, data)


def test_issuers_need_a_claims_map_from_somewhere(tmp_path: Path) -> None:
    data = _data()
    del data["identity"]["claims_map"]
    assert any("claims_map" in problem for problem in _problems(tmp_path, data))
    data["identity"]["issuers"][0]["claims_map"] = {
        "subject": "sub",
        "actor": "act.sub",
        "scopes": "scope",
    }
    assert _load(tmp_path, data).identity.issuers[0].claims_map.actor == "act.sub"


@pytest.mark.parametrize("path", ["act..sub", ".sub", "sub.", "a b"])
def test_claims_paths_are_dotted_names(tmp_path: Path, path: str) -> None:
    data = _data()
    data["identity"]["claims_map"]["actor"] = path
    assert _problems(tmp_path, data)


def test_a_jwks_url_is_a_secret_ref_or_https(tmp_path: Path) -> None:
    data = _data()
    data["identity"]["issuers"][0]["jwks_url"] = "http://idp.example.com/keys"
    assert any("jwks_url" in problem for problem in _problems(tmp_path, data))
    data["identity"]["issuers"][0]["jwks_url"] = "https://idp.example.com/keys"
    _load(tmp_path, data)


def test_a_secret_written_as_a_value_is_refused_without_echoing_it(tmp_path: Path) -> None:
    data = _data()
    data["identity"]["issuers"][0]["client_secret"] = "plain-value-123"
    data["secrets"]["api_key"] = "another-plain-value"
    problems = _problems(tmp_path, data)
    assert any("identity.issuers.0.client_secret" in problem for problem in problems)
    assert any("secrets.api_key" in problem for problem in problems)
    joined = " ".join(problems)
    assert "plain-value" not in joined and "another-plain" not in joined


def test_secret_references_and_refs_are_not_values() -> None:
    problems: list[str] = []
    _plain_secrets(
        {
            "client_secret": "secret://corp-idp/client",
            "token_ref": "sha256:ab",
            "approval_token": "",
            "nested": [{"password": "secret://a/b"}],
        },
        "",
        problems,
    )
    assert problems == []
    _plain_secrets({"nested": [{"password": "hunter2"}]}, "", problems)
    assert problems == [
        "nested.0.password: a secret is written as a secret:// reference, not a value"
    ]


def test_unreadable_files(tmp_path: Path) -> None:
    for content in (b"\xff\xfe", b"a: [1", b"- a\n- b\n", b""):
        path = tmp_path / "bad.yaml"
        path.write_bytes(content)
        with pytest.raises(ConfigError):
            load_config(path)
