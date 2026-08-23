from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path

from src.agent.state import AgentState
from src.config import ROOT
from src.errors import STEP_LABELS
from src.models import MatchRecord

OUTPUT_DIR = ROOT / "outputs"


def _stamp(run_timestamp: str) -> str:
    raw = run_timestamp or datetime.now(timezone.utc).isoformat()
    return re.sub(r"[^0-9T]", "", raw.replace("+00:00", "Z"))[:15]


def _write_json(path: Path, payload: object) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    return str(path)


def node_dump(state: AgentState) -> AgentState:
    ts = state.get("run_timestamp") or datetime.now(timezone.utc).isoformat()
    stamp = _stamp(ts)
    failed_step = state.get("failed_step") or ""
    failed = bool(failed_step)
    raw_matches = state.get("matches") or []

    shortlisted_path = ""
    if not failed:
        now = datetime.now(timezone.utc).isoformat()
        records = []
        for item in raw_matches:
            rec = MatchRecord.model_validate(item)
            rec.written_at = now
            records.append(rec.model_dump())
        state["matches"] = records
        shortlisted_path = _write_json(
            OUTPUT_DIR / f"{stamp}_shortlisted.json",
            records,
        )
        _write_json(OUTPUT_DIR / "shortlisted.json", records)

    run_payload = {
        "status": "failed" if failed else "ok",
        "run_timestamp": ts,
        "failed_step": failed_step,
        "failed_step_label": STEP_LABELS.get(failed_step, failed_step) if failed else "",
        "what_happened": state.get("what_happened") or "",
        "error_message": state.get("error_message") or "",
        "error_detail": state.get("error_detail") or "",
        "fallbacks_tried": state.get("fallbacks_tried") or "",
        "resume_source": state.get("resume_source") or "",
        "resume_source_detail": state.get("resume_source_detail") or "",
        "raw_job_count": len(state.get("raw_jobs") or []),
        "scored_count": len(state.get("scored") or []),
        "match_count": len(state.get("matches") or []),
        "shortlisted_path": shortlisted_path,
    }
    run_path = _write_json(OUTPUT_DIR / f"{stamp}_run.json", run_payload)
    _write_json(OUTPUT_DIR / "run.json", run_payload)

    state["run_output_path"] = run_path
    state["shortlisted_path"] = shortlisted_path
    return state
