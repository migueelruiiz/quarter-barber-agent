"""
Unit tests for src/memory/session_store.py.

SESSION_DB_PATH is redirected to a tmp_path file for every test (autouse
fixture) so these never touch a real project data/ directory or share
state across test runs.
"""

from datetime import datetime, timedelta, timezone

import pytest

from src.memory import session_store


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path, monkeypatch):
    db_path = tmp_path / "sessions.db"
    monkeypatch.setenv("SESSION_DB_PATH", str(db_path))
    return db_path


# ---------------------------------------------------------------------------
# save/get round-trip
# ---------------------------------------------------------------------------

def test_get_session_returns_empty_list_when_no_session_exists():
    assert session_store.get_session("+34600000000") == []


def test_save_and_get_round_trip():
    messages = [{"role": "user", "content": "hola"}, {"role": "assistant", "content": "hola!"}]
    session_store.save_session("+34600000000", messages)

    assert session_store.get_session("+34600000000") == messages


def test_different_session_ids_stored_independently():
    session_store.save_session("session-a", [{"role": "user", "content": "a"}])
    session_store.save_session("session-b", [{"role": "user", "content": "b"}])

    assert session_store.get_session("session-a") == [{"role": "user", "content": "a"}]
    assert session_store.get_session("session-b") == [{"role": "user", "content": "b"}]


# ---------------------------------------------------------------------------
# Upsert behavior: same session_id overwrites, doesn't duplicate rows
# ---------------------------------------------------------------------------

def test_upsert_overwrites_message_history():
    session_store.save_session("session-1", [{"role": "user", "content": "first"}])
    session_store.save_session("session-1", [{"role": "user", "content": "second"}])

    assert session_store.get_session("session-1") == [{"role": "user", "content": "second"}]


def test_upsert_does_not_duplicate_rows():
    session_store.save_session("session-1", [{"role": "user", "content": "first"}])
    session_store.save_session("session-1", [{"role": "user", "content": "second"}])

    conn = session_store._connect()
    try:
        count = conn.execute(
            "SELECT COUNT(*) FROM sessions WHERE session_id = ?", ("session-1",)
        ).fetchone()[0]
    finally:
        conn.close()

    assert count == 1


# ---------------------------------------------------------------------------
# Session expiration after 90 minutes (mocked time)
# ---------------------------------------------------------------------------

class _FrozenDatetime(datetime):
    frozen_now = None

    @classmethod
    def now(cls, tz=None):
        return cls.frozen_now


def test_session_within_90_minutes_is_returned(monkeypatch):
    base = datetime(2026, 7, 12, 10, 0, tzinfo=timezone.utc)
    _FrozenDatetime.frozen_now = base
    monkeypatch.setattr(session_store, "datetime", _FrozenDatetime)

    session_store.save_session("session-x", [{"role": "user", "content": "hi"}])

    _FrozenDatetime.frozen_now = base + timedelta(minutes=89)
    assert session_store.get_session("session-x") == [{"role": "user", "content": "hi"}]


def test_session_older_than_90_minutes_is_expired(monkeypatch):
    base = datetime(2026, 7, 12, 10, 0, tzinfo=timezone.utc)
    _FrozenDatetime.frozen_now = base
    monkeypatch.setattr(session_store, "datetime", _FrozenDatetime)

    session_store.save_session("session-y", [{"role": "user", "content": "hi"}])

    _FrozenDatetime.frozen_now = base + timedelta(minutes=91)
    assert session_store.get_session("session-y") == []
