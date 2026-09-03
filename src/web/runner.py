from __future__ import annotations

import threading
import traceback
from typing import Any

from src import progress
from src.agent.graph import build_graph, initial_state
from src.agent.nodes.dump import run_dir_for, stamp_for
from src.config import load_env, load_yaml_config


class RunBusy(Exception):
    pass


_LOCK = threading.Lock()
_active_stamp: str = ""
_last: dict[str, Any] = {}


def status() -> dict[str, Any]:
    with _LOCK:
        return {"active": bool(_active_stamp), "stamp": _active_stamp, "last": dict(_last)}


def start(resume_path: str = "", max_detail_jobs: int | None = None) -> str:
    """Kick off one discover run on a worker thread and return its stamp."""
    global _active_stamp
    with _LOCK:
        if _active_stamp:
            raise RunBusy(f"run {_active_stamp} is already in progress")
        cfg = load_yaml_config()
        if resume_path:
            cfg.resume.local_path = resume_path
        if max_detail_jobs:
            cfg.scrape.max_detail_jobs = int(max_detail_jobs)
        env = load_env()
        state = initial_state(cfg, env)
        stamp = stamp_for(state["run_timestamp"])
        progress.start_run(stamp, run_dir_for(state["run_timestamp"]))
        _active_stamp = stamp

    thread = threading.Thread(
        target=_run,
        args=(cfg, env, state, stamp),
        name=f"discover-{stamp}",
        daemon=True,
    )
    thread.start()
    return stamp


def _run(cfg, env, state, stamp: str) -> None:
    global _active_stamp
    outcome = "ok"
    summary: dict[str, Any] = {"stamp": stamp}
    try:
        progress.log(f"[run] resume={cfg.resume.local_path} max_detail_jobs={cfg.scrape.max_detail_jobs}")
        result = build_graph(cfg, env).invoke(state)
        if result.get("failed_step"):
            outcome = "failed"
            summary["error"] = result.get("error_message") or ""
            progress.log(
                f"[run] FAILED at {result.get('failed_step')}: {summary['error']}"
            )
        summary["match_count"] = len(result.get("matches") or [])
    except Exception as exc:
        outcome = "failed"
        summary["error"] = str(exc)
        progress.log(f"[run] crashed: {exc}")
        progress.log(traceback.format_exc()[-1500:])
    finally:
        summary["status"] = outcome
        with _LOCK:
            _active_stamp = ""
            _last.clear()
            _last.update(summary)
        progress.finish_run(outcome)
