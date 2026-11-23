import json
import logging
import sqlite3
import threading
from pathlib import Path

import pytest
from typer.testing import CliRunner

from ritvo.adapter_contract.canonical import canonical
from ritvo.cli.main import app
from ritvo.ledger.event import LedgerEvent
from ritvo.ledger.redact import REDACTED, RedactingFilter, Redactor, sensitive_key
from ritvo.ledger.store import GENESIS, Ledger, entry_hash

runner = CliRunner()


def _event(n: int, **extra: object) -> LedgerEvent:
    return LedgerEvent.model_validate(
        {"event_id": f"evt-{n}", "timestamp": "2026-11-23T10:00:00Z", "type": "decision", **extra}
    )


def _ledger(tmp_path: Path, count: int = 3) -> Ledger:
    ledger = Ledger(tmp_path / "ledger.db", Redactor())
    for n in range(1, count + 1):
        ledger.append(_event(n, tool="broker.order", decision="allow"))
    return ledger


def _raw(tmp_path: Path) -> sqlite3.Connection:
    return sqlite3.connect(tmp_path / "ledger.db", isolation_level=None)


# The chain


def test_entries_are_chained_from_the_genesis_hash(tmp_path: Path) -> None:
    entries = _ledger(tmp_path).entries()
    assert [entry.seq for entry in entries] == [1, 2, 3]
    assert entries[0].entry.prev_hash == GENESIS
    assert entries[1].entry.prev_hash == entries[0].entry.hash
    body = entries[0].entry.model_dump(mode="json")
    stored_hash = body.pop("hash")
    assert stored_hash == entry_hash(GENESIS, body)
    assert len(stored_hash) == 64


def test_every_field_is_stored_null_where_it_does_not_apply(tmp_path: Path) -> None:
    entry = _ledger(tmp_path, 1).entries()[0].entry.model_dump(mode="json")
    assert entry["principal"] is None
    assert entry["would_decide"] is None
    assert entry["flags"] == []
    assert {"mode", "approver", "latency_ms", "prev_hash", "hash"} <= set(entry)


def test_a_good_ledger_verifies_and_survives_reopening(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path)
    result = ledger.verify()
    assert result.ok and result.entries == 3
    ledger.close()
    again = Ledger(tmp_path / "ledger.db", Redactor())
    again.append(_event(4))
    assert again.verify().entries == 4


def test_an_empty_ledger_verifies(tmp_path: Path) -> None:
    result = Ledger(tmp_path / "ledger.db").verify()
    assert result.ok and result.entries == 0


def test_appending_an_event_id_twice_writes_once(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path, 2)
    again = ledger.append(_event(1, tool="something.else"))
    assert again.seq == 1
    assert again.entry.tool == "broker.order"
    assert len(ledger.entries()) == 2


def test_parallel_writers_never_share_a_prev_hash(tmp_path: Path) -> None:
    path = tmp_path / "ledger.db"
    Ledger(path).close()

    def write(start: int) -> None:
        ledger = Ledger(path, Redactor())
        for n in range(start, start + 10):
            ledger.append(_event(n))
        ledger.close()

    threads = [threading.Thread(target=write, args=(start,)) for start in (0, 100, 200, 300)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    result = Ledger(path).verify()
    assert result.ok and result.entries == 40


# Tampering is evident


def test_update_and_delete_are_blocked_by_triggers(tmp_path: Path) -> None:
    _ledger(tmp_path)
    db = _raw(tmp_path)
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        db.execute("UPDATE ledger SET entry = '{}' WHERE seq = 1")
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        db.execute("DELETE FROM ledger WHERE seq = 1")


def _tamper(tmp_path: Path, sql: str, *args: object) -> None:
    """Change the file with the triggers out of the way, as someone with file access could."""
    db = _raw(tmp_path)
    db.execute("DROP TRIGGER ledger_no_update")
    db.execute("DROP TRIGGER ledger_no_delete")
    db.execute(sql, args)
    db.close()


def test_tampering_with_a_row_fails_audit_verify(tmp_path: Path) -> None:
    """The phase exit test: change one field of one row and verification names that row."""
    ledger = _ledger(tmp_path)
    ledger.close()
    row = json.loads(_raw(tmp_path).execute("SELECT entry FROM ledger WHERE seq = 2").fetchone()[0])
    row["decision"] = "deny"
    _tamper(tmp_path, "UPDATE ledger SET entry = ? WHERE seq = 2", canonical(row).decode())
    result = runner.invoke(app, ["audit", "verify", "--ledger", str(tmp_path / "ledger.db")])
    assert result.exit_code == 1
    assert "FAILED at entry 2" in result.output
    assert "hash does not match" in result.output
    assert "1 entries before it are intact" in result.output


def test_a_rewritten_entry_with_a_fixed_hash_breaks_the_next_one(tmp_path: Path) -> None:
    _ledger(tmp_path).close()
    row = json.loads(_raw(tmp_path).execute("SELECT entry FROM ledger WHERE seq = 1").fetchone()[0])
    row["decision"] = "deny"
    del row["hash"]
    row["hash"] = entry_hash(row["prev_hash"], row)
    _tamper(tmp_path, "UPDATE ledger SET entry = ? WHERE seq = 1", canonical(row).decode())
    result = Ledger(tmp_path / "ledger.db").verify()
    assert not result.ok and result.seq == 2
    assert "does not follow" in str(result.problem)


def test_a_deleted_row_is_a_gap(tmp_path: Path) -> None:
    _ledger(tmp_path).close()
    _tamper(tmp_path, "DELETE FROM ledger WHERE seq = 2")
    result = Ledger(tmp_path / "ledger.db").verify()
    assert not result.ok and result.seq == 2
    assert "missing" in str(result.problem)


def test_an_entry_moved_to_another_event_id_is_found(tmp_path: Path) -> None:
    _ledger(tmp_path).close()
    _tamper(tmp_path, "UPDATE ledger SET event_id = 'evt-other' WHERE seq = 3")
    result = Ledger(tmp_path / "ledger.db").verify()
    assert not result.ok and result.seq == 3
    assert "event_id" in str(result.problem)


def test_an_entry_that_is_not_canonical_is_found(tmp_path: Path) -> None:
    _ledger(tmp_path).close()
    row = _raw(tmp_path).execute("SELECT entry FROM ledger WHERE seq = 1").fetchone()[0]
    _tamper(
        tmp_path, "UPDATE ledger SET entry = ? WHERE seq = 1", json.dumps(json.loads(row), indent=1)
    )
    result = Ledger(tmp_path / "ledger.db").verify()
    assert not result.ok and result.seq == 1
    assert "canonical" in str(result.problem)


def test_garbage_in_a_row_is_found(tmp_path: Path) -> None:
    _ledger(tmp_path).close()
    _tamper(tmp_path, "UPDATE ledger SET entry = 'not json' WHERE seq = 1")
    assert not Ledger(tmp_path / "ledger.db").verify().ok


# Redaction


def test_sensitive_keys_lose_their_values_but_refs_stay() -> None:
    redactor = Redactor()
    cleaned = redactor.value(
        {
            "token": "abc",
            "Authorization": "x",
            "client_secret": "y",
            "api_key": "z",
            "token_ref": "sha256:ab",
            "approval_or_grant_ref": "g-1",
            "nested": [{"password": "p", "keep": "me"}],
            "empty": {"token": None},
        }
    )
    assert cleaned["token"] == cleaned["Authorization"] == cleaned["client_secret"] == REDACTED
    assert cleaned["api_key"] == REDACTED
    assert cleaned["token_ref"] == "sha256:ab"
    assert cleaned["approval_or_grant_ref"] == "g-1"
    assert cleaned["nested"] == [{"password": REDACTED, "keep": "me"}]
    assert cleaned["empty"] == {"token": None}
    assert sensitive_key("cookie") and not sensitive_key("tool")


def test_jwts_bearer_values_emails_and_registered_secrets_are_removed() -> None:
    redactor = Redactor()
    redactor.register("hunter2-value")
    redactor.register("abc")  # too short to keep
    text = redactor.text(
        "got eyJhbGciOiJFZERTQSJ9.eyJzdWIiOiJ1LTEifQ.c2ln and Bearer s3cr3t-thing "
        "for alice@example.org using hunter2-value and abc"
    )
    assert "eyJ" not in text and "s3cr3t" not in text and "hunter2" not in text
    assert "alice@example.org" not in text
    assert "[email]" in text and "using [redacted] and abc" in text


def test_the_ledger_redacts_before_hashing_and_writing(tmp_path: Path) -> None:
    redactor = Redactor()
    redactor.register("super-secret-value")
    ledger = Ledger(tmp_path / "ledger.db", redactor)
    ledger.append(_event(1, outcome="failed with super-secret-value for bob@example.org"))
    raw = _raw(tmp_path).execute("SELECT entry FROM ledger").fetchone()[0]
    assert "super-secret-value" not in raw and "bob@example.org" not in raw
    assert ledger.verify().ok


def test_the_log_filter_removes_registered_values(caplog: pytest.LogCaptureFixture) -> None:
    redactor = Redactor()
    redactor.register("log-me-not")
    log = logging.getLogger("ritvo.test.redact")
    log.addFilter(RedactingFilter(redactor))
    with caplog.at_level(logging.INFO, logger="ritvo.test.redact"):
        log.info("value is %s", "log-me-not")
    assert "log-me-not" not in caplog.text
    assert "[redacted]" in caplog.text


# Commands


def test_audit_commands(tmp_path: Path) -> None:
    _ledger(tmp_path).close()
    path = str(tmp_path / "ledger.db")
    ok = runner.invoke(app, ["audit", "verify", "--ledger", path])
    assert ok.exit_code == 0 and "OK: 3 entries" in ok.output
    out = tmp_path / "export.jsonl"
    exported = runner.invoke(app, ["audit", "export", "--ledger", path, "--out", str(out)])
    assert exported.exit_code == 0
    lines = out.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 3 and all(canonical(json.loads(line)).decode() == line for line in lines)
    assert (
        runner.invoke(app, ["audit", "export", "--ledger", path, "--out", str(out)]).exit_code == 1
    )
    printed = runner.invoke(app, ["audit", "export", "--ledger", path])
    assert printed.output.splitlines() == lines


def test_audit_commands_need_a_ledger(tmp_path: Path) -> None:
    result = runner.invoke(app, ["audit", "verify", "--ledger", str(tmp_path / "none.db")])
    assert result.exit_code == 1 and "does not exist" in result.output
