"""Structured-output calls to Claude for the enrich step.

Two backends, chosen by ``claude.backend`` in settings.yaml:

* ``agent-sdk`` - the Claude Agent SDK drives the bundled Claude Code binary as a
  one-turn, tool-less query and returns ``structured_output`` validated against
  the pydantic schema. It authenticates the way Claude Code does (your Claude
  login or ``CLAUDE_CODE_OAUTH_TOKEN``), so no API key is needed.
* ``api`` - the Anthropic Messages API via ``anthropic.Anthropic().messages.parse``
  with ``ANTHROPIC_API_KEY`` (or an ``ant auth login`` profile).

Both are safe to call from worker threads; the agent-sdk path spins up its own
event loop per call.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import os
import sys
import tempfile
import threading
import uuid
from pathlib import Path
from typing import Any, Callable, Coroutine, TypeVar

from pydantic import BaseModel

from src.config import EnvSettings

T = TypeVar("T", bound=BaseModel)

MAX_OUTPUT_TOKENS = 16000


def structured_call(
    system: str,
    user: str,
    schema: type[T],
    *,
    backend: str,
    model: str,
    effort: str,
    env: EnvSettings,
) -> T:
    if backend == "api":
        return _api_call(system, user, schema, model, effort, env)
    return _run_coroutine(_agent_call(system, user, schema, model, effort))


def _run_coroutine(coro: Coroutine[Any, Any, T]) -> T:
    """asyncio.run, but safe inside a thread that already has a loop running.

    The assisted-apply worker drives Playwright's sync API, which keeps an event
    loop running on its thread; asyncio.run() there raises. Hop to a helper
    thread with a fresh loop in that case.
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    outcome: dict[str, Any] = {}

    def runner() -> None:
        try:
            outcome["value"] = asyncio.run(coro)
        except BaseException as exc:  # re-raised on the caller's thread
            outcome["error"] = exc

    thread = threading.Thread(target=runner, name="claude-agent-call", daemon=True)
    thread.start()
    thread.join()
    if "error" in outcome:
        raise outcome["error"]
    return outcome["value"]


TOKEN_VAR = "CLAUDE_CODE_OAUTH_TOKEN"


def _sdk_env() -> dict[str, str]:
    """The sign-in token for the bundled Claude Code, when this process was
    started without it.

    Windows hands a new user variable only to programs started after it was
    set, by a parent that has re-read the environment. The token was set
    while the editor, its terminals and this server were already running, so
    the SDK fell back to a saved login that had expired ("OAuth session
    expired and could not be refreshed", Oct 2026). The user's own setting
    is read from where Windows keeps it. Nothing is stored or logged."""
    if os.environ.get(TOKEN_VAR):
        return {}
    if sys.platform != "win32":
        return {}
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as key:
            token, _ = winreg.QueryValueEx(key, TOKEN_VAR)
    except OSError:
        return {}
    return {TOKEN_VAR: str(token)} if token else {}


def _neutral_cwd() -> str:
    """A folder with no Claude Code settings in it.

    The binary reads .claude/settings.json from wherever it is started, and
    started in this repo it printed "Ignoring 184 permissions.allow entries
    ... this workspace has not been trusted" on every tailoring call (Oct
    2026). None of that applies to a tool-less query, so it runs from an
    empty folder of its own."""
    path = Path(tempfile.gettempdir()) / "oea_claude_cwd"
    path.mkdir(parents=True, exist_ok=True)
    return str(path)


def _options(system: str, schema: type[BaseModel], model: str, effort: str):
    from claude_agent_sdk import ClaudeAgentOptions

    return ClaudeAgentOptions(
        model=model,
        system_prompt=system,
        # tools=[] takes the built-in tool definitions out of the prompt;
        # allowed_tools only says which need no approval, and left the full
        # set in every call's input (and a denied tool call could eat one of
        # the four turns).
        tools=[],
        allowed_tools=[],
        cwd=_neutral_cwd(),
        setting_sources=[],
        # The harness delivers structured output through an internal tool call,
        # which can take more than one turn; no user tools are allowed anyway.
        max_turns=4,
        effort=effort,
        output_format={"type": "json_schema", "schema": schema.model_json_schema()},
        env=_sdk_env(),
    )


async def _agent_call(system: str, user: str, schema: type[T], model: str, effort: str) -> T:
    from claude_agent_sdk import ResultMessage, query

    result: ResultMessage | None = None
    async for message in query(prompt=user, options=_options(system, schema, model, effort)):
        if isinstance(message, ResultMessage):
            result = message
    return _parse_result(result, schema)


def _parse_result(result: Any, schema: type[T]) -> T:
    if result is None:
        raise RuntimeError("Claude Agent SDK returned no result message")
    if result.subtype != "success":
        raise RuntimeError(f"Claude Agent SDK ended with {result.subtype}: {result.result!r}"[:800])
    if not result.structured_output:
        raise RuntimeError(f"Claude Agent SDK returned no structured output: {result.result!r}"[:800])
    return schema.model_validate(result.structured_output)


# -- one Claude Code process per kind of call, kept for an apply session ------

# Measured Oct 2026 (SDK 0.2.148): a one-off query() took 17.3 s, nearly all
# of it the bundled binary starting; a kept client took 1.9 s to start and
# then 2.0-2.7 s a call, each call in a fresh conversation (its own
# session_id), so nothing one call says reaches the next.
CONNECT_TIMEOUT_S = 90
CALL_TIMEOUT_S = 600
# After this many broken processes the session stops starting new ones and
# every call goes the one-off way.
MAX_BROKEN = 2


class _Kept:
    """One running client and the task that owns it. The task connects,
    answers calls one at a time from its inbox, and disconnects - all in the
    same task, which the SDK's task groups require."""

    def __init__(self, loop: asyncio.AbstractEventLoop, spec: tuple):
        self.loop = loop
        self.stopping = False      # set before the inbox exists: stop once connected
        self.inbox: asyncio.Queue | None = None
        self.ready: concurrent.futures.Future = concurrent.futures.Future()
        self.task = asyncio.run_coroutine_threadsafe(self._serve(spec), loop)

    async def _serve(self, spec: tuple) -> None:
        self.inbox = asyncio.Queue()
        client = None
        try:
            # Here, not in the caller: importing the SDK takes 1.6 s, and the
            # caller is the apply session, about to open the browser.
            from claude_agent_sdk import ClaudeSDKClient, ResultMessage

            client = ClaudeSDKClient(options=_options(*spec))
            await client.connect()
        except BaseException as exc:
            self.ready.set_exception(exc)
            if client is not None:
                try:
                    await client.disconnect()   # a half-started process too
                except BaseException:
                    pass
            return
        self.ready.set_result(True)
        try:
            while not self.stopping:
                item = await self.inbox.get()
                if item is None:
                    break
                user, reply = item
                try:
                    await client.query(user, session_id=uuid.uuid4().hex)
                    result = None
                    async for message in client.receive_response():
                        if isinstance(message, ResultMessage):
                            result = message
                    reply.set_result(result)
                except BaseException as exc:
                    if not reply.done():
                        reply.set_exception(exc)
                    break   # the process may be in any state now; it is retired
        finally:
            try:
                await client.disconnect()
            except BaseException:
                pass

    def ask(self, user: str) -> Any:
        """The ResultMessage for one call; raises if the process is broken."""
        self.ready.result(timeout=CONNECT_TIMEOUT_S)
        if self.task.done():
            raise RuntimeError("the kept Claude process has stopped")
        reply: concurrent.futures.Future = concurrent.futures.Future()
        self.loop.call_soon_threadsafe(self.inbox.put_nowait, (user, reply))
        return reply.result(timeout=CALL_TIMEOUT_S)

    def stop(self) -> None:
        self.stopping = True
        if self.inbox is not None and not self.task.done():
            self.loop.call_soon_threadsafe(self.inbox.put_nowait, None)


class ClaudeSession:
    """Claude Code processes kept running for one apply session.

    The instructions, the answer's schema, the model and its effort are fixed
    when a process starts, so there is one process per combination - in
    practice two: the form planner and the dropdown picker. warm() starts one
    in the background (at the Apply click, while the job page loads); call()
    uses it, starting it first if nothing has. Anything going wrong with a
    kept process sends that call the one-off way instead, so an apply never
    fails for want of one. close() ends them all."""

    def __init__(self, log: Callable[[str], None] | None = None):
        self._log = log or (lambda _line: None)
        self._lock = threading.Lock()
        self._kept: dict[tuple, _Kept] = {}
        self._broken = 0
        self._closed = False
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._loop.run_forever, name="claude-session", daemon=True)
        self._thread.start()

    @staticmethod
    def _key(system: str, schema: type[BaseModel], model: str, effort: str) -> tuple:
        return (model, effort, system, schema.__module__, schema.__qualname__)

    def _get(self, system: str, schema: type[BaseModel], model: str, effort: str) -> _Kept | None:
        with self._lock:
            if self._closed or self._broken >= MAX_BROKEN:
                return None
            key = self._key(system, schema, model, effort)
            kept = self._kept.get(key)
            if kept is None:
                kept = self._kept[key] = _Kept(self._loop, (system, schema, model, effort))
            return kept

    def warm(self, system: str, schema: type[BaseModel], model: str, effort: str) -> None:
        """Start the process for these calls without waiting for it."""
        self._get(system, schema, model, effort)

    def call(self, system: str, user: str, schema: type[T], model: str, effort: str) -> T:
        kept = self._get(system, schema, model, effort)
        if kept is not None:
            try:
                result = kept.ask(user)
            except BaseException as exc:
                self._retire(kept, exc)
            else:
                return _parse_result(result, schema)
        return _run_coroutine(_agent_call(system, user, schema, model, effort))

    def _retire(self, kept: _Kept, exc: BaseException) -> None:
        with self._lock:
            for key, value in list(self._kept.items()):
                if value is kept:
                    del self._kept[key]
            self._broken += 1
        kept.stop()
        kept.task.cancel()
        reason = str(exc).splitlines()[0][:160] if str(exc) else type(exc).__name__
        self._log(f"The kept Claude process failed ({reason}); this call starts one of its own.")

    def close(self) -> None:
        """End every kept process. Returns at once; they wind down behind."""
        with self._lock:
            if self._closed:
                return
            self._closed = True
            kept = list(self._kept.values())
            self._kept.clear()
        for item in kept:
            item.stop()

        def wind_down() -> None:
            for item in kept:
                try:
                    item.task.result(timeout=15)
                except BaseException:
                    item.task.cancel()
            self._loop.call_soon_threadsafe(self._loop.stop)

        threading.Thread(target=wind_down, name="claude-session-close", daemon=True).start()


def _api_call(
    system: str, user: str, schema: type[T], model: str, effort: str, env: EnvSettings
) -> T:
    import anthropic

    client = anthropic.Anthropic(api_key=env.anthropic_api_key or None)
    response = client.messages.parse(
        model=model,
        max_tokens=MAX_OUTPUT_TOKENS,
        system=system,
        messages=[{"role": "user", "content": user}],
        output_config={"effort": effort},
        output_format=schema,
    )
    if response.stop_reason == "refusal":
        details = getattr(response, "stop_details", None)
        raise RuntimeError(f"Claude refused the request: {getattr(details, 'explanation', '') or details}")
    if response.parsed_output is None:
        raise RuntimeError(f"Claude returned no parsable output (stop_reason={response.stop_reason})")
    return response.parsed_output
