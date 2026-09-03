"""Provider-agnostic structured LLM calls for the enrich and apply steps.

``make_invoker(cfg, env, purpose)`` returns ``invoke(system, user, schema)``
that calls whichever provider settings.yaml selects for that purpose and
returns a validated instance of ``schema``.
"""

from __future__ import annotations

from typing import Callable, Literal, TypeVar

from pydantic import BaseModel

from src.config import AppConfig, EnvSettings

T = TypeVar("T", bound=BaseModel)
Invoker = Callable[[str, str, type[T]], T]
Purpose = Literal["enrich", "apply"]


def _settings(cfg: AppConfig, purpose: Purpose) -> tuple[str, str, str, float]:
    """(provider, model, effort, temperature) for this purpose."""
    if purpose == "enrich":
        provider = cfg.enrich_provider
        if provider == "claude":
            return provider, cfg.claude.enrich_model, cfg.claude.effort, 0.2
        return provider, cfg.openai.enrich_model, "", 0.2
    provider = cfg.apply_provider
    if provider == "claude":
        return provider, cfg.claude.apply_model, cfg.claude.apply_effort, 0.0
    return provider, cfg.openai.apply_model, "", 0.0


def describe_provider(cfg: AppConfig, purpose: Purpose) -> str:
    provider, model, effort, _ = _settings(cfg, purpose)
    if provider == "claude":
        return f"claude/{cfg.claude.backend} model={model} effort={effort}"
    return f"openai model={model}"


def make_invoker(cfg: AppConfig, env: EnvSettings, purpose: Purpose) -> Invoker:
    provider, model, effort, temperature = _settings(cfg, purpose)

    if provider == "claude":
        from src.llm_claude import structured_call

        def invoke_claude(system: str, user: str, schema: type[T]) -> T:
            return structured_call(
                system,
                user,
                schema,
                backend=cfg.claude.backend,
                model=model,
                effort=effort,
                env=env,
            )

        return invoke_claude

    if not env.openai_api_key:
        raise RuntimeError("OPENAI_API_KEY is not set")

    from langchain_openai import ChatOpenAI

    llm = ChatOpenAI(model=model, api_key=env.openai_api_key, temperature=temperature)
    runners: dict[type, object] = {}

    def invoke_openai(system: str, user: str, schema: type[T]) -> T:
        runner = runners.get(schema)
        if runner is None:
            runner = runners[schema] = llm.with_structured_output(schema)
        result = runner.invoke(f"{system}\n\n{user}")
        return result if isinstance(result, schema) else schema.model_validate(result)

    return invoke_openai
