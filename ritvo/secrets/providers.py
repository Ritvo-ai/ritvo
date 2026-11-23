"""The secret providers: keyring, env_file and memory (docs/SPEC.md section 4.10)."""

import contextlib
import json
import logging
import os
import re
from pathlib import Path

import keyring
import keyring.errors

from ritvo.secrets.base import SecretNotFound, SecretUnavailable, check_path

SERVICE = "ritvo"
log = logging.getLogger("ritvo.secrets")


class KeyringProvider:
    """Values in the operating system's keyring, under the service name `ritvo`."""

    def get(self, path: str) -> str:
        check_path(path)
        try:
            value = keyring.get_password(SERVICE, path)
        except keyring.errors.KeyringError:
            raise SecretUnavailable("the keyring could not be read") from None
        if value is None:
            raise SecretNotFound("the secret is not set")
        return value

    def set(self, path: str, value: str) -> None:
        check_path(path)
        try:
            keyring.set_password(SERVICE, path, value)
        except keyring.errors.KeyringError:
            raise SecretUnavailable("the keyring could not be written") from None

    def delete(self, path: str) -> None:
        check_path(path)
        try:
            keyring.delete_password(SERVICE, path)
        except keyring.errors.PasswordDeleteError:
            return
        except keyring.errors.KeyringError:
            raise SecretUnavailable("the keyring could not be written") from None

    def describe(self) -> str:
        return "keyring (service ritvo)"


class MemoryProvider:
    """Values in the process. For development and tests only."""

    INSECURE = True

    def __init__(self) -> None:
        self._values: dict[str, str] = {}
        log.warning("INSECURE: the in-memory secret provider is for development and tests only")

    def get(self, path: str) -> str:
        check_path(path)
        try:
            return self._values[path]
        except KeyError:
            raise SecretNotFound("the secret is not set") from None

    def set(self, path: str, value: str) -> None:
        check_path(path)
        self._values[path] = value

    def delete(self, path: str) -> None:
        check_path(path)
        self._values.pop(path, None)

    def describe(self) -> str:
        return "memory (development and tests only, not secure)"


def env_name(path: str) -> str:
    """`corp-idp/jwks` is `CORP_IDP__JWKS`."""
    return "__".join(re.sub(r"[^A-Z0-9]+", "_", segment.upper()) for segment in path.split("/"))


class EnvFileProvider:
    """Values in a dotenv-style file of `NAME=value` lines. For development only."""

    INSECURE = True

    def __init__(self, file: Path) -> None:
        self._file = file
        log.warning("INSECURE: the env_file secret provider is for development only")

    def _read(self) -> dict[str, str]:
        try:
            text = self._file.read_text(encoding="utf-8")
        except FileNotFoundError:
            return {}
        except (OSError, UnicodeDecodeError):
            raise SecretUnavailable("the secrets file could not be read") from None
        values: dict[str, str] = {}
        for line in text.splitlines():
            if not line.strip() or line.lstrip().startswith("#") or "=" not in line:
                continue
            name, raw = line.split("=", 1)
            raw = raw.strip()
            if raw.startswith('"'):
                with contextlib.suppress(ValueError):
                    raw = json.loads(raw)
            values[name.strip()] = raw
        return values

    def _write(self, values: dict[str, str]) -> None:
        lines = [
            f"{name}={json.dumps(value, ensure_ascii=False)}" for name, value in values.items()
        ]
        try:
            self._file.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
            self._file.chmod(0o600)
        except OSError:
            raise SecretUnavailable("the secrets file could not be written") from None

    def get(self, path: str) -> str:
        check_path(path)
        values = self._read()
        try:
            return values[env_name(path)]
        except KeyError:
            raise SecretNotFound("the secret is not set") from None

    def set(self, path: str, value: str) -> None:
        check_path(path)
        values = self._read()
        values[env_name(path)] = value
        self._write(values)

    def delete(self, path: str) -> None:
        check_path(path)
        values = self._read()
        if values.pop(env_name(path), None) is not None:
            self._write(values)

    def describe(self) -> str:
        return "env_file (development only, not secure)"


def from_backend(
    backend: str, env_file: Path | None = None
) -> KeyringProvider | MemoryProvider | EnvFileProvider:
    """The provider named by `secrets.backend` in ritvo.yaml."""
    if backend == "keyring":
        return KeyringProvider()
    if backend == "memory":
        return MemoryProvider()
    if backend == "env_file":
        return EnvFileProvider(
            env_file if env_file is not None else Path(os.environ.get("RITVO_ENV_FILE", ".env"))
        )
    raise SecretUnavailable("unknown secrets backend")
