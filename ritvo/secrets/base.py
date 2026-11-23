"""The secrets interface and `secret://` references (docs/SPEC.md section 4.10)."""

import re
from typing import Protocol

PREFIX = "secret://"
_SEGMENT = re.compile(r"[a-z0-9._-]{1,64}")


class SecretError(Exception):
    """Base of every secrets failure. A caller treats any of them as a failure to read."""


class SecretNotFound(SecretError):
    """The path is not set."""


class SecretUnavailable(SecretError):
    """The provider could not be reached or read. The high tier fails closed."""


def parse_ref(ref: str) -> str:
    """The path of a `secret://<path>` reference, or an error that does not echo the value."""
    if not ref.startswith(PREFIX):
        raise SecretError("a secret reference starts with secret://")
    return check_path(ref[len(PREFIX) :])


def check_path(path: str) -> str:
    segments = path.split("/")
    for segment in segments:
        if segment in (".", "..") or not _SEGMENT.fullmatch(segment):
            raise SecretError(
                "a secret path is segments of 1 to 64 characters from a-z, 0-9, . _ -, "
                "separated by /"
            )
    return path


class SecretProvider(Protocol):
    """Where secret values live."""

    def get(self, path: str) -> str: ...

    def set(self, path: str, value: str) -> None: ...

    def delete(self, path: str) -> None: ...

    def describe(self) -> str: ...
