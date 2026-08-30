from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path

from src import progress
from src.agent.filenames import unique_tex_name
from src.agent.state import AgentState
from src.config import ROOT, AppConfig
from src.errors import STEP_LABELS
from src.models import MatchRecord
from src.pdf_compile import compile_tex

OUTPUT_DIR = ROOT / "outputs"


def _stamp(run_timestamp: str) -> str:
    raw = run_timestamp or datetime.now(timezone.utc).isoformat()
    return re.sub(r"[^0-9T]", "", raw.replace("+00:00", "Z"))[:15]


def stamp_for(run_timestamp: str) -> str:
    return _stamp(run_timestamp)


def run_dir_for(run_timestamp: str) -> Path:
    return OUTPUT_DIR / _stamp(run_timestamp)


def _compile_pdfs(tex_jobs: list[tuple[MatchRecord, Path]]) -> int:
    """Best effort: a PDF failure is recorded on the record and never fails the run."""
    total = len(tex_jobs)
    compiled = 0
    progress.log(f"[pdf] Compiling {total} resume(s)…")
    for i, (rec, tex_path) in enumerate(tex_jobs, start=1):
        label = f"{rec.company}  {rec.title}".strip() or rec.job_id
        progress.log(f"[pdf] {i}/{total}  {label}")
        pdf_path, error = compile_tex(tex_path)
        if pdf_path is not None:
            rec.resume_pdf_file = pdf_path.name
            rec.resume_pdf_path = str(pdf_path)
            compiled += 1
        else:
            rec.resume_pdf_error = error
            progress.log(f"[pdf] {i}/{total}  failed: {error}")
    progress.log(f"[pdf] Compiled {compiled} of {total}")
    return compiled


def _write_json(path: Path, payload: object) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    return str(path)


def node_dump(state: AgentState, cfg: AppConfig | None = None) -> AgentState:
    ts = state.get("run_timestamp") or datetime.now(timezone.utc).isoformat()
    stamp = _stamp(ts)
    failed_step = state.get("failed_step") or ""
    failed = bool(failed_step)
    raw_matches = state.get("matches") or []
    run_dir = OUTPUT_DIR / stamp
    run_dir.mkdir(parents=True, exist_ok=True)
    state["run_dir"] = str(run_dir)
    compile_pdf = True if cfg is None else bool(cfg.compile_pdf)

    shortlisted_path = ""
    tex_count = 0
    pdf_count = 0
    if not failed:
        now = datetime.now(timezone.utc).isoformat()
        records = []
        used_names: set[str] = set()
        tex_jobs: list[tuple[MatchRecord, Path]] = []
        for item in raw_matches:
            rec = MatchRecord.model_validate(item)
            rec.written_at = now
            body = (rec.resume_latex or "").strip()
            rec.resume_latex = ""
            if body:
                name = unique_tex_name(rec.company, rec.title, rec.job_id, used_names)
                tex_path = run_dir / name
                tex_path.write_text(body, encoding="utf-8")
                rec.resume_tex_file = name
                tex_count += 1
                tex_jobs.append((rec, tex_path))
            records.append(rec)

        if tex_jobs and compile_pdf:
            pdf_count = _compile_pdfs(tex_jobs)
        elif tex_jobs:
            for rec, _ in tex_jobs:
                rec.resume_pdf_error = "compile_pdf disabled in config"

        records = [rec.model_dump() for rec in records]
        state["matches"] = records
        shortlisted_path = _write_json(run_dir / "shortlisted.json", records)
        _write_json(OUTPUT_DIR / "shortlisted.json", records)

    state["resume_tex_count"] = tex_count
    state["resume_pdf_count"] = pdf_count
    run_payload = {
        "status": "failed" if failed else "ok",
        "run_timestamp": ts,
        "run_dir": str(run_dir),
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
        "resume_tex_count": tex_count,
        "resume_pdf_count": pdf_count,
        "shortlisted_path": shortlisted_path,
    }
    run_path = _write_json(run_dir / "run.json", run_payload)
    _write_json(OUTPUT_DIR / "run.json", run_payload)

    state["run_output_path"] = run_path
    state["shortlisted_path"] = shortlisted_path
    return state
