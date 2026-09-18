"""Model naming and model settings shared by the planner and the converser.

Both language-model halves of an actor run on the same OpenRouter model with
the same settings; keeping the resolution in one place means a `--planner-model`
flag or a `PLANNER_MODEL` environment variable moves both of them together.
"""

from __future__ import annotations

import os

from pydantic_ai.models.openrouter import OpenRouterModelSettings

DEFAULT_PLANNER_MODEL = "qwen/qwen3.7-flash"
PLANNER_MODEL_ENV = "PLANNER_MODEL"


def resolve_model_name(model_name: str = "") -> str:
    """The model id to run on: the argument, else the environment, else default.

    Bare OpenRouter ids look like `vendor/model`; anything else (such as
    `test` or an explicit `provider:model`) is passed through untouched.
    """
    resolved = model_name or os.environ.get(PLANNER_MODEL_ENV, DEFAULT_PLANNER_MODEL)
    if ":" not in resolved and "/" in resolved:
        return f"openrouter:{resolved}"
    return resolved


def planner_model_settings(model_name: str) -> OpenRouterModelSettings:
    """Per-model settings: reasoning off where allowed, low effort otherwise.

    Reasoning traces multiply latency; the planner gets its thinking from tool
    calls and the reflection paragraph instead. Some endpoints (GLM 5.x,
    MiniMax) refuse to disable reasoning, so they get low effort.
    """
    mandatory_reasoning = ("glm-5", "minimax")
    if any(marker in model_name for marker in mandatory_reasoning):
        reasoning: dict[str, object] = {"effort": "low"}
    else:
        reasoning = {"enabled": False}
    return OpenRouterModelSettings(
        openrouter_reasoning=reasoning,  # type: ignore[typeddict-item]
        temperature=0.7,
        timeout=90.0,
    )
