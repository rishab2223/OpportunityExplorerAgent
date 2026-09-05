"""Shared SQLite connection for the app's one local database.

Both cross-run job history (src/history.py) and the apply answer bank
(src/answers.py) live in localData/job_history.db. Keeping the connection,
WAL setup, schema init and the JOB_HISTORY_DB override here means there is
exactly one init path - two modules racing their own schema setup on a fresh
file is the bug this layout prevents.

JOB_HISTORY_DB redirects the whole database to another file. Set it when
testing against a live server so writes can never touch your real data:
JOB_HISTORY_DB=localData/scratch_history.db
"""

from __future__ import annotations

import os
import sqlite3
import threading
from pathlib import Path

from src.config import ROOT

_ENV_DB = os.environ.get("JOB_HISTORY_DB", "").strip()
DB_PATH = Path(_ENV_DB) if _ENV_DB else ROOT / "localData" / "job_history.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS job_history (
  job_id      TEXT PRIMARY KEY,
  status      TEXT NOT NULL,
  company     TEXT,
  title       TEXT,
  location    TEXT,
  source      TEXT,
  apply_url   TEXT,
  fingerprint TEXT,
  stamp       TEXT,
  note        TEXT,
  contact     TEXT,
  marked_at   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_history_fingerprint ON job_history(fingerprint);
CREATE TABLE IF NOT EXISTS known_answers (
  question_key TEXT PRIMARY KEY,
  question     TEXT NOT NULL,
  answer       TEXT NOT NULL,
  kind         TEXT NOT NULL,
  times_used   INTEGER NOT NULL DEFAULT 0,
  first_seen   TEXT NOT NULL,
  last_used    TEXT
);
"""

_INIT_LOCK = threading.Lock()
_initialized: set[str] = set()


def _db_path() -> Path:
    """History tests retarget history.DB_PATH; honor that seam here."""
    from src import history

    return Path(history.DB_PATH)


def connect() -> sqlite3.Connection:
    path = _db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=10)
    try:
        conn.row_factory = sqlite3.Row
        key = str(path)
        if key not in _initialized:
            # Serialize first-time setup: two threads racing to create the
            # schema on a fresh file would collide on the schema transaction.
            with _INIT_LOCK:
                if key not in _initialized:
                    conn.execute("PRAGMA journal_mode=WAL")
                    conn.executescript(_SCHEMA)
                    # Databases created before the referral feature lack the
                    # contact column; upgrade them in place.
                    cols = {row[1] for row in conn.execute("PRAGMA table_info(job_history)")}
                    if "contact" not in cols:
                        conn.execute("ALTER TABLE job_history ADD COLUMN contact TEXT")
                    _initialized.add(key)
        return conn
    except BaseException:
        conn.close()
        raise
