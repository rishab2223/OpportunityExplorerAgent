from __future__ import annotations

import re
import time
import traceback

from langchain_openai import ChatOpenAI

from src import progress
from src.agent.nodes.resume import _fail
from src.agent.state import AgentState
from src.config import AppConfig, EnvSettings
from src.errors import StepError
from src.models import MatchEnrichment, MatchRecord, ScoredJob

ENRICH_PDF_SYSTEM = (
    "You help a candidate prepare for a specific job. "
    "Give truth-preserving resume edit suggestions (do not invent employers or dates) "
    "and interview prep: likely questions, STAR talking points, and questions to ask them."
)

ENRICH_TEX_SYSTEM = (
    "You help a candidate prepare for a specific job. "
    "Return a complete, compilable one-page LaTeX resume with JD-specific edits "
    "already applied: reorder, rephrase, and retarget the summary and skills. "
    "Do not invent employers, dates, titles, metrics, or skills. "
    "Preserve the document class, packages, and macros unless a change is required "
    "to compile. Keep the resume to one page; do not add length or sections that "
    "would overflow. Also provide interview prep: likely questions, STAR talking "
    "points, and questions to ask them. "
    "Put only raw LaTeX in resume_latex (no markdown, no code fences). "
    "In resume_edit_suggestions, list the concrete changes you made to the source "
    "resume (what moved, what was rephrased, what was omitted, and why) so the "
    "candidate can cross-check the .tex. Do not invent facts there either."
)

_FENCE_OPEN = re.compile(r"^```(?:latex|tex)?\s*", re.IGNORECASE)
_FENCE_CLOSE = re.compile(r"\s*```$")


def _strip_latex_fences(text: str) -> str:
    s = (text or "").strip()
    if s.startswith("```"):
        s = _FENCE_OPEN.sub("", s, count=1)
        s = _FENCE_CLOSE.sub("", s)
    return s.strip()


def _valid_latex(text: str) -> bool:
    return "\\documentclass" in text and "\\end{document}" in text


def node_enrich(state: AgentState, cfg: AppConfig, env: EnvSettings) -> AgentState:
    raw_matches = state.get("matches") or []
    if not raw_matches:
        return state
    if not env.openai_api_key:
        return _fail(
            state,
            StepError(
                "enrich",
                "OPENAI_API_KEY is not set",
                what_happened="Interview prep & resume suggestions failed. Later steps were not run.",
            ),
        )
    resume = state.get("resume_text") or ""
    source_latex = state.get("resume_latex") or ""
    source = state.get("resume_source") or ""
    detail = state.get("resume_source_detail") or ""
    tex_mode = bool(source_latex.strip())
    out: list[MatchRecord] = []
    try:
        llm = ChatOpenAI(
            model=cfg.openai.enrich_model,
            api_key=env.openai_api_key or None,
            temperature=0.2,
        ).with_structured_output(MatchEnrichment)
        total = len(raw_matches)
        started = time.monotonic()
        progress.log(f"[enrich] Enriching {total} match(es)…")
        for i, item in enumerate(raw_matches, start=1):
            job = ScoredJob.model_validate(item)
            label = f"{job.company}  {job.title}".strip() or job.job_id
            progress.log(f"[enrich] {i}/{total}  {label}")
            jd = (job.description or "")[:8000]
            if tex_mode:
                prompt = (
                    f"{ENRICH_TEX_SYSTEM}\n\nRESUME_LATEX:\n{source_latex}\n\n"
                    f"JOB TITLE: {job.title}\nCOMPANY: {job.company}\n"
                    f"LOCATION: {job.location}\nAPPLY: {job.apply_url}\n\n"
                    f"JOB DESCRIPTION:\n{jd}"
                )
            else:
                prompt = (
                    f"{ENRICH_PDF_SYSTEM}\n\nRESUME:\n{resume[:8000]}\n\n"
                    f"JOB TITLE: {job.title}\nCOMPANY: {job.company}\n"
                    f"LOCATION: {job.location}\nAPPLY: {job.apply_url}\n\n"
                    f"JOB DESCRIPTION:\n{jd}"
                )
            result = llm.invoke(prompt)
            if not isinstance(result, MatchEnrichment):
                result = MatchEnrichment.model_validate(result)
            skip_reason = ""
            latex_out = ""
            suggestions = result.resume_edit_suggestions
            if tex_mode:
                cleaned = _strip_latex_fences(result.resume_latex)
                if _valid_latex(cleaned):
                    latex_out = cleaned
                else:
                    skip_reason = "missing_documentclass_or_end"
            out.append(
                MatchRecord(
                    **job.model_dump(),
                    resume_edit_suggestions=suggestions,
                    interview_prep=result.interview_prep,
                    resume_latex=latex_out,
                    latex_skip_reason=skip_reason,
                    resume_source=source,
                    resume_source_detail=detail,
                )
            )
            detail_note = "latex" if latex_out else (
                skip_reason if skip_reason else "suggestions"
            )
            progress.log(f"[enrich] {i}/{total}  {detail_note}")
        elapsed = time.monotonic() - started
        progress.log(f"[enrich] Done {total} match(es) in {elapsed:.0f}s")
        state["matches"] = [m.model_dump() for m in out]
    except Exception as exc:
        return _fail(
            state,
            StepError(
                "enrich",
                str(exc),
                detail=traceback.format_exc()[-2000:],
                what_happened=(
                    "Interview prep & resume suggestions failed. Later steps were not run."
                ),
            ),
        )
    return state
