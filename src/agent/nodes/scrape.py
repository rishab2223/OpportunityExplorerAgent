from __future__ import annotations

import traceback

from src.agent.nodes.resume import _fail
from src.agent.state import AgentState
from src.config import AppConfig, EnvSettings
from src.errors import StepError
from src.scrape import scrape_jobs


def node_scrape(state: AgentState, cfg: AppConfig, env: EnvSettings) -> AgentState:
    try:
        jobs = scrape_jobs(cfg, env)
        state["raw_jobs"] = [j.model_dump() for j in jobs]
    except StepError as exc:
        return _fail(state, exc)
    except Exception as exc:
        return _fail(
            state,
            StepError(
                "scrape",
                str(exc),
                detail=traceback.format_exc()[-2000:],
                what_happened="Scrape jobs failed. Later steps were not run.",
            ),
        )
    return state
