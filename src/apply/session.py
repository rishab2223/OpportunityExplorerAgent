from __future__ import annotations

import re
import threading
import uuid
from queue import Empty, Queue
from typing import Any

TERMINAL_STATUSES = ("applied", "failed", "aborted", "closed")
# "dump" / "dump 10": save the page for offline inspection and keep waiting.
# Not an answer, so it never reaches a field or the answer bank.
DUMP_COMMAND_RE = re.compile(r"^(?:dump|dump page|dump dom|inspect)(?:\s+(\d{1,2}))?$", re.IGNORECASE)


class SessionBusy(Exception):
    pass


class Aborted(Exception):
    pass


class PageChanged(Exception):
    """Raised from idle_tick while the worker waits at a hand-off prompt: the
    user opened a form (an Easy Apply popup, a new tab) or submitted the
    application instead of typing, so the wait ends and the worker acts on
    the page. `reason` is 'fields', 'url', 'tab' or 'submitted'."""

    def __init__(self, reason: str = "fields") -> None:
        super().__init__(reason)
        self.reason = reason


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
        # Runs with the final status BEFORE the done event is emitted, so the
        # UI's reload on "done" already sees the recorded outcome (history row,
        # per-run status). Consumed once.
        self.on_outcome = None
        # Called roughly once a second while waiting for the user (see _wait).
        self.idle_tick = None
        # Called with a delay in seconds when the user types "dump [N]" at a
        # prompt; the worker saves the live page under outputs/dom/.
        self.on_dump = None

    def emit(self, event_type: str, text: str, **extra: Any) -> None:
        # extra may itself carry a "kind" key (choice events), so the event
        # type parameter must not share that name.
        event = {"type": event_type, "text": text, "status": self.status, **extra}
        with self._lock:
            self._events.append(event)
            targets = list(self._subscribers)
        for queue in targets:
            queue.put(event)

    def log(self, text: str) -> None:
        self.emit("step", text)

    def ask(self, question: str, suggestion: str = "") -> str:
        """Pause the worker until the user answers in the chat pane. A
        suggestion is pre-filled into the chat input for editing."""
        extra = {"suggestion": suggestion} if suggestion else {}
        return self._wait(question, "question", extra)

    def ask_choice(self, kind: str, text: str, meta: dict[str, Any] | None = None) -> str:
        """Like ask(), but the UI renders a modal (resume picker, cover-letter
        editor) instead of a chat line. The reply comes back through the same
        chat endpoint, using the __use__ / __revise__ sentinels."""
        return self._wait(text, "choice", {"kind": kind, "meta": meta or {}})

    def _wait(self, question: str, event_type: str, extra: dict[str, Any]) -> str:
        if self._abort.is_set():
            raise Aborted("session aborted")
        self.status = "waiting_for_user"
        self.pending_question = question
        self.emit(event_type, question, **extra)
        while True:
            try:
                answer = self._answers.get(timeout=1)
            except Empty:
                if self._abort.is_set():
                    raise Aborted("session aborted")
                # Sync Playwright only delivers page events (a file picker the
                # user just opened) while the worker is talking to the browser;
                # the worker installs a tick so those are serviced mid-wait.
                tick = self.idle_tick
                if tick is not None:
                    try:
                        tick()
                    except PageChanged:
                        self.pending_question = ""
                        self.status = "running"
                        # The prompt is withdrawn: the page answered it.
                        self.emit("answer", "(the page changed; reading it again)")
                        raise
                    except Exception:
                        pass
                continue
            dump = DUMP_COMMAND_RE.match(answer.strip())
            if dump and self.on_dump is not None:
                self.emit("answer", answer.strip())
                try:
                    self.on_dump(int(dump.group(1) or 0))
                except Exception as exc:
                    self.log(f"Page dump failed: {str(exc).splitlines()[0][:200]}")
                continue  # the prompt is still open
            break
        self.pending_question = ""
        self.status = "running"
        text = answer.strip()
        if text.lower() in ("abort", "stop", "cancel"):
            self._abort.set()
            raise Aborted("user aborted")
        # Long payloads (an edited resume source) would swamp the transcript.
        self.emit("answer", text if len(text) <= 400 else f"{text[:400]}... [{len(text)} chars]")
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
        callback, self.on_outcome = self.on_outcome, None
        if callback is not None:
            try:
                callback(status)
            except Exception:
                pass
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
