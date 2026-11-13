from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  observed_at TEXT NOT NULL,
  event_type TEXT NOT NULL,
  entity_id TEXT NOT NULL,
  payload_json TEXT NOT NULL,
  previous_hash TEXT NOT NULL,
  event_hash TEXT NOT NULL UNIQUE
);
CREATE INDEX IF NOT EXISTS idx_events_entity ON events(entity_id, id);
CREATE INDEX IF NOT EXISTS idx_events_type_time ON events(event_type, observed_at);
CREATE TABLE IF NOT EXISTS idempotency_keys (
  idempotency_key TEXT PRIMARY KEY,
  event_hash TEXT NOT NULL,
  created_at TEXT NOT NULL,
  FOREIGN KEY(event_hash) REFERENCES events(event_hash)
);
"""


class FlightRecorder:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.executescript(SCHEMA)

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        """Open a connection, commit or roll back, then always close it.

        ``with sqlite3.connect(...)`` only manages the transaction, never the
        handle. Leaving handles open leaks file descriptors in the long-running
        service and, on Windows, keeps the database file locked so it cannot be
        removed or replaced.
        """
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA busy_timeout=5000")
        connection.execute("PRAGMA foreign_keys=ON")
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def position_events(self) -> list[dict[str, Any]]:
        """Return the complete position history required for restart recovery."""
        with self._connect() as connection:
            rows = connection.execute(
                """SELECT * FROM events
                   WHERE event_type IN ('paper_position_opened','paper_position_closed')
                   ORDER BY id"""
            ).fetchall()
        return [dict(row) | {"payload": json.loads(row["payload_json"])} for row in rows]

    def append(
        self,
        event_type: str,
        entity_id: str,
        payload: dict[str, Any],
        observed_at: datetime | None = None,
    ) -> str:
        return self._append(event_type, entity_id, payload, observed_at, None)

    def append_once(
        self,
        event_type: str,
        entity_id: str,
        payload: dict[str, Any],
        idempotency_key: str,
        observed_at: datetime | None = None,
    ) -> str:
        """Append exactly once across collector retries and process restarts."""
        if not idempotency_key.strip():
            raise ValueError("idempotency_key is required")
        return self._append(event_type, entity_id, payload, observed_at, idempotency_key)

    def _append(
        self,
        event_type: str,
        entity_id: str,
        payload: dict[str, Any],
        observed_at: datetime | None,
        idempotency_key: str | None,
    ) -> str:
        timestamp = (observed_at or datetime.now(UTC)).isoformat()
        payload_json = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            if idempotency_key is not None:
                existing = connection.execute(
                    "SELECT event_hash FROM idempotency_keys WHERE idempotency_key = ?",
                    (idempotency_key,),
                ).fetchone()
                if existing is not None:
                    return str(existing["event_hash"])
            row = connection.execute(
                "SELECT event_hash FROM events ORDER BY id DESC LIMIT 1"
            ).fetchone()
            previous_hash = row["event_hash"] if row else "GENESIS"
            material = f"{previous_hash}|{timestamp}|{event_type}|{entity_id}|{payload_json}"
            event_hash = hashlib.sha256(material.encode()).hexdigest()
            connection.execute(
                """INSERT INTO events
                   (observed_at,event_type,entity_id,payload_json,previous_hash,event_hash)
                   VALUES (?,?,?,?,?,?)""",
                (timestamp, event_type, entity_id, payload_json, previous_hash, event_hash),
            )
            if idempotency_key is not None:
                connection.execute(
                    """INSERT INTO idempotency_keys
                       (idempotency_key,event_hash,created_at) VALUES (?,?,?)""",
                    (idempotency_key, event_hash, datetime.now(UTC).isoformat()),
                )
        return event_hash

    def list_events(self, limit: int = 100, entity_id: str | None = None) -> list[dict[str, Any]]:
        limit = max(1, min(limit, 1000))
        query = "SELECT * FROM events"
        params: list[Any] = []
        if entity_id:
            query += " WHERE entity_id = ?"
            params.append(entity_id)
        query += " ORDER BY id DESC LIMIT ?"
        params.append(limit)
        with self._connect() as connection:
            rows = connection.execute(query, params).fetchall()
        return [dict(row) | {"payload": json.loads(row["payload_json"])} for row in rows]

    def events_by_type(self, event_type: str, limit: int = 200_000) -> list[dict[str, Any]]:
        """Return every event of one type, oldest first, for offline analysis.

        ``list_events`` caps at 1000 because it backs an API surface where an
        unbounded read is a denial-of-service risk. Analysis has the opposite
        requirement: a single overnight collector run journals thousands of
        observations, and silently scoring a source against the most recent
        fifth of them would produce a confident, wrong answer.
        """
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM events WHERE event_type = ? ORDER BY id LIMIT ?",
                (event_type, max(1, limit)),
            ).fetchall()
        return [dict(row) | {"payload": json.loads(row["payload_json"])} for row in rows]

    def last_event(self, entity_id: str, event_type: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                """SELECT * FROM events
                   WHERE entity_id = ? AND event_type = ?
                   ORDER BY id DESC LIMIT 1""",
                (entity_id, event_type),
            ).fetchone()
        return None if row is None else dict(row) | {"payload": json.loads(row["payload_json"])}

    def verify_chain(self) -> bool:
        previous = "GENESIS"
        with self._connect() as connection:
            rows = connection.execute("SELECT * FROM events ORDER BY id").fetchall()
        for row in rows:
            material = "|".join(
                (
                    previous,
                    row["observed_at"],
                    row["event_type"],
                    row["entity_id"],
                    row["payload_json"],
                )
            )
            expected = hashlib.sha256(material.encode()).hexdigest()
            if row["previous_hash"] != previous or row["event_hash"] != expected:
                return False
            previous = row["event_hash"]
        return True
