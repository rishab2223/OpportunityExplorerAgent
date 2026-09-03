from __future__ import annotations

import threading
import time
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed

from langchain_openai import ChatOpenAI

from src import progress
from src.agent.nodes.resume import _fail
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
    jobs = [JobPosting.model_validate(j) for j in state.get("raw_jobs") or []]
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
        total = len(jobs)
        workers = max(1, min(cfg.openai.score_concurrency, total))
        started = time.monotonic()
        progress.log(f"[score] Scoring {total} job(s) with {workers} worker(s)…")

        def score_one(job: JobPosting) -> ScoredJob:
            jd = (job.description or "")[:6000]
            prompt = (
                f"{SCORE_SYSTEM}\n\nRESUME:\n{resume[:8000]}\n\n"
                f"JOB TITLE: {job.title}\nCOMPANY: {job.company}\n"
                f"LOCATION: {job.location}\n\nJOB DESCRIPTION:\n{jd}"
            )
            result = llm.invoke(prompt)
            if not isinstance(result, JobScore):
                result = JobScore.model_validate(result)
            return ScoredJob(**job.model_dump(), **result.model_dump())

        # Results keep the scrape order regardless of completion order.
        results: list[ScoredJob | None] = [None] * total
        done = 0
        counter_lock = threading.Lock()
        pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="score")
        try:
            futures = {pool.submit(score_one, job): i for i, job in enumerate(jobs)}
            for future in as_completed(futures):
                i = futures[future]
                scored = future.result()
                results[i] = scored
                with counter_lock:
                    done += 1
                    n = done
                label = f"{scored.company}  {scored.title}".strip() or scored.job_id
                progress.log(f"[score] {n}/{total}  relevance={scored.relevance}  {label}")
        finally:
            # On failure, drop the queued jobs instead of burning calls on them.
            pool.shutdown(wait=True, cancel_futures=True)
        elapsed = time.monotonic() - started
        progress.log(f"[score] Done {total} job(s) in {elapsed:.0f}s")
        state["scored"] = [s.model_dump() for s in results if s is not None]
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
    matches = [j for j in scored if int(j.get("relevance") or 0) >= min_score]
    state["matches"] = matches
    progress.log(f"[filter] {len(matches)} of {len(scored)} at or above min_score={min_score}")
    return state
