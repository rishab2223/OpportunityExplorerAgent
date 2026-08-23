from __future__ import annotations

import traceback

from langchain_openai import ChatOpenAI

from src.agent.nodes.resume import _fail
from src.agent.state import AgentState
from src.config import AppConfig, EnvSettings
from src.errors import StepError
from src.models import MatchEnrichment, MatchRecord, ScoredJob

ENRICH_SYSTEM = (
    "You help a candidate prepare for a specific job. "
    "Give truth-preserving resume edit suggestions (do not invent employers or dates) "
    "and interview prep: likely questions, STAR talking points, and questions to ask them."
)


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
    source = state.get("resume_source") or ""
    detail = state.get("resume_source_detail") or ""
    out: list[MatchRecord] = []
    try:
        llm = ChatOpenAI(
            model=cfg.openai.enrich_model,
            api_key=env.openai_api_key or None,
            temperature=0.2,
        ).with_structured_output(MatchEnrichment)
        for item in raw_matches:
            job = ScoredJob.model_validate(item)
            jd = (job.description or "")[:8000]
            prompt = (
                f"{ENRICH_SYSTEM}\n\nRESUME:\n{resume[:8000]}\n\n"
                f"JOB TITLE: {job.title}\nCOMPANY: {job.company}\n"
                f"LOCATION: {job.location}\nAPPLY: {job.apply_url}\n\n"
                f"JOB DESCRIPTION:\n{jd}"
            )
            result = llm.invoke(prompt)
            if not isinstance(result, MatchEnrichment):
                result = MatchEnrichment.model_validate(result)
            out.append(
                MatchRecord(
                    **job.model_dump(),
                    **result.model_dump(),
                    resume_source=source,
                    resume_source_detail=detail,
                )
            )
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
