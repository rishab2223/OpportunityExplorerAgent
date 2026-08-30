from __future__ import annotations

import threading
from pathlib import Path
from queue import Queue
from typing import Any

MAX_BUFFER = 5000

_LOCK = threading.Lock()
_subscribers: list[Queue] = []
_buffer: list[str] = []
_log_path: Path | None = None
_stamp: str = ""


def start_run(stamp: str, run_dir: Path) -> Path:
    """Point the logger at outputs/{stamp}/run.log and reset the live buffer."""
    global _log_path, _stamp
    run_dir = Path(run_dir)
    run_dir.mkdir(parents=True, exist_ok=True)
    path = run_dir / "run.log"
    with _LOCK:
        _log_path = path
        _stamp = stamp
        _buffer.clear()
    return path


def finish_run(status: str) -> None:
    global _log_path, _stamp
    log(f"[run] finished: {status}")
    publish({"type": "done", "text": status})
    with _LOCK:
        _log_path = None
        _stamp = ""


def current_stamp() -> str:
    with _LOCK:
        return _stamp


def log(message: str) -> None:
    line = str(message).rstrip()
    try:
        print(line, flush=True)
    except UnicodeEncodeError:
        # Legacy Windows consoles cannot encode every character a job posting contains.
        print(line.encode("ascii", "replace").decode("ascii"), flush=True)
    # Buffer and subscriber snapshot are taken together so a client subscribing
    # mid-run gets each line exactly once: either in its backlog or on its queue.
    with _LOCK:
        path = _log_path
        _buffer.append(line)
        if len(_buffer) > MAX_BUFFER:
            del _buffer[: len(_buffer) - MAX_BUFFER]
        targets = list(_subscribers)
    if path is not None:
        try:
            with path.open("a", encoding="utf-8") as fh:
                fh.write(line + "\n")
        except OSError:
            pass
    event = {"type": "log", "text": line}
    for queue in targets:
        queue.put(event)


def publish(event: dict[str, Any]) -> None:
    with _LOCK:
        targets = list(_subscribers)
    for queue in targets:
        queue.put(event)


def subscribe() -> tuple[Queue, list[str]]:
    """Register for live events and get everything logged so far in this run."""
    queue: Queue = Queue()
    with _LOCK:
        _subscribers.append(queue)
        return queue, list(_buffer)


def unsubscribe(queue: Queue) -> None:
    with _LOCK:
        if queue in _subscribers:
            _subscribers.remove(queue)
