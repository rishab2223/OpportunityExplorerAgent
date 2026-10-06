from __future__ import annotations

import json
import re
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src import history
from src.config import ROOT

OUTPUT_DIR = ROOT / "outputs"
STAMP_RE = re.compile(r"^\d{8}T\d{6}$")
VALID_DECISIONS = ("yes", "no")

_DECISION_LOCK = threading.Lock()


class RunNotFound(Exception):
    pass


class JobNotFound(Exception):
    pass


def list_stamps() -> list[str]:
    if not OUTPUT_DIR.exists():
        return []
    stamps = [
        p.name
        for p in OUTPUT_DIR.iterdir()
        if p.is_dir() and STAMP_RE.match(p.name) and (p / "run.json").exists()
    ]
    return sorted(stamps, reverse=True)


def list_runs() -> list[dict[str, Any]]:
    """Each run, newest first, with what the Run dropdown names it by."""
    found = []
    for stamp in list_stamps():
        try:
            run = _read_json(OUTPUT_DIR / stamp / "run.json")
        except (OSError, ValueError):
            run = {}
        run = run if isinstance(run, dict) else {}
        found.append({"stamp": stamp, "status": run.get("status", ""),
                      "match_count": int(run.get("match_count") or 0),
                      "held_back_count": int(run.get("held_back_count") or 0)})
    return found


def load_held_waiting() -> list[dict[str, Any]]:
    """Every held-back job not yet acted on, from every run.

    A job is held in the run that found it, but it waits for the candidate
    until they act on it, whichever run is on screen. Each row carries its
    `stamp`, so actions reach that run's files. A job held in several runs
    (same id, or same company and title) is shown once, from the newest.
    """
    waiting: list[dict[str, Any]] = []
    seen: set[str] = set()
    for stamp in list_stamps():                       # newest first
        if not (OUTPUT_DIR / stamp / "held_back.json").exists():
            continue
        for row in load_jobs(stamp):
            if not row.get("held_back"):
                continue
            keys = {k for k in (row.get("job_id") or "",
                                history.fingerprint(row.get("company") or "", row.get("title") or ""))
                    if k}
            # The newest copy settles it, acted on or not: moving Wednesday's
            # copy to the shortlist must not leave Monday's still waiting.
            if keys & seen:
                continue
            seen |= keys
            if row.get("history_status") or row.get("decision"):
                continue
            row["stamp"] = stamp
            waiting.append(row)
    waiting.sort(key=lambda row: -int(row.get("relevance") or 0))
    return waiting


def run_dir(stamp: str) -> Path:
    if not STAMP_RE.match(stamp or ""):
        raise RunNotFound(f"invalid run id: {stamp}")
    path = OUTPUT_DIR / stamp
    if not path.is_dir():
        raise RunNotFound(f"no such run: {stamp}")
    return path


def load_run(stamp: str) -> dict[str, Any]:
    path = run_dir(stamp) / "run.json"
    if not path.exists():
        raise RunNotFound(f"run.json missing for {stamp}")
    payload = _read_json(path)
    payload["stamp"] = stamp
    return payload


def _rows(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows = _read_json(path)
    return [row for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []


def load_jobs(stamp: str) -> list[dict[str, Any]]:
    """The shortlist, then the jobs a filter held back for review.

    One list, so every endpoint that looks a job up - Start apply, Skip,
    Mark applied, Referral - works on a held-back job as it does on any
    other. A held-back row carries `held_back` (the reason); the page sorts
    the two apart.
    """
    directory = run_dir(stamp)
    rows = _rows(directory / "shortlisted.json")
    for row in _rows(directory / "held_back.json"):
        row["held_back"] = row.get("held_back") or "held back by a filter"
        rows.append(row)
    if not rows:
        return []
    decisions = load_decisions(stamp)
    history_by_id, history_by_fp = history.snapshot()
    for row in rows:
        entry = decisions.get(row.get("job_id") or "", {})
        row["decision"] = entry.get("decision", "")
        row["status"] = entry.get("status", "pending")
        row["decided_at"] = entry.get("decided_at", "")
        # Cross-run history: what you have since applied to, skipped, or are
        # chasing a referral for, from any run.
        entry = history_by_id.get(row.get("job_id") or "")
        h_how = "id" if entry else ""
        if not entry:
            fp = history.fingerprint(row.get("company") or "", row.get("title") or "")
            entry = history_by_fp.get(fp)
            h_how = "similar" if entry else ""
        row["history_status"] = entry["status"] if entry else ""
        row["history_how"] = h_how
        row["history_contact"] = entry["contact"] if entry else ""
        row["history_marked_at"] = entry["marked_at"] if entry else ""
        row["tex_path"] = _sibling(directory, row.get("resume_tex_file"))
        # Older runs predate resume_pdf_path; fall back to the file on disk.
        if not row.get("resume_pdf_path"):
            row["resume_pdf_path"] = _existing_pdf(directory, row.get("resume_tex_file"))
    return rows


def find_job(stamp: str, job_id: str) -> dict[str, Any]:
    for row in load_jobs(stamp):
        if row.get("job_id") == job_id:
            return row
    raise JobNotFound(f"no job {job_id} in run {stamp}")


def decisions_path(stamp: str) -> Path:
    return run_dir(stamp) / "applications.json"


def load_decisions(stamp: str) -> dict[str, dict[str, Any]]:
    path = run_dir(stamp) / "applications.json"
    if not path.exists():
        return {}
    data = _read_json(path)
    return data if isinstance(data, dict) else {}


def save_decision(
    stamp: str,
    job_id: str,
    decision: str | None = None,
    status: str | None = None,
    note: str = "",
) -> dict[str, Any]:
    path = decisions_path(stamp)
    with _DECISION_LOCK:
        data = load_decisions(stamp)
        entry = data.get(job_id) or {}
        if decision is not None:
            entry["decision"] = decision
        if status is not None:
            entry["status"] = status
        if note:
            entry["note"] = note
        entry["decided_at"] = datetime.now(timezone.utc).isoformat()
        data[job_id] = entry
        path.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")
    return entry


def raw_rows(stamp: str) -> list[dict[str, Any]]:
    """The run's rows as written, shortlist then held back, without the
    decision and history columns load_jobs adds (and without its database
    read) - for callers that only need a file name or a title."""
    directory = run_dir(stamp)
    return _rows(directory / "shortlisted.json") + _rows(directory / "held_back.json")


def update_job(stamp: str, job_id: str, fields: dict[str, Any]) -> None:
    """Write fields onto one job of the run (interview prep written on
    demand), in whichever of its files holds it."""
    directory = run_dir(stamp)
    with _DECISION_LOCK:
        for name in ("shortlisted.json", "held_back.json"):
            path = directory / name
            rows = _rows(path)
            for row in rows:
                if row.get("job_id") == job_id:
                    row.update(fields)
                    path.write_text(json.dumps(rows, indent=2, default=str), encoding="utf-8")
                    return
    raise JobNotFound(f"no job {job_id} in run {stamp}")


def read_log(stamp: str) -> list[str]:
    path = run_dir(stamp) / "run.log"
    if not path.exists():
        return []
    return path.read_text(encoding="utf-8", errors="replace").splitlines()


def _sibling(directory: Path, filename: str | None) -> str:
    return str(directory / filename) if filename else ""


def _existing_pdf(directory: Path, tex_file: str | None) -> str:
    if not tex_file:
        return ""
    candidate = (directory / tex_file).with_suffix(".pdf")
    return str(candidate) if candidate.exists() else ""


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))
