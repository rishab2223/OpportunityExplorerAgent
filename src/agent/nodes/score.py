from __future__ import annotations

import time
import traceback

from langchain_openai import ChatOpenAI

from src import llm_cache, progress
from src.agent.nodes.resume import _fail
from src.agent.parallel import Counter, run_pool, twins
from src.agent.state import AgentState
from src.config import AppConfig, EnvSettings
from src.errors import StepError
from src.models import JobPosting, JobScore, ScoredJob

SCORE_SYSTEM = (
    "You score how relevant a job posting is to a candidate resume. "
    "Return relevance from 1 to 10, a short why_score, strengths, and gaps. "
    "Do not suggest resume edits or interview questions."
)


def node_score(state: AgentState, cfg: AppConfig, env: EnvSettings) -> AgentState:
    raw_jobs = state.get("raw_jobs") or []
    jobs = [JobPosting.model_validate(j) for j in raw_jobs]
    resume = state.get("resume_text") or ""
    if not jobs:
        state["scored"] = []
        return state
    if not env.openai_api_key:
        return _fail(
            state,
            StepError(
                "score",
                "OPENAI_API_KEY is not set",
                what_happened="Score jobs failed. Later steps were not run.",
            ),
        )
    try:
        llm = ChatOpenAI(
            model=cfg.openai.score_model,
            api_key=env.openai_api_key or None,
            temperature=0,
        ).with_structured_output(JobScore)
        # The same posting in another city gets the same score.
        copies = twins(raw_jobs)
        unique = [i for i in range(len(jobs)) if i not in copies]
        total = len(unique)
        workers = max(1, min(cfg.openai.score_concurrency, total))
        started = time.monotonic()
        progress.log(
            f"[score] Scoring {total} job(s) with {workers} worker(s)"
            + (f"; {len(copies)} more are the same posting in another city" if copies else "")
            + "…")
        reused = Counter()
        done = Counter()

        def score_one(job: JobPosting, _tag: str) -> JobScore:
            jd = (job.description or "")[:6000]
            # Location left out of the prompt: the score is about the work,
            # and keeping it in made every city a cache miss.
            prompt = (
                f"{SCORE_SYSTEM}\n\nRESUME:\n{resume[:8000]}\n\n"
                f"JOB TITLE: {job.title}\nCOMPANY: {job.company}\n\n"
                f"JOB DESCRIPTION:\n{jd}"
            )
            # The same posting against the same resume and model scores the
            # same: yesterday's run already paid for it.
            key = llm_cache.key(cfg.openai.score_model, prompt) if cfg.reuse_enrichment else ""
            cached = llm_cache.get("score", key) if key else None
            if cached is not None:
                try:
                    result = JobScore.model_validate(cached)
                    reused.bump()
                    return result
                except ValueError:
                    pass
            result = llm.invoke(prompt)
            if not isinstance(result, JobScore):
                result = JobScore.model_validate(result)
            if key:
                llm_cache.put("score", key, result.model_dump())
            return result

        def report(index: int, result: JobScore) -> None:
            job = jobs[unique[index]]
            label = f"{job.company}  {job.title}".strip() or job.job_id
            progress.log(f"[score] {done.bump()}/{total}  relevance={result.relevance}  {label}")

        worked = run_pool([jobs[i] for i in unique], score_one, workers, report)
        scores: list[JobScore | None] = [None] * len(jobs)
        for index, result in zip(unique, worked):
            scores[index] = result
        for index, first in copies.items():
            scores[index] = scores[first]
        elapsed = time.monotonic() - started
        progress.log(f"[score] Done {total} job(s) in {elapsed:.0f}s"
                     + (f", {reused.value} reused from an earlier run" if reused.value else ""))
        state["scored"] = [ScoredJob(**job.model_dump(), **score.model_dump()).model_dump()
                           for job, score in zip(jobs, scores) if score is not None]
    except Exception as exc:
        return _fail(
            state,
            StepError(
                "score",
                str(exc),
                detail=traceback.format_exc()[-2000:],
                what_happened="Score jobs failed. Later steps were not run.",
            ),
        )
    return state


def node_filter(state: AgentState, cfg: AppConfig, _env: EnvSettings) -> AgentState:
    min_score = cfg.min_score
    scored = state.get("scored") or []
    matches = [j for j in scored
               if not j.get("held_back") and int(j.get("relevance") or 0) >= min_score]
    state["matches"] = matches
    progress.log(f"[filter] {len(matches)} of {len(scored)} at or above min_score={min_score}")

    # Jobs a filter in review mode marked: only the strong ones reach the
    # candidate, strongest first and capped, so Held back never turns into
    # a second shortlist. None of them is enriched; that is the costly step.
    marked = [j for j in scored if j.get("held_back")]
    held: list[dict] = []
    if marked:
        rule = cfg.experience
        bar = rule.review_min_score or 10
        strong = sorted((j for j in marked if int(j.get("relevance") or 0) >= bar),
                        key=lambda j: -int(j.get("relevance") or 0))
        held = strong[:rule.review_max]
        progress.log(
            f"[filter] {len(held)} of {len(marked)} held-back job(s) scored {bar} or more"
            + (f" (showing the top {rule.review_max})" if len(strong) > len(held) else "")
            + (" - see Held back" if held else ""))
    state["held_back"] = held
    return state
