from __future__ import annotations

from typing import Any, TypedDict


class AgentState(TypedDict, total=False):
    resume_text: str
    resume_latex: str
    resume_source: str
    resume_source_detail: str
    raw_jobs: list[dict[str, Any]]
    scored: list[dict[str, Any]]
    matches: list[dict[str, Any]]
    run_dir: str
    run_output_path: str
    shortlisted_path: str
    resume_tex_count: int
    resume_pdf_count: int
    failed_step: str
    error_message: str
    error_detail: str
    fallbacks_tried: str
    what_happened: str
    run_timestamp: str
