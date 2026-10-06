"""Results of model calls a later run would otherwise make again.

A job posting stays up for days and every run re-scrapes it: the same posting
was scored and tailored afresh each morning. A result is reused only when
everything that went into it is the same - the posting's text, the resume,
the model and the prompt - so the key is a hash of all of them, and a change
to any one is simply a miss.

Kept in localData/job_history.db (llm_cache) and never shown anywhere; delete
the rows, or turn reuse off in the run settings, to start fresh.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from typing import Any

from src import db

# Old entries stop mattering once the posting is gone; a posting rarely lives
# longer than this, and a stale key costs one row.
MAX_AGE_DAYS = 30


def key(*parts: str) -> str:
    digest = hashlib.sha256()
    for part in parts:
        digest.update(str(part or "").encode("utf-8"))
        digest.update(b"\x1f")
    return digest.hexdigest()


def get(kind: str, cache_key: str) -> dict[str, Any] | None:
    try:
        conn = db.connect()
    except Exception:
        return None
    try:
        row = conn.execute(
            "SELECT value, created_at FROM llm_cache WHERE kind = ? AND key = ?",
            (kind, cache_key)).fetchone()
    except Exception:
        return None
    finally:
        conn.close()
    if row is None:
        return None
    try:
        made = datetime.fromisoformat(row["created_at"])
        if (datetime.now(timezone.utc) - made).days > MAX_AGE_DAYS:
            return None
        value = json.loads(row["value"])
    except (TypeError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def forget(kind: str, cache_key: str) -> None:
    """Drop one entry: a tailored resume that could not be made to fit one
    page is worth a fresh attempt next time, not a repeat."""
    try:
        conn = db.connect()
    except Exception:
        return
    try:
        with conn:
            conn.execute("DELETE FROM llm_cache WHERE kind = ? AND key = ?", (kind, cache_key))
    except Exception:
        pass
    finally:
        conn.close()


def prune() -> int:
    """Delete what is past MAX_AGE_DAYS, once per run; each tailoring row
    holds a whole resume, so the table would otherwise grow by tens of
    kilobytes per job for ever. Returns how many went."""
    try:
        conn = db.connect()
    except Exception:
        return 0
    try:
        limit = (datetime.now(timezone.utc) - timedelta(days=MAX_AGE_DAYS)).isoformat()
        with conn:
            gone = conn.execute("DELETE FROM llm_cache WHERE created_at < ?", (limit,)).rowcount
        return int(gone or 0)
    except Exception:
        return 0
    finally:
        conn.close()


def put(kind: str, cache_key: str, value: dict[str, Any]) -> None:
    """Best effort: a cache that cannot be written costs a future call, never
    this run."""
    try:
        conn = db.connect()
    except Exception:
        return
    try:
        with conn:
            conn.execute(
                "INSERT OR REPLACE INTO llm_cache (kind, key, value, created_at) VALUES (?, ?, ?, ?)",
                (kind, cache_key, json.dumps(value), datetime.now(timezone.utc).isoformat()))
    except Exception:
        pass
    finally:
        conn.close()
