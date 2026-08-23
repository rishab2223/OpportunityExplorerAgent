from __future__ import annotations

import traceback

from langchain_openai import ChatOpenAI

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
    scored: list[ScoredJob] = []
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
        for job in jobs:
            jd = (job.description or "")[:6000]
            prompt = (
                f"{SCORE_SYSTEM}\n\nRESUME:\n{resume[:8000]}\n\n"
                f"JOB TITLE: {job.title}\nCOMPANY: {job.company}\n"
                f"LOCATION: {job.location}\n\nJOB DESCRIPTION:\n{jd}"
            )
            result = llm.invoke(prompt)
            if not isinstance(result, JobScore):
                result = JobScore.model_validate(result)
            scored.append(ScoredJob(**job.model_dump(), **result.model_dump()))
        state["scored"] = [s.model_dump() for s in scored]
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
    matches = [j for j in scored if int(j.get("relevance") or 0) > min_score]
    state["matches"] = matches
    return state
