"""Named values the app remembers across restarts, in the `properties` table
of localData/job_history.db: the day the daily batch last ran, and more as
they come. Values are text; callers own their meaning."""

from __future__ import annotations

from datetime import datetime, timezone

from src import db


def get(name: str, default: str | None = None) -> str | None:
    conn = db.connect()
    try:
        row = conn.execute("SELECT value FROM properties WHERE name = ?", (name,)).fetchone()
    finally:
        conn.close()
    return row["value"] if row else default


def put(name: str, value: str) -> None:
    conn = db.connect()
    try:
        with conn:
            conn.execute(
                "INSERT INTO properties (name, value, updated_at) VALUES (?, ?, ?) "
                "ON CONFLICT(name) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at",
                (name, str(value), datetime.now(timezone.utc).isoformat()))
    finally:
        conn.close()


def all_values() -> dict[str, dict[str, str]]:
    conn = db.connect()
    try:
        rows = conn.execute("SELECT name, value, updated_at FROM properties ORDER BY name").fetchall()
    finally:
        conn.close()
    return {r["name"]: {"value": r["value"], "updated_at": r["updated_at"]} for r in rows}
