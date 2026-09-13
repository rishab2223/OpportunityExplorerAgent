"""A short queue of jobs to apply to, one after another.

The agent never clicks submit, so every job in a queue still ends with the
candidate reading the form and submitting it themselves. A queue cannot
reduce that to fewer than one review per job, and is not meant to: what it
removes is everything either side of the review - going back to the table,
finding the next row, pressing Start apply, and waiting for Chrome.

Strictly serial, because it has to be. Chrome allows one instance per
user-data-dir and the persistent profile (the one holding the candidate's
logins) is a single directory; Playwright's sync API is thread-affine on top
of that. So the next job starts only once the previous browser has closed,
which is what worker.start_apply's on_released reports.

Two ways out of a job, and they are opposites:
  park  - leave this one for later, start the next. Nothing is recorded.
  abort - stop the whole queue.

There is no cap on the length. How many applications a candidate can read
properly in one sitting is their judgement, not this module's, and a number
picked here would only be a guess dressed as a rule.
"""

from __future__ import annotations

import threading
from typing import Any, Callable

_LOCK = threading.Lock()
_pending: list[dict[str, str]] = []
_parked: list[dict[str, str]] = []
_done: list[dict[str, str]] = []
_current: dict[str, str] | None = None
_starter: Callable[[str, str], str] | None = None
_note: str = ""


def _item(entry: dict[str, Any]) -> dict[str, str]:
    return {
        "stamp": str(entry.get("stamp") or ""),
        "job_id": str(entry.get("job_id") or ""),
        "label": str(entry.get("label") or ""),
    }


def start(items: list[dict[str, Any]], starter: Callable[[str, str], str]) -> dict[str, Any]:
    """Queue these jobs and begin the first. `starter(stamp, job_id)` opens one
    apply session and returns its label; it raises whatever the single-job
    path raises, which is reported unchanged."""
    global _starter, _note
    wanted = [_item(entry) for entry in items]
    wanted = [entry for entry in wanted if entry["stamp"] and entry["job_id"]]
    if not wanted:
        raise ValueError("no jobs to apply to")

    with _LOCK:
        if _current is not None:
            raise ValueError("a queued job is already running")
        _pending[:] = wanted
        _parked.clear()
        _done.clear()
        _starter = starter
        _note = ""
    _advance()
    return state()


def _advance() -> None:
    """Start the next job, if there is one. Never raises: a queue that cannot
    start a job stops and says why, rather than taking the session with it."""
    global _current, _note
    with _LOCK:
        if _current is not None or not _pending:
            return
        entry = _pending.pop(0)
        starter = _starter
        _current = entry
    if starter is None:
        with _LOCK:
            _current = None
        return
    try:
        entry["label"] = starter(entry["stamp"], entry["job_id"]) or entry["label"]
    except Exception as exc:
        # Could not open this one (no apply URL, the browser is busy, a bad
        # run folder). Stop rather than churn through the rest: whatever
        # blocked this job almost certainly blocks the next.
        with _LOCK:
            _current = None
            _note = f"stopped: {str(exc).splitlines()[0][:200]}"
            _pending.clear()


def release(status: str) -> None:
    """One session has ended AND its browser is closed. Called on the worker
    thread from start_apply's on_released."""
    global _current, _note
    with _LOCK:
        entry, _current = _current, None
        if entry is None:
            return                      # a single-job session, not ours
        if status == "parked":
            _parked.append(entry)
        else:
            _done.append({**entry, "status": status})
        if status == "aborted":
            # Abort means stop everything; park is the one that moves on.
            if _pending:
                _note = f"aborted with {len(_pending)} job(s) still queued"
            _pending.clear()
    _advance()


def clear() -> dict[str, Any]:
    """Drop everything still queued. The running session is left alone - the
    candidate ends that with park or abort in the chat."""
    global _note
    with _LOCK:
        dropped = len(_pending)
        _pending.clear()
        _note = f"cleared {dropped} queued job(s)" if dropped else ""
    return state()


def state() -> dict[str, Any]:
    with _LOCK:
        return {
            "current": dict(_current) if _current else None,
            "pending": [dict(entry) for entry in _pending],
            "parked": [dict(entry) for entry in _parked],
            "done": [dict(entry) for entry in _done],
            "note": _note,
            "active": _current is not None or bool(_pending),
        }


def reset() -> None:
    """Forget everything. For tests, and for a server that just restarted."""
    global _current, _starter, _note
    with _LOCK:
        _pending.clear()
        _parked.clear()
        _done.clear()
        _current = None
        _starter = None
        _note = ""
