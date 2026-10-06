from __future__ import annotations

import time
import traceback

from src import llm_cache, progress
from src.agent.nodes.resume import _fail
from src.agent.parallel import Counter, run_pool, twins
from src.agent.state import AgentState
from src.config import AppConfig, EnvSettings
from src.errors import StepError
from src.llm import Invoker, describe_provider, make_invoker
from src.models import MatchEnrichment, MatchRecord, ScoredJob, TexEnrichment
from src.resume import fit_policy
from src.resume.latex_sections import (
    MAX_LENGTH_RATIO,
    EditResult,
    ParsedResume,
    split_sections,
    tailor_latex,
)

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
    "output. Assume the source already fills its page exactly, because it "
    "does: every line you add must be paid for by a line you remove, so "
    "prefer rephrasing and reordering to adding, and keep each replacement "
    "no longer than the section it replaces. Know the cost of added length: "
    "{fit} {prep}"
    "In resume_edit_suggestions, list the "
    "concrete changes you made (what moved, what was rephrased, and why) so "
    "the candidate can cross-check the .tex. Do not invent facts there either."
)
PREP_ASK = ("Also provide interview prep: likely questions, STAR talking points, "
            "and questions to ask them. ")
# Interview prep was about 60% of what this call wrote, and so most of its
# time. Asked for later, for the jobs the candidate actually opens.
PREP_LATER = "Leave interview_prep empty; it is written separately. "

PREP_SYSTEM = (
    "You help a candidate prepare for an interview for a specific job. Using "
    "their resume and the job description, write interview prep: likely "
    "questions (technical and behavioural) with STAR talking points drawn "
    "only from the resume, the gaps they should be ready to address, and "
    "good questions to ask the interviewer. Do not invent facts. Plain text "
    "with short headings; put it all in interview_prep and leave "
    "resume_edit_suggestions empty."
)

# Bumped whenever the prompts change, so a cached result from an older
# prompt is never reused.
PROMPT_VERSION = "3"


def tex_system(with_prep: bool) -> str:
    # What happens on overflow is the candidate's own fit policy (Resume fit
    # tab), or the old fixed order until they agree one.
    return (ENRICH_TEX_SYSTEM.replace("{fit}", fit_policy.prompt_text())
            .replace("{prep}", PREP_ASK if with_prep else PREP_LATER))


def pdf_system(with_prep: bool) -> str:
    if with_prep:
        return ENRICH_PDF_SYSTEM
    return ("You help a candidate prepare for a specific job. Give truth-preserving "
            "resume edit suggestions (do not invent employers or dates). " + PREP_LATER)


def write_prep(invoke: Invoker, resume: str, job: ScoredJob, cache_key: str = "") -> str:
    """Interview prep for one job, on demand (Job info > Write interview prep).
    Kept under `cache_key` when one is given, so the same posting in a later
    run does not cost the call again."""
    cached = llm_cache.get("prep", cache_key) if cache_key else None
    if cached and cached.get("interview_prep"):
        return str(cached["interview_prep"])
    jd = (job.description or "")[:8000]
    result = invoke(PREP_SYSTEM, _pdf_user(resume, job, jd), MatchEnrichment)
    prep = result.interview_prep.strip()
    if prep and cache_key:
        llm_cache.put("prep", cache_key, {"interview_prep": prep})
    return prep


def _job_block(job: ScoredJob, jd: str) -> str:
    return (
        f"JOB TITLE: {job.title}\nCOMPANY: {job.company}\n"
        f"LOCATION: {job.location}\nAPPLY: {job.apply_url}\n\n"
        f"JOB DESCRIPTION:\n{jd}"
    )


def _tex_user(source_latex: str, headings: list[str], job: ScoredJob, jd: str) -> str:
    # The length cap used to appear only in the retry message, after a 95s
    # call had been wasted on a 1.2x rewrite; now the model is told the number.
    size = len(source_latex)
    return (
        f"RESUME_LATEX ({size} characters; the whole document must stay under "
        f"{int(size * MAX_LENGTH_RATIO)} characters after your edits, and ideally under {size}):\n"
        f"{source_latex}\n\n"
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
    system: str,
) -> tuple[TexEnrichment, EditResult]:
    """One structured call, plus one retry with the violations named if edits were rejected."""
    user = _tex_user(source_latex, headings, job, jd)
    result = invoke(system, user, TexEnrichment)
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
    retry = invoke(system, retry_user, TexEnrichment)
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
    with_prep = cfg.interview_prep == "run"
    tex_prompt, pdf_prompt = tex_system(with_prep), pdf_system(with_prep)
    try:
        invoke = make_invoker(cfg, env, "enrich")
        provider = describe_provider(cfg, "enrich")
        copies = twins(raw_matches)
        unique = [i for i in range(len(raw_matches)) if i not in copies]
        total = len(unique)
        workers = max(1, min(cfg.openai.enrich_concurrency, total))
        started = time.monotonic()
        progress.log(
            f"[enrich] Enriching {total} match(es) with {workers} worker(s) via {provider}"
            + (f"; {len(copies)} more are the same posting in another city and share the result"
               if copies else "")
            + ("" if with_prep else "; interview prep on demand")
            + "…"
        )
        resume_key = source_latex if tex_mode else resume
        reused = Counter()
        done = Counter()
        # key per job id, for dump: a result whose PDF could not be made to
        # fit one page is dropped from the cache so the next run tries again.
        keys: dict[str, str] = {}
        if cfg.reuse_enrichment:
            llm_cache.prune()

        def cache_key(job: ScoredJob) -> str:
            # Everything the answer depends on: the prompts (which carry the
            # fit policy and the prep mode), the model, the resume, the job.
            return llm_cache.key(PROMPT_VERSION, provider, tex_prompt if tex_mode else pdf_prompt,
                                 resume_key, job.title, job.company, job.location,
                                 job.description or "")

        def enrich_one(item: dict, tag: str) -> MatchRecord:
            job = ScoredJob.model_validate(item)
            key = cache_key(job) if cfg.reuse_enrichment else ""
            if key:
                keys[job.job_id] = key
                cached = llm_cache.get("enrich", key)
                if cached is not None:
                    try:
                        rec = MatchRecord(**job.model_dump(), **cached,
                                          resume_source=source, resume_source_detail=detail)
                        reused.bump()
                        return rec
                    except (TypeError, ValueError):
                        pass   # an old row: make the call
            try:
                rec, clean = _enrich_job(job, tag)
            except Exception as exc:  # one bad call must not sink the whole shortlist
                progress.log(f"[enrich] {tag}  failed: {str(exc)[:300]}")
                return MatchRecord(
                    **job.model_dump(),
                    latex_skip_reason="error: " + str(exc)[:500],
                    resume_source=source,
                    resume_source_detail=detail,
                )
            # Only a clean result is kept: one with a rejected edit is worth
            # another attempt next time, not a repeat.
            if key and clean:
                llm_cache.put("enrich", key, {
                    "resume_edit_suggestions": rec.resume_edit_suggestions,
                    "interview_prep": rec.interview_prep,
                    "resume_latex": rec.resume_latex,
                })
            return rec

        def _enrich_job(job: ScoredJob, tag: str) -> tuple[MatchRecord, bool]:
            jd = (job.description or "")[:8000]
            skip_reason = ""
            latex_out = ""
            clean = True
            if tex_mode:
                result, edited = _enrich_tex(
                    invoke, source_latex, parsed, headings, job, jd, tag, tex_prompt
                )
                latex_out = edited.latex
                suggestions = result.resume_edit_suggestions
                clean = not edited.problems and edited.applied > 0
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
                result = invoke(pdf_prompt, _pdf_user(resume, job, jd), MatchEnrichment)
                suggestions = result.resume_edit_suggestions
            return MatchRecord(
                **job.model_dump(),
                resume_edit_suggestions=suggestions,
                interview_prep=result.interview_prep,
                resume_latex=latex_out,
                latex_skip_reason=skip_reason,
                resume_source=source,
                resume_source_detail=detail,
            ), clean

        for n, index in enumerate(unique, start=1):
            item = raw_matches[index]
            label = f"{item.get('company', '')}  {item.get('title', '')}".strip()
            progress.log(f"[enrich] {n}/{total}  {label or item.get('job_id', '')}")

        def report(_index: int, rec: MatchRecord) -> None:
            note = "latex" if rec.resume_latex else (rec.latex_skip_reason or "suggestions")
            progress.log(f"[enrich] done {done.bump()}/{total}  {note}  {rec.company}  {rec.title}")

        worked = run_pool([raw_matches[i] for i in unique], enrich_one, workers, report)
        # Results keep the shortlist order; a copy takes its first's result,
        # whatever that was (a failed first means a failed copy, counted once).
        results: list[MatchRecord | None] = [None] * len(raw_matches)
        for index, rec in zip(unique, worked):
            results[index] = rec
        for index, first in copies.items():
            twin = results[first]
            job = ScoredJob.model_validate(raw_matches[index])
            results[index] = MatchRecord(
                **job.model_dump(),
                resume_edit_suggestions=twin.resume_edit_suggestions,
                interview_prep=twin.interview_prep,
                resume_latex=twin.resume_latex,
                latex_skip_reason=twin.latex_skip_reason,
                resume_source=source,
                resume_source_detail=detail,
            )
            if twin.job_id in keys:
                keys[job.job_id] = keys[twin.job_id]
        elapsed = time.monotonic() - started
        records = [m for m in results if m is not None]
        failed = [m for m in worked if m.latex_skip_reason.startswith("error: ")]
        progress.log(
            f"[enrich] Done {total} match(es) in {elapsed:.0f}s"
            + (f", {reused.value} reused from an earlier run" if reused.value else "")
            + (f" ({len(failed)} failed, kept with score only)" if failed else "")
        )
        if worked and len(failed) == len(worked):
            raise RuntimeError(
                f"all {len(worked)} enrichment calls failed; first error: "
                + failed[0].latex_skip_reason[len("error: "):]
            )
        state["matches"] = [m.model_dump() for m in records]
        state["enrich_keys"] = keys
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
