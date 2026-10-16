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
CREATE TABLE IF NOT EXISTS cover_letters (
  job_id     TEXT PRIMARY KEY,
  company    TEXT,
  title      TEXT,
  text       TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS llm_cache (
  kind       TEXT NOT NULL,
  key        TEXT NOT NULL,
  value      TEXT NOT NULL,
  created_at TEXT NOT NULL,
  PRIMARY KEY (kind, key)
);
-- Small facts the app keeps about itself (src/properties.py): the day the
-- daily batch last ran, and whatever else needs remembering across restarts.
CREATE TABLE IF NOT EXISTS properties (
  name       TEXT PRIMARY KEY,
  value      TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
-- The Applied page (src/tracker.py): where each application stands after it
-- went in. A job is on that page because job_history says "applied"; this
-- holds what happened next.
CREATE TABLE IF NOT EXISTS applications (
  job_id           TEXT PRIMARY KEY,
  stage            TEXT NOT NULL,
  stage_at         TEXT NOT NULL,
  notes            TEXT,
  contact_name     TEXT,
  contact_role     TEXT,
  contact_link     TEXT,
  contact_relation TEXT,
  follow_up_at     TEXT,
  updated_at       TEXT NOT NULL
);
-- Its timeline: stage changes and messages sent, newest last.
CREATE TABLE IF NOT EXISTS application_events (
  id     INTEGER PRIMARY KEY AUTOINCREMENT,
  job_id TEXT NOT NULL,
  kind   TEXT NOT NULL,
  text   TEXT,
  at     TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_application_events_job ON application_events(job_id);
-- Referral, outreach, follow-up and thank-you drafts (src/web/outreach.py).
-- Drafts only: the candidate sends them, and says so with Mark sent.
CREATE TABLE IF NOT EXISTS outreach_messages (
  job_id     TEXT NOT NULL,
  kind       TEXT NOT NULL,
  text       TEXT NOT NULL,
  sent_at    TEXT,
  updated_at TEXT NOT NULL,
  PRIMARY KEY (job_id, kind)
);
-- The daily "still open?" check (src/open_check.py): when each waiting job
-- was last looked at, and what its page said.
CREATE TABLE IF NOT EXISTS open_checks (
  job_id     TEXT PRIMARY KEY,
  stamp      TEXT,
  result     TEXT NOT NULL,
  detail     TEXT,
  checked_at TEXT NOT NULL
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
                    # The posting's text, so a company + title match can be
                    # checked against it (history.same_posting).
                    if "description" not in cols:
                        conn.execute("ALTER TABLE job_history ADD COLUMN description TEXT")
                    # When the candidate pressed Use on this letter; a later
                    # session attaches it without asking again.
                    cols = {row[1] for row in conn.execute("PRAGMA table_info(cover_letters)")}
                    if "accepted_at" not in cols:
                        conn.execute("ALTER TABLE cover_letters ADD COLUMN accepted_at TEXT")
                    _initialized.add(key)
        return conn
    except BaseException:
        conn.close()
        raise
