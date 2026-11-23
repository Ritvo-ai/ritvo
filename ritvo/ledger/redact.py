"""Redact secrets and personal data before anything is written (docs/SPEC.md sections 4.7, 4.10)."""

import logging
import re
import threading
from typing import Any

REDACTED = "[redacted]"
EMAIL = "[email]"
MIN_REGISTERED = 4

_SENSITIVE_KEY = re.compile(
    r"token|secret|password|passwd|authorization|api_?key|cookie|private_?key", re.IGNORECASE
)
_JWT = re.compile(r"eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]*")
_BEARER = re.compile(r"Bearer\s+\S+", re.IGNORECASE)
_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")


def sensitive_key(name: str) -> bool:
    """A key whose value is never kept, except a `_ref`, which holds a hash or an identifier."""
    return bool(_SENSITIVE_KEY.search(name)) and not name.lower().endswith("_ref")


class Redactor:
    """Removes registered secret values, tokens and email addresses."""

    def __init__(self) -> None:
        self._values: set[str] = set()
        self._lock = threading.Lock()

    def register(self, value: str) -> None:
        """Remember a secret value so it is removed from text. Short values are not kept."""
        if len(value) >= MIN_REGISTERED:
            with self._lock:
                self._values.add(value)

    def text(self, value: str) -> str:
        with self._lock:
            known = sorted(self._values, key=len, reverse=True)
        for secret in known:
            value = value.replace(secret, REDACTED)
        value = _JWT.sub(REDACTED, value)
        value = _BEARER.sub(REDACTED, value)
        return _EMAIL.sub(EMAIL, value)

    def value(self, item: Any) -> Any:
        """A copy of a JSON value with every string cleaned and every sensitive key blanked."""
        if isinstance(item, str):
            return self.text(item)
        if isinstance(item, dict):
            return {
                key: REDACTED
                if isinstance(key, str) and sensitive_key(key) and item[key] is not None
                else self.value(item[key])
                for key in item
            }
        if isinstance(item, list):
            return [self.value(entry) for entry in item]
        return item


class RedactingFilter(logging.Filter):
    """A logging filter that cleans every message with a redactor."""

    def __init__(self, redactor: Redactor) -> None:
        super().__init__()
        self._redactor = redactor

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = self._redactor.text(record.getMessage())
        record.args = None
        return True


# The redactor that secret access and the ledger share unless a caller passes its own.
REDACTOR = Redactor()
