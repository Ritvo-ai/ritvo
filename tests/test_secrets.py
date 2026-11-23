import logging
from collections.abc import Callable
from pathlib import Path

import keyring.backends.fail
import pytest

from ritvo.ledger.redact import Redactor
from ritvo.secrets.access import Secrets
from ritvo.secrets.base import (
    SecretError,
    SecretNotFound,
    SecretProvider,
    SecretUnavailable,
    check_path,
    parse_ref,
)
from ritvo.secrets.providers import EnvFileProvider, KeyringProvider, MemoryProvider, env_name

Factory = Callable[[Path], SecretProvider]

PROVIDERS: dict[str, Factory] = {
    "keyring": lambda tmp: KeyringProvider(),
    "memory": lambda tmp: MemoryProvider(),
    "env_file": lambda tmp: EnvFileProvider(tmp / "secrets.env"),
}


@pytest.fixture(params=list(PROVIDERS))
def provider(request: pytest.FixtureRequest, tmp_path: Path) -> SecretProvider:
    return PROVIDERS[request.param](tmp_path)


# One contract that every provider passes


def test_set_then_get(provider: SecretProvider) -> None:
    provider.set("corp-idp/jwks", "https://idp.example.com/keys")
    assert provider.get("corp-idp/jwks") == "https://idp.example.com/keys"


def test_get_of_an_unset_path_is_not_found(provider: SecretProvider) -> None:
    with pytest.raises(SecretNotFound):
        provider.get("never/set")


def test_set_replaces(provider: SecretProvider) -> None:
    provider.set("a/b", "one")
    provider.set("a/b", "two")
    assert provider.get("a/b") == "two"


def test_delete_removes_and_is_idempotent(provider: SecretProvider) -> None:
    provider.set("a/b", "one")
    provider.delete("a/b")
    with pytest.raises(SecretNotFound):
        provider.get("a/b")
    provider.delete("a/b")
    provider.delete("never/set")


def test_values_are_kept_exactly(provider: SecretProvider) -> None:
    for value in ("naïve ☃", "with = sign", 'a "quoted" one', "line\nbreak", " padded ", "#hash"):
        provider.set("odd/value", value)
        assert provider.get("odd/value") == value


def test_paths_are_independent(provider: SecretProvider) -> None:
    provider.set("one", "1")
    provider.set("one/two", "2")
    provider.set("other", "3")
    provider.delete("one")
    assert provider.get("one/two") == "2"
    assert provider.get("other") == "3"


@pytest.mark.parametrize("path", ["", "/a", "a/", "a//b", "a/../b", "./a", "A/b", "a b", "a/é"])
def test_bad_paths_are_refused(provider: SecretProvider, path: str) -> None:
    for call in (
        lambda: provider.get(path),
        lambda: provider.set(path, "x"),
        lambda: provider.delete(path),
    ):
        with pytest.raises(SecretError):
            call()


def test_describe_names_the_provider(provider: SecretProvider) -> None:
    assert provider.describe()


# Provider specifics


def test_the_insecure_providers_say_so(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING, logger="ritvo.secrets"):
        memory = MemoryProvider()
        env = EnvFileProvider(tmp_path / "x.env")
    assert caplog.text.count("INSECURE") == 2
    assert "not secure" in memory.describe() and "not secure" in env.describe()
    assert "secure" not in KeyringProvider().describe().replace("keyring", "")


def test_env_names() -> None:
    assert env_name("corp-idp/jwks") == "CORP_IDP__JWKS"
    assert env_name("a.b/c_d") == "A_B__C_D"


def test_env_file_keeps_other_lines_readable(tmp_path: Path) -> None:
    file = tmp_path / "secrets.env"
    file.write_text('# a comment\n\nPLAIN=value\nQUOTED="two words"\n', encoding="utf-8")
    provider = EnvFileProvider(file)
    assert provider.get("plain") == "value"
    assert provider.get("quoted") == "two words"


def test_env_file_never_reads_the_process_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SOME__SECRET", "from-the-environment")
    with pytest.raises(SecretNotFound):
        EnvFileProvider(tmp_path / "none.env").get("some/secret")


def test_keyring_uses_the_service_name_ritvo(memory_keyring: object) -> None:
    KeyringProvider().set("corp-idp/jwks", "v")
    assert memory_keyring._values == {("ritvo", "corp-idp/jwks"): "v"}  # type: ignore[attr-defined]


def test_a_keyring_that_cannot_be_used_fails_closed() -> None:
    previous = keyring.get_keyring()
    keyring.set_keyring(keyring.backends.fail.Keyring())  # type: ignore[no-untyped-call]
    try:
        provider = KeyringProvider()
        for call in (
            lambda: provider.get("a"),
            lambda: provider.set("a", "b"),
            lambda: provider.delete("a"),
        ):
            with pytest.raises(SecretUnavailable):
                call()
    finally:
        keyring.set_keyring(previous)


# References and logged access


def test_references() -> None:
    assert parse_ref("secret://corp-idp/jwks") == "corp-idp/jwks"
    for bad in ("corp-idp/jwks", "secret://", "secret://a/../b", "https://x"):
        with pytest.raises(SecretError) as error:
            parse_ref(bad)
        assert bad.removeprefix("secret://") not in str(error.value) or bad in ("secret://",)
    assert check_path("a.b/c-d_e") == "a.b/c-d_e"


def test_access_is_logged_by_path_and_the_value_is_registered(
    caplog: pytest.LogCaptureFixture,
) -> None:
    redactor = Redactor()
    secrets = Secrets(MemoryProvider(), redactor)
    secrets.set("api/key", "value-that-must-not-leak")
    with caplog.at_level(logging.INFO, logger="ritvo.secrets"):
        assert secrets.resolve("secret://api/key") == "value-that-must-not-leak"
    assert "secret read: api/key" in caplog.text
    assert "value-that-must-not-leak" not in caplog.text
    assert "value-that-must-not-leak" not in redactor.text("seen value-that-must-not-leak here")


def test_a_failed_read_is_logged_by_path_and_raised(caplog: pytest.LogCaptureFixture) -> None:
    secrets = Secrets(MemoryProvider(), Redactor())
    with caplog.at_level(logging.WARNING, logger="ritvo.secrets"), pytest.raises(SecretNotFound):
        secrets.get("not/set")
    assert "secret read failed: not/set" in caplog.text
