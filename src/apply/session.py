from __future__ import annotations

import threading
import uuid
from queue import Empty, Queue
from typing import Any

TERMINAL_STATUSES = ("applied", "failed", "aborted")


class SessionBusy(Exception):
    pass


class Aborted(Exception):
    pass


class ApplySession:
    """One assisted-apply run. The worker thread blocks on `ask` until the user replies."""

    def __init__(self, stamp: str, job_id: str, label: str) -> None:
        self.id = uuid.uuid4().hex[:12]
        self.stamp = stamp
        self.job_id = job_id
        self.label = label
        self.status = "running"
        self.pending_question = ""
        self._events: list[dict[str, Any]] = []
        self._subscribers: list[Queue] = []
        self._answers: Queue = Queue()
        self._lock = threading.Lock()
        self._abort = threading.Event()

    def emit(self, kind: str, text: str) -> None:
        event = {"type": kind, "text": text, "status": self.status}
        with self._lock:
            self._events.append(event)
            targets = list(self._subscribers)
        for queue in targets:
            queue.put(event)

    def log(self, text: str) -> None:
        self.emit("step", text)

    def ask(self, question: str) -> str:
        """Pause the worker until the user answers in the chat pane."""
        if self._abort.is_set():
            raise Aborted("session aborted")
        self.status = "waiting_for_user"
        self.pending_question = question
        self.emit("question", question)
        while True:
            try:
                answer = self._answers.get(timeout=1)
            except Empty:
                if self._abort.is_set():
                    raise Aborted("session aborted")
                continue
            break
        self.pending_question = ""
        self.status = "running"
        text = answer.strip()
        if text.lower() in ("abort", "stop", "cancel"):
            self._abort.set()
            raise Aborted("user aborted")
        self.emit("answer", text)
        return text

    def answer(self, text: str) -> None:
        self._answers.put(text)

    def abort(self) -> None:
        self._abort.set()
        self._answers.put("abort")

    def aborted(self) -> bool:
        return self._abort.is_set()

    def finish(self, status: str, text: str = "") -> None:
        self.status = status
        self.emit("done", text or status)

    def subscribe(self) -> tuple[Queue, list[dict[str, Any]]]:
        queue: Queue = Queue()
        with self._lock:
            self._subscribers.append(queue)
            return queue, list(self._events)

    def unsubscribe(self, queue: Queue) -> None:
        with self._lock:
            if queue in self._subscribers:
                self._subscribers.remove(queue)

    def snapshot(self) -> dict[str, Any]:
        return {
            "session_id": self.id,
            "stamp": self.stamp,
            "job_id": self.job_id,
            "label": self.label,
            "status": self.status,
            "question": self.pending_question,
        }


_LOCK = threading.Lock()
_current: ApplySession | None = None


def start(stamp: str, job_id: str, label: str) -> ApplySession:
    global _current
    with _LOCK:
        if _current is not None and _current.status not in TERMINAL_STATUSES:
            raise SessionBusy(f"apply session {_current.id} is still active")
        _current = ApplySession(stamp, job_id, label)
        return _current


def current() -> ApplySession | None:
    with _LOCK:
        return _current


def get(session_id: str) -> ApplySession | None:
    with _LOCK:
        if _current is not None and _current.id == session_id:
            return _current
    return None
