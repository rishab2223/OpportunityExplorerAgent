from __future__ import annotations

import threading
import time
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed
from src import progress
from src.agent.nodes.resume import _fail
from src.agent.state import AgentState
from src.config import AppConfig, EnvSettings
from src.errors import StepError
from src.llm import Invoker, describe_provider, make_invoker
from src.models import MatchEnrichment, MatchRecord, ScoredJob, TexEnrichment
from src.resume.latex_sections import EditResult, ParsedResume, split_sections, tailor_latex

ENRICH_PDF_SYSTEM = (
    "You help a candidate prepare for a specific job. "
    "Give truth-preserving resume edit suggestions (do not invent employers or dates) "
    "and interview prep: likely questions, STAR talking points, and questions to ask them."
)

ENRICH_TEX_SYSTEM = (
    "You help a candidate prepare for a specific job. The resume is LaTeX "
    "source. Tailor it by returning section_edits: replacement bodies for ONLY "
    "the sections worth changing for this job (typically the summary and "
    "skills; reorder or reword experience bullets when it clearly helps). "
    "Each edit has heading, copied exactly from the SECTIONS list, and "
    "latex_body, the complete replacement for everything between that "
    "\\section line and the next one, as raw LaTeX with no markdown fences. "
    "Rules: never drop a section (a section you do not list stays unchanged); "
    "reuse the source's own environments, macros, and spacing exactly (the "
    "same \\begin{itemize}[...] options, \\vspace, \\newline, \\hfill layout); "
    "a latex_body must not contain \\section, \\documentclass, \\usepackage, "
    "or \\begin{document}; do not invent employers, dates, titles, metrics, or "
    "skills. The resume must stay one page, and you cannot see the rendered "
    "output, so prefer rephrasing and reordering over adding length - but DO "
    "add genuinely valuable content for this job when you see it. Know the "
    "cost of added length: if the page overflows, content is dropped "
    "automatically in this priority order - first the CCNA certification "
    "bullet, then the whole Certifications section. So only add what is worth "
    "more for this job than those certifications, and never pad. Also provide "
    "interview prep: likely questions, STAR talking "
    "points, and questions to ask them. In resume_edit_suggestions, list the "
    "concrete changes you made (what moved, what was rephrased, and why) so "
    "the candidate can cross-check the .tex. Do not invent facts there either."
)


def _job_block(job: ScoredJob, jd: str) -> str:
    return (
        f"JOB TITLE: {job.title}\nCOMPANY: {job.company}\n"
        f"LOCATION: {job.location}\nAPPLY: {job.apply_url}\n\n"
        f"JOB DESCRIPTION:\n{jd}"
    )


def _tex_user(source_latex: str, headings: list[str], job: ScoredJob, jd: str) -> str:
    return (
        f"RESUME_LATEX:\n{source_latex}\n\n"
        f"SECTIONS: {' | '.join(headings)}\n\n" + _job_block(job, jd)
    )


def _pdf_user(resume: str, job: ScoredJob, jd: str) -> str:
    return f"RESUME:\n{resume[:8000]}\n\n" + _job_block(job, jd)


def _tailor(source_latex: str, parsed: ParsedResume, result: TexEnrichment) -> EditResult:
    edits = [(e.heading, e.latex_body) for e in result.section_edits]
    return tailor_latex(source_latex, edits, parsed)


def _enrich_tex(
    invoke: Invoker,
    source_latex: str,
    parsed: ParsedResume,
    headings: list[str],
    job: ScoredJob,
    jd: str,
    tag: str,
) -> tuple[TexEnrichment, EditResult]:
    """One structured call, plus one retry with the violations named if edits were rejected."""
    user = _tex_user(source_latex, headings, job, jd)
    result = invoke(ENRICH_TEX_SYSTEM, user, TexEnrichment)
    edited = _tailor(source_latex, parsed, result)
    if not edited.problems:
        return result, edited
    retry_user = (
        user
        + "\n\nYour previous section_edits were rejected for these reasons:\n- "
        + "\n- ".join(edited.problems)
        + "\nReturn corrected section_edits following the rules above."
    )
    progress.log(f"[enrich] {tag}  retrying: " + "; ".join(edited.problems)[:300])
    retry = invoke(ENRICH_TEX_SYSTEM, retry_user, TexEnrichment)
    redone = _tailor(source_latex, parsed, retry)
    # Keep whichever attempt applied more edits with fewer problems.
    if (redone.applied, -len(redone.problems)) > (edited.applied, -len(edited.problems)):
        return retry, redone
    return result, edited


def node_enrich(state: AgentState, cfg: AppConfig, env: EnvSettings) -> AgentState:
    raw_matches = state.get("matches") or []
    if not raw_matches:
        return state
    if cfg.enrich_provider == "openai" and not env.openai_api_key:
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
    parsed = split_sections(source_latex) if tex_mode else None
    if tex_mode and not parsed.sections:
        progress.log(
            "[enrich] No \\section headings found in the resume .tex; "
            "falling back to text suggestions"
        )
        tex_mode = False
    headings = [s.heading for s in parsed.sections] if tex_mode else []
    try:
        invoke = make_invoker(cfg, env, "enrich")
        total = len(raw_matches)
        workers = max(1, min(cfg.openai.enrich_concurrency, total))
        started = time.monotonic()
        progress.log(
            f"[enrich] Enriching {total} match(es) with {workers} worker(s) via {describe_provider(cfg, 'enrich')}…"
        )

        def enrich_one(item: dict, tag: str) -> MatchRecord:
            job = ScoredJob.model_validate(item)
            try:
                return _enrich_job(job, tag)
            except Exception as exc:  # one bad call must not sink the whole shortlist
                progress.log(f"[enrich] {tag}  failed: {str(exc)[:300]}")
                return MatchRecord(
                    **job.model_dump(),
                    latex_skip_reason="error: " + str(exc)[:500],
                    resume_source=source,
                    resume_source_detail=detail,
                )

        def _enrich_job(job: ScoredJob, tag: str) -> MatchRecord:
            jd = (job.description or "")[:8000]
            skip_reason = ""
            latex_out = ""
            if tex_mode:
                result, edited = _enrich_tex(
                    invoke, source_latex, parsed, headings, job, jd, tag
                )
                latex_out = edited.latex
                suggestions = result.resume_edit_suggestions
                if not edited.applied:
                    skip_reason = (
                        "rejected: " + "; ".join(edited.problems)[:500]
                        if edited.problems
                        else "no_section_edits_returned"
                    )
                elif edited.problems:
                    suggestions += (
                        "\n\n[pipeline] Some edits were rejected and the original "
                        "text was kept for those sections: "
                        + "; ".join(edited.problems)
                    )
            else:
                result = invoke(ENRICH_PDF_SYSTEM, _pdf_user(resume, job, jd), MatchEnrichment)
                suggestions = result.resume_edit_suggestions
            return MatchRecord(
                **job.model_dump(),
                resume_edit_suggestions=suggestions,
                interview_prep=result.interview_prep,
                resume_latex=latex_out,
                latex_skip_reason=skip_reason,
                resume_source=source,
                resume_source_detail=detail,
            )

        # Results keep the shortlist order regardless of completion order.
        results: list[MatchRecord | None] = [None] * total
        done = 0
        counter_lock = threading.Lock()
        pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="enrich")
        try:
            futures = {}
            for i, item in enumerate(raw_matches, start=1):
                label = f"{item.get('company', '')}  {item.get('title', '')}".strip()
                tag = f"{i}/{total}"
                progress.log(f"[enrich] {tag}  {label or item.get('job_id', '')}")
                futures[pool.submit(enrich_one, item, tag)] = i - 1
            for future in as_completed(futures):
                i = futures[future]
                rec = future.result()
                results[i] = rec
                with counter_lock:
                    done += 1
                    n = done
                note = "latex" if rec.resume_latex else (rec.latex_skip_reason or "suggestions")
                progress.log(f"[enrich] done {n}/{total}  {note}  {rec.company}  {rec.title}")
        finally:
            pool.shutdown(wait=True, cancel_futures=True)
        elapsed = time.monotonic() - started
        records = [m for m in results if m is not None]
        failed = [m for m in records if m.latex_skip_reason.startswith("error: ")]
        progress.log(
            f"[enrich] Done {total} match(es) in {elapsed:.0f}s"
            + (f" ({len(failed)} failed, kept with score only)" if failed else "")
        )
        if records and len(failed) == len(records):
            raise RuntimeError(
                f"all {len(records)} enrichment calls failed; first error: "
                + failed[0].latex_skip_reason[len("error: "):]
            )
        state["matches"] = [m.model_dump() for m in records]
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
