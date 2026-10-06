from __future__ import annotations

import json
import threading
import traceback
from typing import Any

from src import progress
from src.agent.graph import build_graph, initial_state
from src.agent.nodes.dump import run_dir_for, stamp_for
from src.config import load_env
from src.web import run_options


class RunBusy(Exception):
    pass


_LOCK = threading.Lock()
_active_stamp: str = ""
_last: dict[str, Any] = {}


def status() -> dict[str, Any]:
    with _LOCK:
        return {"active": bool(_active_stamp), "stamp": _active_stamp, "last": dict(_last)}


def start(overrides: dict[str, Any] | None = None, remember: bool = False) -> str:
    """Kick off one discover run on a worker thread and return its stamp.

    `overrides` are the run settings popup's choices, on top of settings.yaml
    and any saved preferences; refused as a whole (run_options.BadOptions)
    when any of them is out of range. `remember` keeps what differs from
    settings.yaml as the defaults for the next run started here - once the
    run has actually started, so a refused run changes nothing."""
    global _active_stamp
    # Reading and validating settings is slow enough to keep off the lock:
    # status() takes it too, and the page polls that.
    base = run_options.load_yaml_config()
    cfg = run_options.apply(run_options.effective_config(base), overrides)
    env = load_env()
    state = initial_state(cfg, env)
    stamp = stamp_for(state["run_timestamp"])
    run_dir = run_dir_for(state["run_timestamp"])
    with _LOCK:
        if _active_stamp:
            raise RunBusy(f"run {_active_stamp} is already in progress")
        progress.start_run(stamp, run_dir)
        _active_stamp = stamp
    try:
        # What this run was asked to do, beside what it found.
        (run_dir / "run_settings.json").write_text(
            json.dumps(run_options.values(cfg), indent=2), encoding="utf-8")
    except OSError:
        pass
    thread = threading.Thread(
        target=_run,
        args=(cfg, env, state, stamp),
        name=f"discover-{stamp}",
        daemon=True,
    )
    thread.start()
    if remember and overrides:
        run_options.save_preferences(overrides, base)
    return stamp


def _run(cfg, env, state, stamp: str) -> None:
    global _active_stamp
    outcome = "ok"
    summary: dict[str, Any] = {"stamp": stamp}
    try:
        progress.log(f"[run] resume={cfg.resume.local_path}")
        progress.log(run_options.summary_line(cfg))
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
