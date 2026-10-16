"""The once-a-day batch, run by the server itself (#38, Oct 2026).

At settings' daily.at (11:30 by default, local time) - or, when the server
was not running then, as soon as it starts later that day - it:

  1. looks again at the shortlist's waiting jobs (src/open_check.py), each
     every daily.check_every_days days, so dead postings leave the list;
  2. starts a search over the last 24 hours with your saved run settings, so
     new jobs are applied to while they are fresh.

The day it last ran is kept in the properties table, so a restart never runs
it twice, and a batch cut short by a stop picks up where it was: jobs looked
at today are not due again, and a search already started today is not
started again.

Started only by the real server (src.web.__main__), never on import: the
checks import the app and must not set off a real batch.
"""

from __future__ import annotations

import threading
import time
from datetime import datetime
from typing import Any

from src import dumps, open_check, properties
from src.config import AppConfig

LAST_DONE = "daily_batch_done"          # the date the whole batch finished
SEARCH_STARTED = "daily_search_started"  # the date its search run was started
CHECK_STARTED = "daily_check_started"
TICK_SECONDS = 60

_state: dict[str, Any] = {"running": False, "last_result": "", "thread": None}
_lock = threading.Lock()


def _today(now: datetime) -> str:
    return now.date().isoformat()


def batch_log(line: str) -> None:
    """The batch's own log, dumps/daily_batch.log, beside the console: a run
    you start yourself must not get the batch's lines in its run.log."""
    stamped = f"{datetime.now():%Y-%m-%d %H:%M:%S} {line}"
    print(stamped, flush=True)
    try:
        dumps.DUMPS_DIR.mkdir(parents=True, exist_ok=True)
        with open(dumps.DUMPS_DIR / "daily_batch.log", "a", encoding="utf-8") as fh:
            fh.write(stamped + "\n")
    except OSError:
        pass


def is_due(now: datetime, at: str, last_done: str | None) -> bool:
    """Past today's time, and not yet done today."""
    hour, minute = (int(x) for x in at.split(":"))
    return (now.hour, now.minute) >= (hour, minute) and last_done != _today(now)


def status(cfg: AppConfig) -> dict[str, Any]:
    """What the page shows about the batch."""
    return {
        "enabled": cfg.daily.enabled,
        "at": cfg.daily.at,
        "last_done": properties.get(LAST_DONE) or "",
        "running": _state["running"],
        "last_result": _state["last_result"],
    }


def run_batch(cfg: AppConfig, now: datetime | None = None, log=None) -> str:
    """One batch, now. Returns a one-line summary."""
    from src.web import runner, run_options

    now = now or datetime.now()
    today = _today(now)
    log = log or batch_log
    with _lock:
        if _state["running"]:
            return "already running"
        _state["running"] = True
    parts = []
    try:
        if cfg.daily.open_check:
            properties.put(CHECK_STARTED, today)
            counts = open_check.run(cfg.daily.check_every_days, cfg.daily.max_checks,
                                    cfg.daily.check_gap_seconds, log=log)
            parts.append(f"checked {sum(counts.values())}: {counts['closed']} closed, "
                         f"{counts['looks_closed']} look closed")
        if cfg.daily.search_run and properties.get(SEARCH_STARTED) != today:
            # Your saved run settings, over the last 24 hours only.
            base = run_options.load_yaml_config()
            settings = run_options.values(run_options.effective_config(base))
            settings["scrape"]["posted_within"] = "24h"
            try:
                stamp = runner.start(overrides=settings)
                properties.put(SEARCH_STARTED, today)
                parts.append(f"search run {stamp} started")
                log(f"[daily] Started the search over the last 24 hours: run {stamp}")
            except runner.RunBusy as exc:
                # A run you started yourself is going: today's is not needed twice.
                properties.put(SEARCH_STARTED, today)
                parts.append(f"search skipped: {exc}")
                log(f"[daily] A run is already going, so no second one today: {exc}")
        properties.put(LAST_DONE, today)
        summary = "; ".join(parts) or "nothing to do"
        _state["last_result"] = f"{now:%d %b %H:%M}: {summary}"
        log(f"[daily] Done: {summary}")
        return summary
    except Exception as exc:
        _state["last_result"] = f"{now:%d %b %H:%M}: failed - {exc}"
        log(f"[daily] The batch failed: {exc}")
        return f"failed: {exc}"
    finally:
        with _lock:
            _state["running"] = False


def _loop(load_cfg) -> None:
    while True:
        try:
            cfg = load_cfg()
            if cfg.daily.enabled and is_due(datetime.now(), cfg.daily.at, properties.get(LAST_DONE)):
                run_batch(cfg)
        except Exception as exc:
            print(f"[daily] scheduler error: {exc}", flush=True)
        time.sleep(TICK_SECONDS)


def start_scheduler(load_cfg) -> threading.Thread:
    """Start the batch clock on a daemon thread. `load_cfg` returns the
    settings each minute, so a change to daily.at needs no restart."""
    if _state["thread"] is not None:
        return _state["thread"]
    thread = threading.Thread(target=_loop, args=(load_cfg,), name="daily-batch", daemon=True)
    _state["thread"] = thread
    thread.start()
    return thread
