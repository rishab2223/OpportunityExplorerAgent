from __future__ import annotations

import time
import traceback

from langchain_openai import ChatOpenAI
from pydantic import BaseModel, Field

from src import llm_cache, progress
from src.agent.nodes.resume import _fail
from src.agent.parallel import Counter, run_pool, twins
from src.agent.state import AgentState
from src.config import AppConfig, EnvSettings
from src.errors import StepError
from src.models import JobPosting, JobScore, ScoredJob

# The score is out of 100, the sum of six parts the model marks one by one
# against fixed anchors. A single 1-10 number from a one-line prompt put 96 of
# 104 shortlisted jobs at 7 or 8 and none at 10 (Oct 2026): it did not rank.
# (name, most points, what full and partial marks mean)
PARTS: tuple[tuple[str, int, str], ...] = (
    ("skills", 35,
     "the job's REQUIRED skills found on the resume with real use. 35: every required skill is "
     "there; 28: all the core ones, only nice-to-haves missing; 20: the main language or platform "
     "matches but two or three required skills are missing; 10: an adjacent stack; 0: unrelated"),
    ("experience", 20,
     "years and seniority asked against the resume's. 20: within the candidate's years and level; "
     "14: one level or 1-2 years off; 6: clearly more senior or junior; 0: far off"),
    ("role", 15,
     "the kind of work. 15: the same kind of engineering the resume shows most of; 10: close "
     "(full-stack leaning the same way, a platform role for a backend engineer); 5: a different "
     "track (frontend only, mobile, QA, data, support); 0: not an engineering role"),
    ("stack", 10,
     "depth of overlap in the domain and tools beyond the required skills: the frameworks, cloud, "
     "architecture (event-driven, APIs, distributed systems) and domain the job names. 10: deep; "
     "5: some; 0: none"),
    ("company", 10,
     "the employer. 9-10: a product company, an engineering centre of a global company, or a "
     "funded startup building its own product; 6: unclear from the posting; 3-4: IT services, "
     "consulting, or a staffing agency hiring for a client"),
    ("location", 10,
     "whether the candidate can take the job where it is. 10: remote, or in the candidate's "
     "country (the resume says where they are); 6: in that country but on-site far away with no "
     "relocation help mentioned; 2: abroad, needing a visa or relocation the posting does not offer"),
)

# Each part in a few words, for the (i) beside the score in the run settings
# popup - the same parts and points the model marks.
PART_SUMMARIES = {
    "skills": "The job's required skills found on your resume, with real use",
    "experience": "Years and seniority asked, against yours",
    "role": "The kind of work: the same kind your resume shows most of",
    "stack": "Overlap in frameworks, cloud, architecture and domain",
    "company": "Product company, global engineering centre or funded startup above IT services or staffing",
    "location": "Whether you can take the job where it is (remote or in your country is full marks)",
}

SCORE_SYSTEM = (
    "You score how well a job posting fits a candidate's resume, out of 100, by marking six "
    "parts one at a time against the anchors below. Mark each part on its own evidence, from "
    "the posting and the resume only - never invent a skill or a year. Use the whole range: "
    "a strong fit must reach 80-100, a weak one 30 or less. Give each part a one-line reason, "
    "then a short why_score, the candidate's strengths for this job, and the gaps.\n"
    + "\n".join(f"- {name} (0-{most}): {anchors}" for name, most, anchors in PARTS)
    + "\nDo not suggest resume edits or interview questions."
)


class ScoreCard(BaseModel):
    """The model's marks. No bounds in the schema (strict structured output
    does not take them everywhere): out-of-range marks are clamped in code."""
    skills: int = 0
    skills_why: str = ""
    experience: int = 0
    experience_why: str = ""
    role: int = 0
    role_why: str = ""
    stack: int = 0
    stack_why: str = ""
    company: int = 0
    company_why: str = ""
    location: int = 0
    location_why: str = ""
    why_score: str = ""
    strengths: list[str] = Field(default_factory=list)
    gaps: list[str] = Field(default_factory=list)


def to_score(card: ScoreCard) -> JobScore:
    """The marks added up: the score is the sum, each part held to its range."""
    parts = {name: max(0, min(most, int(getattr(card, name) or 0))) for name, most, _ in PARTS}
    return JobScore(relevance=sum(parts.values()), why_score=card.why_score,
                    strengths=card.strengths, gaps=card.gaps, score_scale=100, score_parts=parts)


# One retry for a call that fails, then the job is kept unscored.
RETRY_AFTER_S = 3
# Seconds one scoring call may take before it counts as failed.
CALL_TIMEOUT_S = 60


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
        # A timeout: without one, a call the provider never answered held the
        # whole step - a re-score of the saved runs sat 30 minutes on a few
        # second looks with 15 connections open and no progress (Oct 2026).
        models = {name: ChatOpenAI(model=name, api_key=env.openai_api_key or None, temperature=0,
                                   timeout=CALL_TIMEOUT_S, max_retries=2)
                  .with_structured_output(ScoreCard)
                  for name in {cfg.openai.score_model, cfg.openai.second_look_model}}
        # The same posting in another city gets the same score.
        copies = twins(raw_jobs)
        unique = [i for i in range(len(jobs)) if i not in copies]
        total = len(unique)
        workers = max(1, min(cfg.openai.score_concurrency, total))
        started = time.monotonic()
        progress.log(
            f"[score] Scoring {total} job(s) out of 100 with {workers} worker(s)"
            + (f"; {len(copies)} more are the same posting in another city" if copies else "")
            + "…")
        reused = Counter()
        done = Counter()
        failed = Counter()

        def mark(job: JobPosting, model: str) -> JobScore:
            score = score_job(models[model], model, job, resume, cfg.reuse_enrichment, reused)
            if score is None:
                failed.bump()
                # Kept, not dropped: a job the call could not mark may be the
                # best one of the run.
                return JobScore(relevance=cfg.min_score, score_scale=100, score_failed=True,
                                why_score="Could not be scored (the call failed twice). Kept so a "
                                          "good job is not lost: check it yourself.")
            return score

        def report(index: int, result: JobScore) -> None:
            job = jobs[unique[index]]
            label = f"{job.company}  {job.title}".strip() or job.job_id
            progress.log(f"[score] {done.bump()}/{total}  relevance={result.relevance}/100  {label}")

        worked = run_pool([jobs[i] for i in unique], lambda job, _tag: mark(job, cfg.openai.score_model),
                          workers, report)

        # A second look, by the stronger model, for a job just under the bar.
        # Kept if either look reaches it; only this band pays for the call.
        band = [n for n, s in enumerate(worked)
                if not s.score_failed and 0 < cfg.min_score - s.relevance <= cfg.second_look_below]
        if band:
            look_started = time.monotonic()
            progress.log(f"[score] Second look at {len(band)} job(s) scoring "
                         f"{cfg.min_score - cfg.second_look_below}-{cfg.min_score - 1} "
                         f"with {cfg.openai.second_look_model}…")
            again = run_pool([jobs[unique[n]] for n in band],
                             lambda job, _tag: mark(job, cfg.openai.second_look_model),
                             max(1, min(cfg.openai.score_concurrency, len(band))))
            kept = 0
            for n, second in zip(band, again):
                first = worked[n]
                if not second.score_failed and second.relevance >= cfg.min_score:
                    kept += 1
                    second.second_look = (f"First look {first.relevance}, second look "
                                          f"{second.relevance} by {cfg.openai.second_look_model}: kept.")
                    worked[n] = second
                else:
                    first.second_look = (f"Second look by {cfg.openai.second_look_model}: "
                                         f"{'failed' if second.score_failed else second.relevance}.")
            progress.log(f"[score] Second look kept {kept} of {len(band)} "
                         f"in {time.monotonic() - look_started:.0f}s")

        scores: list[JobScore | None] = [None] * len(jobs)
        for index, result in zip(unique, worked):
            scores[index] = result
        for index, first in copies.items():
            scores[index] = scores[first]
        elapsed = time.monotonic() - started
        progress.log(f"[score] Done {total} job(s) in {elapsed:.0f}s"
                     + (f", {reused.value} reused from an earlier run" if reused.value else "")
                     + (f"; {failed.value} could not be scored and were kept to check by hand"
                        if failed.value else ""))
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


def score_prompt(job: JobPosting, resume: str) -> str:
    # The whole posting, up to a generous cap: requirements are often at the
    # end, after the company blurb, and a 6000-character cut lost them.
    jd = (job.description or "")[:14000]
    # Location left out of the prompt's header: the posting's own text says
    # where the job is, and a city field made every city a cache miss.
    return (
        f"RESUME:\n{resume[:10000]}\n\n"
        f"JOB TITLE: {job.title}\nCOMPANY: {job.company}\n\n"
        f"JOB DESCRIPTION:\n{jd}"
    )


def score_job(llm, model: str, job: JobPosting, resume: str, reuse: bool,
              reused: Counter | None = None) -> JobScore | None:
    """One job's score, or None when the call failed twice."""
    prompt = f"{SCORE_SYSTEM}\n\n{score_prompt(job, resume)}"
    # The same posting against the same resume and model scores the same:
    # yesterday's run already paid for it.
    key = llm_cache.key(model, prompt) if reuse else ""
    cached = llm_cache.get("score", key) if key else None
    if cached is not None:
        try:
            result = JobScore.model_validate(cached)
            if result.score_scale == 100:
                if reused is not None:
                    reused.bump()
                return result
        except ValueError:
            pass
    for attempt in range(2):
        try:
            card = llm.invoke(prompt)
            if not isinstance(card, ScoreCard):
                card = ScoreCard.model_validate(card)
            result = to_score(card)
            break
        except Exception as exc:
            if attempt == 0:
                progress.log(f"[score] {job.company} {job.title}: the call failed "
                             f"({str(exc).splitlines()[0][:120]}); trying once more")
                time.sleep(RETRY_AFTER_S)
    else:
        return None
    if key:
        llm_cache.put("score", key, result.model_dump())
    return result


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
        bar = rule.review_min_score or 100
        strong = sorted((j for j in marked if int(j.get("relevance") or 0) >= bar),
                        key=lambda j: -int(j.get("relevance") or 0))
        held = strong[:rule.review_max]
        progress.log(
            f"[filter] {len(held)} of {len(marked)} held-back job(s) scored {bar} or more"
            + (f" (showing the top {rule.review_max})" if len(strong) > len(held) else "")
            + (" - see Held back" if held else ""))
    state["held_back"] = held
    return state
