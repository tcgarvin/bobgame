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
JOURNAL_MODEL_ENV = "JOURNAL_MODEL"


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
        # Usage accounting: OpenRouter then reports the dollar cost and the
        # cached-token count of every response (docs/11_cost_accounting.md).
        openrouter_usage={"include": True},
        temperature=0.7,
        timeout=90.0,
    )


def resolve_journal_model_name(
    model_name: str = "", planner_model_name: str = ""
) -> str:
    """The model the sleep-time journal runs on (docs/12_sleep_journal.md).

    An explicit `--journal-model`, else `$JOURNAL_MODEL`, else whatever the
    planner already resolved to, so a run that only sets `--planner-model`
    keeps both halves on one model.
    """
    explicit = model_name or os.environ.get(JOURNAL_MODEL_ENV, "")
    if explicit:
        return resolve_model_name(explicit)
    return planner_model_name or resolve_model_name()


def journal_model_settings(model_name: str) -> OpenRouterModelSettings:
    """The planner's settings with reasoning always on at low effort.

    The journal is written once a day and its quality matters more than its
    latency, so unlike the planner it always gets to think a little.
    """
    settings = planner_model_settings(model_name)
    settings["openrouter_reasoning"] = {"effort": "low"}
    return settings
