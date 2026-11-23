"""Read secrets by reference with logged access and redaction (docs/SPEC.md section 4.10)."""

import logging

from ritvo.ledger.redact import REDACTOR, Redactor
from ritvo.secrets.base import SecretError, SecretProvider, parse_ref

log = logging.getLogger("ritvo.secrets")


class Secrets:
    """A provider with access logged by path, never by value. Values read are redacted from logs."""

    def __init__(self, provider: SecretProvider, redactor: Redactor = REDACTOR) -> None:
        self._provider = provider
        self._redactor = redactor

    def get(self, path: str) -> str:
        try:
            value = self._provider.get(path)
        except SecretError:
            log.warning("secret read failed: %s", path)
            raise
        log.info("secret read: %s", path)
        self._redactor.register(value)
        return value

    def resolve(self, ref: str) -> str:
        """The value behind a `secret://<path>` reference."""
        return self.get(parse_ref(ref))

    def set(self, path: str, value: str) -> None:
        self._provider.set(path, value)
        log.info("secret written: %s", path)
        self._redactor.register(value)

    def delete(self, path: str) -> None:
        self._provider.delete(path)
        log.info("secret deleted: %s", path)

    def describe(self) -> str:
        return self._provider.describe()
