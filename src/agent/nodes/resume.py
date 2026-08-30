from __future__ import annotations

import traceback

from src.agent.state import AgentState
from src.config import AppConfig, EnvSettings
from src.errors import StepError
from src.resume.loader import load_resume


def node_load_resume(state: AgentState, cfg: AppConfig, _env: EnvSettings) -> AgentState:
    try:
        text, source, detail, latex = load_resume(cfg)
    except StepError as exc:
        return _fail(state, exc)
    except Exception as exc:
        return _fail(
            state,
            StepError(
                "load_resume",
                str(exc),
                detail=traceback.format_exc()[-2000:],
                what_happened="Load resume failed. Later steps were not run.",
            ),
        )
    state["resume_text"] = text
    state["resume_latex"] = latex
    state["resume_source"] = source
    state["resume_source_detail"] = detail
    return state


def _fail(state: AgentState, exc: StepError) -> AgentState:
    state["failed_step"] = exc.step_name
    state["error_message"] = exc.message
    state["error_detail"] = (exc.detail or traceback.format_exc())[-2000:]
    state["fallbacks_tried"] = exc.fallbacks
    state["what_happened"] = exc.what_happened
    return state
