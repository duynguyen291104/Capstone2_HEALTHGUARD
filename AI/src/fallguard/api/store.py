from __future__ import annotations

import json
import sqlite3
import threading
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


class SQLiteEventStore:
    """Small thread-safe SQLite repository for fall events and feedback."""

    def __init__(self, path: str | Path = "runtime/fallguard.db") -> None:
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._connection = sqlite3.connect(self.path, check_same_thread=False)
        self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA foreign_keys = ON")
        self._connection.execute("PRAGMA busy_timeout = 5000")
        if self.path != ":memory:":
            self._connection.execute("PRAGMA journal_mode = WAL")
        self._create_schema()

    def _create_schema(self) -> None:
        with self._lock, self._connection:
            self._connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS events (
                    event_id TEXT PRIMARY KEY,
                    state TEXT NOT NULL,
                    risk_level TEXT NOT NULL,
                    timestamp_ms INTEGER NOT NULL,
                    camera_id TEXT,
                    person_id TEXT,
                    source TEXT,
                    fall_probability REAL NOT NULL DEFAULT 0,
                    inactive_seconds REAL NOT NULL DEFAULT 0,
                    self_recovery INTEGER NOT NULL DEFAULT 0,
                    evidence_json TEXT NOT NULL DEFAULT '[]',
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    feedback_label TEXT,
                    feedback_notes TEXT,
                    feedback_reviewer TEXT,
                    feedback_at TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_events_timestamp
                    ON events(timestamp_ms DESC);
                CREATE INDEX IF NOT EXISTS idx_events_camera
                    ON events(camera_id);
                CREATE INDEX IF NOT EXISTS idx_events_state
                    ON events(state);
                """
            )

    def upsert_event(self, event: Mapping[str, Any]) -> dict[str, Any]:
        event_id = str(event.get("event_id") or "").strip()
        if not event_id:
            raise ValueError("event_id is required")
        now = datetime.now(UTC).isoformat()
        evidence = list(event.get("evidence") or [])
        metadata = dict(event.get("metadata") or {})
        values = {
            "event_id": event_id,
            "state": str(event.get("state") or "CONFIRMED_FALL"),
            "risk_level": str(event.get("risk_level") or "HIGH"),
            "timestamp_ms": int(event.get("timestamp_ms") or 0),
            "camera_id": _optional_string(event.get("camera_id")),
            "person_id": _optional_string(event.get("person_id")),
            "source": _optional_string(event.get("source")),
            "fall_probability": float(
                event.get(
                    "fall_probability",
                    metadata.get("smoothed_fall_probability", 0.0),
                )
            ),
            "inactive_seconds": float(event.get("inactive_seconds") or 0.0),
            "self_recovery": int(bool(event.get("self_recovery", False))),
            "evidence_json": json.dumps(evidence, ensure_ascii=False),
            "metadata_json": json.dumps(metadata, ensure_ascii=False),
            "created_at": now,
            "updated_at": now,
        }
        with self._lock, self._connection:
            self._connection.execute(
                """
                INSERT INTO events (
                    event_id, state, risk_level, timestamp_ms, camera_id,
                    person_id, source, fall_probability, inactive_seconds,
                    self_recovery, evidence_json, metadata_json, created_at,
                    updated_at
                ) VALUES (
                    :event_id, :state, :risk_level, :timestamp_ms, :camera_id,
                    :person_id, :source, :fall_probability, :inactive_seconds,
                    :self_recovery, :evidence_json, :metadata_json, :created_at,
                    :updated_at
                )
                ON CONFLICT(event_id) DO UPDATE SET
                    state = excluded.state,
                    risk_level = excluded.risk_level,
                    timestamp_ms = excluded.timestamp_ms,
                    camera_id = COALESCE(excluded.camera_id, events.camera_id),
                    person_id = COALESCE(excluded.person_id, events.person_id),
                    source = COALESCE(excluded.source, events.source),
                    fall_probability = excluded.fall_probability,
                    inactive_seconds = excluded.inactive_seconds,
                    self_recovery = excluded.self_recovery,
                    evidence_json = excluded.evidence_json,
                    metadata_json = excluded.metadata_json,
                    updated_at = excluded.updated_at
                """,
                values,
            )
        stored = self.get_event(event_id)
        if stored is None:  # pragma: no cover - defensive
            raise RuntimeError("Event write did not persist")
        return stored

    save = upsert_event

    def get_event(self, event_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT * FROM events WHERE event_id = ?",
                (event_id,),
            ).fetchone()
        return _decode_row(row) if row is not None else None

    get = get_event

    def list_events(
        self,
        *,
        limit: int = 50,
        offset: int = 0,
        state: str | None = None,
        risk_level: str | None = None,
        camera_id: str | None = None,
    ) -> list[dict[str, Any]]:
        if not 1 <= limit <= 500:
            raise ValueError("limit must be between 1 and 500")
        if offset < 0:
            raise ValueError("offset must be non-negative")
        where, params = _filters(state=state, risk_level=risk_level, camera_id=camera_id)
        query = f"""
            SELECT * FROM events
            {where}
            ORDER BY timestamp_ms DESC, created_at DESC
            LIMIT ? OFFSET ?
        """
        with self._lock:
            rows = self._connection.execute(query, (*params, limit, offset)).fetchall()
        return [_decode_row(row) for row in rows]

    list = list_events

    def count_events(
        self,
        *,
        state: str | None = None,
        risk_level: str | None = None,
        camera_id: str | None = None,
    ) -> int:
        where, params = _filters(state=state, risk_level=risk_level, camera_id=camera_id)
        with self._lock:
            row = self._connection.execute(
                f"SELECT COUNT(*) AS count FROM events {where}",
                params,
            ).fetchone()
        return int(row["count"])

    def add_feedback(
        self,
        event_id: str,
        *,
        label: str,
        notes: str | None = None,
        reviewer: str | None = None,
    ) -> dict[str, Any] | None:
        if label not in {"true_fall", "false_alarm", "uncertain"}:
            raise ValueError("Unsupported feedback label")
        now = datetime.now(UTC).isoformat()
        with self._lock, self._connection:
            cursor = self._connection.execute(
                """
                UPDATE events
                SET feedback_label = ?, feedback_notes = ?,
                    feedback_reviewer = ?, feedback_at = ?, updated_at = ?
                WHERE event_id = ?
                """,
                (label, notes, reviewer, now, now, event_id),
            )
        if cursor.rowcount == 0:
            return None
        return self.get_event(event_id)

    feedback = add_feedback

    def summary(self) -> dict[str, Any]:
        with self._lock:
            row = self._connection.execute(
                """
                SELECT
                    COUNT(*) AS total_events,
                    SUM(CASE WHEN risk_level = 'HIGH' THEN 1 ELSE 0 END) AS high_risk,
                    SUM(CASE WHEN state = 'RECOVERED' OR self_recovery = 1 THEN 1 ELSE 0 END)
                        AS recovered,
                    SUM(CASE WHEN feedback_label = 'false_alarm' THEN 1 ELSE 0 END)
                        AS false_alarms,
                    AVG(inactive_seconds) AS average_inactive_seconds
                FROM events
                """
            ).fetchone()
        return {
            "total_events": int(row["total_events"] or 0),
            "high_risk": int(row["high_risk"] or 0),
            "recovered": int(row["recovered"] or 0),
            "false_alarms": int(row["false_alarms"] or 0),
            "average_inactive_seconds": float(row["average_inactive_seconds"] or 0.0),
        }

    def ping(self) -> bool:
        try:
            with self._lock:
                value = self._connection.execute("SELECT 1").fetchone()[0]
            return value == 1
        except sqlite3.Error:
            return False

    def close(self) -> None:
        with self._lock:
            self._connection.close()


def _filters(
    *,
    state: str | None,
    risk_level: str | None,
    camera_id: str | None,
) -> tuple[str, tuple[str, ...]]:
    clauses: list[str] = []
    params: list[str] = []
    for column, value in (
        ("state", state),
        ("risk_level", risk_level),
        ("camera_id", camera_id),
    ):
        if value is not None:
            clauses.append(f"{column} = ?")
            params.append(value)
    return (f"WHERE {' AND '.join(clauses)}" if clauses else "", tuple(params))


def _decode_row(row: sqlite3.Row) -> dict[str, Any]:
    result = dict(row)
    result["self_recovery"] = bool(result["self_recovery"])
    result["evidence"] = json.loads(result.pop("evidence_json") or "[]")
    result["metadata"] = json.loads(result.pop("metadata_json") or "{}")
    result["feedback"] = (
        {
            "label": result.pop("feedback_label"),
            "notes": result.pop("feedback_notes"),
            "reviewer": result.pop("feedback_reviewer"),
            "created_at": result.pop("feedback_at"),
        }
        if result.get("feedback_label") is not None
        else None
    )
    if result["feedback"] is None:
        result.pop("feedback_label")
        result.pop("feedback_notes")
        result.pop("feedback_reviewer")
        result.pop("feedback_at")
    return result


def _optional_string(value: Any) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip()
    return normalized or None
