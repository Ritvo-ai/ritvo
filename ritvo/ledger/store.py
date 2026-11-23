"""The ledger store: append, verify and export (docs/SPEC.md section 4.7)."""

import json
import sqlite3
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

from ritvo.adapter_contract.canonical import canonical, sha256_hex
from ritvo.ledger.event import LedgerEntry, LedgerEvent
from ritvo.ledger.redact import REDACTOR, Redactor

GENESIS = "0" * 64
DEFAULT_PATH = Path("ritvo-ledger.db")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS ledger (
  seq INTEGER PRIMARY KEY AUTOINCREMENT,
  event_id TEXT NOT NULL UNIQUE,
  entry TEXT NOT NULL
);
CREATE TRIGGER IF NOT EXISTS ledger_no_update BEFORE UPDATE ON ledger
BEGIN SELECT RAISE(ABORT, 'the ledger is append-only'); END;
CREATE TRIGGER IF NOT EXISTS ledger_no_delete BEFORE DELETE ON ledger
BEGIN SELECT RAISE(ABORT, 'the ledger is append-only'); END;
"""


class LedgerError(Exception):
    """The ledger cannot be opened or written."""


@dataclass(frozen=True)
class Entry:
    seq: int
    entry: LedgerEntry


@dataclass(frozen=True)
class VerifyResult:
    ok: bool
    entries: int
    seq: int | None = None
    problem: str | None = None


def entry_hash(prev_hash: str, body: dict[str, object]) -> str:
    """SHA-256 of the previous hash as ASCII and the canonical JSON of the entry without `hash`."""
    return sha256_hex(prev_hash.encode("ascii") + canonical(body))


class Ledger:
    def __init__(self, path: Path = DEFAULT_PATH, redactor: Redactor = REDACTOR) -> None:
        self._redactor = redactor
        try:
            self._db = sqlite3.connect(path, isolation_level=None)
            self._db.executescript(_SCHEMA)
        except sqlite3.Error as exc:
            raise LedgerError(f"cannot open the ledger {path.name}: {exc}") from None

    def close(self) -> None:
        self._db.close()

    def append(self, event: LedgerEvent) -> Entry:
        """Redact, chain and store an event. An `event_id` already stored writes nothing."""
        body = self._redactor.value(event.model_dump(mode="json"))
        self._db.execute("BEGIN IMMEDIATE")
        try:
            row = self._db.execute(
                "SELECT seq, entry FROM ledger WHERE event_id = ?", (body["event_id"],)
            ).fetchone()
            if row is not None:
                self._db.execute("COMMIT")
                return Entry(row[0], LedgerEntry.model_validate_json(row[1]))
            last = self._db.execute("SELECT entry FROM ledger ORDER BY seq DESC LIMIT 1").fetchone()
            prev_hash = GENESIS if last is None else json.loads(last[0])["hash"]
            body["prev_hash"] = prev_hash
            body["hash"] = entry_hash(prev_hash, body)
            text = canonical(body).decode("utf-8")
            cursor = self._db.execute(
                "INSERT INTO ledger (event_id, entry) VALUES (?, ?)", (body["event_id"], text)
            )
            self._db.execute("COMMIT")
        except BaseException:
            self._db.execute("ROLLBACK")
            raise
        assert cursor.lastrowid is not None  # noqa: S101 - set by a successful INSERT
        return Entry(cursor.lastrowid, LedgerEntry.model_validate(body))

    def rows(self) -> Iterator[tuple[int, str, str]]:
        yield from self._db.execute("SELECT seq, event_id, entry FROM ledger ORDER BY seq")

    def entries(self) -> list[Entry]:
        return [Entry(seq, LedgerEntry.model_validate_json(text)) for seq, _, text in self.rows()]

    def verify(self) -> VerifyResult:
        """Recompute every hash and check the chain. Reports the first entry that fails."""
        prev_hash = GENESIS
        count = 0
        for seq, event_id, text in self.rows():
            count += 1
            if seq != count:
                return VerifyResult(False, count - 1, count, f"entry {count} is missing")
            try:
                body = json.loads(text)
                stored = body["hash"]
            except (ValueError, KeyError, TypeError):
                return VerifyResult(False, count - 1, seq, "the entry is not readable")
            if not isinstance(body, dict) or body.get("event_id") != event_id:
                return VerifyResult(False, count - 1, seq, "the entry does not match its event_id")
            if body.get("prev_hash") != prev_hash:
                return VerifyResult(
                    False, count - 1, seq, "the entry does not follow the one before"
                )
            without_hash = {key: value for key, value in body.items() if key != "hash"}
            if entry_hash(prev_hash, without_hash) != stored:
                return VerifyResult(False, count - 1, seq, "the hash does not match the entry")
            if canonical(body).decode("utf-8") != text:
                return VerifyResult(False, count - 1, seq, "the entry is not in canonical form")
            prev_hash = stored
        return VerifyResult(True, count)

    def export(self) -> Iterator[str]:
        """Each entry as one line of canonical JSON."""
        for _, _, text in self.rows():
            yield text
