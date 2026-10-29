"""Base de datos SQLite: eventos y cola de subida a la nube."""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path

from .util import DATA

SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    camera_id TEXT NOT NULL,
    type TEXT NOT NULL,
    ts REAL NOT NULL,
    message TEXT DEFAULT '',
    snapshot TEXT DEFAULT '',
    clip TEXT DEFAULT '',
    description TEXT DEFAULT '',
    labels TEXT DEFAULT '',
    meta TEXT DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_events_ts ON events(ts DESC);
CREATE INDEX IF NOT EXISTS idx_events_cam ON events(camera_id, ts DESC);

CREATE TABLE IF NOT EXISTS uploads (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    path TEXT NOT NULL,
    kind TEXT DEFAULT 'recording',
    status TEXT DEFAULT 'pending',
    attempts INTEGER DEFAULT 0,
    error TEXT DEFAULT '',
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_uploads_status ON uploads(status, id);
"""

# Columnas añadidas después de la primera versión (migración automática).
EVENT_MIGRATIONS = {
    "description": "TEXT DEFAULT ''",
    "labels": "TEXT DEFAULT ''",
    "meta": "TEXT DEFAULT ''",
}

_UNSET = object()


def _dumps(value) -> str:
    if value is None or value == "":
        return ""
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False)


def _loads(value, default):
    if not value:
        return default
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return default


class DB:
    def __init__(self, path: Path | None = None) -> None:
        DATA.mkdir(parents=True, exist_ok=True)
        self.path = path or (DATA / "vstarcam.db")
        self._lock = threading.Lock()
        self.conn = sqlite3.connect(str(self.path), check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        with self._lock:
            self.conn.executescript(SCHEMA)
            self._migrate_events()
            self.conn.commit()

    def _migrate_events(self) -> None:
        """Añade columnas nuevas a bases de datos ya existentes."""
        columns = {row["name"] for row in self.conn.execute("PRAGMA table_info(events)")}
        for name, decl in EVENT_MIGRATIONS.items():
            if name not in columns:
                self.conn.execute(f"ALTER TABLE events ADD COLUMN {name} {decl}")

    def close(self) -> None:
        with self._lock:
            self.conn.close()

    # -------------------------------------------------------------- eventos
    def add_event(
        self, camera_id: str, kind: str, message: str = "",
        snapshot: str = "", clip: str = "", description: str = "",
        labels=None, meta=None,
    ) -> int:
        with self._lock:
            cur = self.conn.execute(
                "INSERT INTO events (camera_id, type, ts, message, snapshot, clip,"
                " description, labels, meta) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    camera_id, kind, time.time(), message, snapshot, clip,
                    description or message, _dumps(labels), _dumps(meta),
                ),
            )
            self.conn.commit()
            return int(cur.lastrowid)

    def update_event(
        self, event_id: int, message: str | None = None,
        description: str | None = None, labels=_UNSET, meta=_UNSET,
        merge_meta: dict | None = None, snapshot: str | None = None,
        clip: str | None = None,
    ) -> None:
        """Actualiza un evento; ``merge_meta`` conserva las claves existentes."""
        fields: list[str] = []
        args: list = []
        if message is not None:
            fields.append("message = ?")
            args.append(message)
        if description is not None:
            fields.append("description = ?")
            args.append(description)
        if snapshot is not None:
            fields.append("snapshot = ?")
            args.append(snapshot)
        if clip is not None:
            fields.append("clip = ?")
            args.append(clip)
        if labels is not _UNSET:
            fields.append("labels = ?")
            args.append(_dumps(labels))
        with self._lock:
            if meta is not _UNSET or merge_meta:
                row = self.conn.execute(
                    "SELECT meta FROM events WHERE id = ?", (event_id,)
                ).fetchone()
                if row is None:
                    return
                current = _loads(row["meta"], {})
                if not isinstance(current, dict):
                    current = {}
                if merge_meta:
                    current.update(merge_meta)
                if meta is not _UNSET:
                    current = _loads(meta, {}) if isinstance(meta, str) else (meta or {})
                fields.append("meta = ?")
                args.append(_dumps(current))
            if not fields:
                return
            args.append(event_id)
            self.conn.execute(
                f"UPDATE events SET {', '.join(fields)} WHERE id = ?", args
            )
            self.conn.commit()

    def events(
        self, limit: int = 50, camera_id: str | None = None,
        since: float | None = None, kind: str | None = None,
    ) -> list[dict]:
        sql = "SELECT * FROM events WHERE 1=1"
        args: list = []
        if camera_id:
            sql += " AND camera_id = ?"
            args.append(camera_id)
        if since:
            sql += " AND ts >= ?"
            args.append(since)
        if kind:
            sql += " AND type = ?"
            args.append(kind)
        sql += " ORDER BY ts DESC LIMIT ?"
        args.append(max(1, min(limit, 1000)))
        with self._lock:
            rows = self.conn.execute(sql, args).fetchall()
        items = []
        for row in rows:
            item = dict(row)
            item["labels"] = _loads(item.get("labels"), [])
            item["meta"] = _loads(item.get("meta"), {})
            item["description"] = item.get("description") or item.get("message") or ""
            items.append(item)
        return items

    def delete_event(self, event_id: int) -> None:
        with self._lock:
            self.conn.execute("DELETE FROM events WHERE id = ?", (event_id,))
            self.conn.commit()

    def prune_events(self, keep_days: int) -> int:
        cutoff = time.time() - keep_days * 86400
        with self._lock:
            cur = self.conn.execute("DELETE FROM events WHERE ts < ?", (cutoff,))
            self.conn.commit()
            return cur.rowcount

    # --------------------------------------------------------------- subidas
    def enqueue_upload(self, path: str, kind: str = "recording") -> None:
        now = time.time()
        with self._lock:
            self.conn.execute(
                "INSERT INTO uploads (path, kind, status, created_at, updated_at)"
                " VALUES (?, ?, 'pending', ?, ?)",
                (path, kind, now, now),
            )
            self.conn.commit()

    def pending_uploads(self, limit: int = 10) -> list[dict]:
        with self._lock:
            rows = self.conn.execute(
                "SELECT * FROM uploads WHERE status = 'pending' AND attempts < 8"
                " ORDER BY id LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]

    def set_upload_status(self, upload_id: int, status: str, error: str = "") -> None:
        with self._lock:
            self.conn.execute(
                "UPDATE uploads SET status = ?, error = ?, attempts = attempts + 1,"
                " updated_at = ? WHERE id = ?",
                (status, error[:500], time.time(), upload_id),
            )
            self.conn.commit()

    def upload_stats(self) -> dict:
        with self._lock:
            rows = self.conn.execute(
                "SELECT status, COUNT(*) AS n FROM uploads GROUP BY status"
            ).fetchall()
        return {r["status"]: r["n"] for r in rows}
