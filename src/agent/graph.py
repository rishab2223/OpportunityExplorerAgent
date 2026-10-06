from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Callable

from langgraph.graph import END, StateGraph

from src.agent.nodes.dump import node_dump
from src.agent.nodes.enrich import node_enrich
from src.agent.nodes.resume import node_load_resume
from src.agent.nodes.score import node_filter, node_score
from src.agent.nodes.scrape import node_scrape
from src.agent.state import AgentState
from src.config import AppConfig, EnvSettings


def _failed(state: AgentState) -> bool:
    return bool(state.get("failed_step"))


def _timed(name: str, step: Callable[[AgentState], AgentState]) -> Callable[[AgentState], AgentState]:
    """Record how long a step took in state["timings"] (seconds), for run.json
    and the run settings popup. An hour-long run has to say where the hour went."""
    def run(state: AgentState) -> AgentState:
        started = time.monotonic()
        out = step(state)
        timings = dict(out.get("timings") or {})
        timings[name] = round(time.monotonic() - started, 1)
        out["timings"] = timings
        return out
    return run


def build_graph(cfg: AppConfig, env: EnvSettings):
    graph = StateGraph(AgentState)

    def load_resume(state: AgentState) -> AgentState:
        return node_load_resume(state, cfg, env)

    def scrape(state: AgentState) -> AgentState:
        return node_scrape(state, cfg, env)

    def score(state: AgentState) -> AgentState:
        return node_score(state, cfg, env)

    def filter_matches(state: AgentState) -> AgentState:
        return node_filter(state, cfg, env)

    def enrich(state: AgentState) -> AgentState:
        return node_enrich(state, cfg, env)

    def dump(state: AgentState) -> AgentState:
        return node_dump(state, cfg)

    graph.add_node("load_resume", _timed("load_resume", load_resume))
    graph.add_node("scrape", _timed("scrape", scrape))
    graph.add_node("score", _timed("score", score))
    graph.add_node("filter_matches", _timed("filter_matches", filter_matches))
    graph.add_node("enrich", _timed("enrich", enrich))
    graph.add_node("dump", dump)

    graph.set_entry_point("load_resume")

    graph.add_conditional_edges(
        "load_resume",
        lambda s: "dump" if _failed(s) else "scrape",
        {"dump": "dump", "scrape": "scrape"},
    )
    graph.add_conditional_edges(
        "scrape",
        lambda s: "dump" if _failed(s) else "score",
        {"dump": "dump", "score": "score"},
    )
    graph.add_conditional_edges(
        "score",
        lambda s: "dump" if _failed(s) else "filter_matches",
        {"dump": "dump", "filter_matches": "filter_matches"},
    )

    def after_filter(state: AgentState) -> str:
        if _failed(state):
            return "dump"
        if state.get("matches"):
            return "enrich"
        return "dump"

    graph.add_conditional_edges(
        "filter_matches",
        after_filter,
        {"dump": "dump", "enrich": "enrich"},
    )
    graph.add_edge("enrich", "dump")
    graph.add_edge("dump", END)
    return graph.compile()


def initial_state(_cfg: AppConfig, _env: EnvSettings) -> AgentState:
    return {
        "run_timestamp": datetime.now(timezone.utc).isoformat(),
        "raw_jobs": [],
        "scored": [],
        "matches": [],
    }
