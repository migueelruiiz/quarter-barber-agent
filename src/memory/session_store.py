"""
SQLite-backed session store for the ReAct agent loop.

Implements docs/react_loop_spec.md ("Session memory"). The FastAPI backend
is stateless between HTTP requests -- each WhatsApp webhook call is an
independent process invocation with no in-process memory of earlier turns.
This module is what lets a multi-turn booking conversation, keyed by the
customer's phone number (`session_id`), survive across those separate calls.

Full message history is stored verbatim (every user/assistant/tool message),
not a summarized state -- booking conversations are short enough that
summarization would add a class of bugs (stale/incorrect summaries) without
a real capacity constraint to justify it.
"""

import json
import os
import sqlite3
from datetime import datetime, timedelta, timezone

SESSION_TIMEOUT = timedelta(minutes=90)

DEFAULT_DB_PATH = os.path.join("data", "sessions.db")


def _db_path() -> str:
    return os.environ.get("SESSION_DB_PATH", DEFAULT_DB_PATH)


def _connect() -> sqlite3.Connection:
    path = _db_path()
    dirname = os.path.dirname(path)
    if dirname:
        os.makedirs(dirname, exist_ok=True)

    conn = sqlite3.connect(path)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS sessions (
            session_id TEXT PRIMARY KEY,
            messages TEXT NOT NULL,
            updated_at TIMESTAMP NOT NULL
        )
        """
    )
    return conn


def get_session(session_id: str) -> list[dict]:
    """Return the stored message history for session_id, or [] if no session
    exists or the stored updated_at is older than SESSION_TIMEOUT."""
    conn = _connect()
    try:
        row = conn.execute(
            "SELECT messages, updated_at FROM sessions WHERE session_id = ?",
            (session_id,),
        ).fetchone()
    finally:
        conn.close()

    if row is None:
        return []

    messages_json, updated_at_raw = row
    updated_at = datetime.fromisoformat(updated_at_raw)
    if updated_at.tzinfo is None:
        updated_at = updated_at.replace(tzinfo=timezone.utc)

    if datetime.now(timezone.utc) - updated_at > SESSION_TIMEOUT:
        return []

    return json.loads(messages_json)


def save_session(session_id: str, messages: list[dict]) -> None:
    """Upsert the full message list for session_id with the current timestamp."""
    conn = _connect()
    try:
        conn.execute(
            """
            INSERT OR REPLACE INTO sessions (session_id, messages, updated_at)
            VALUES (?, ?, ?)
            """,
            (session_id, json.dumps(messages), datetime.now(timezone.utc).isoformat()),
        )
        conn.commit()
    finally:
        conn.close()
