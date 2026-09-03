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
import threading
from typing import Any, Coroutine, TypeVar

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


async def _agent_call(system: str, user: str, schema: type[T], model: str, effort: str) -> T:
    from claude_agent_sdk import ClaudeAgentOptions, ResultMessage, query

    options = ClaudeAgentOptions(
        model=model,
        system_prompt=system,
        allowed_tools=[],
        # The harness delivers structured output through an internal tool call,
        # which can take more than one turn; no user tools are allowed anyway.
        max_turns=4,
        effort=effort,
        output_format={"type": "json_schema", "schema": schema.model_json_schema()},
    )
    result: ResultMessage | None = None
    async for message in query(prompt=user, options=options):
        if isinstance(message, ResultMessage):
            result = message
    if result is None:
        raise RuntimeError("Claude Agent SDK returned no result message")
    if result.subtype != "success":
        raise RuntimeError(f"Claude Agent SDK ended with {result.subtype}: {result.result!r}"[:800])
    if not result.structured_output:
        raise RuntimeError(f"Claude Agent SDK returned no structured output: {result.result!r}"[:800])
    return schema.model_validate(result.structured_output)


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
