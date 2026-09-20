"""Which model the eval suite asks: Jev, or a chat model on OpenRouter.

One environment variable picks the backend, so the whole suite - and the
matrix runner in `run_matrix.py`, which only sets environment variables and
shells out to pytest - can be pointed at another model without touching a test.

    JEV_EVAL_BACKEND unset, or `jev`   -> TypeSafe, model from JEV_EVAL_MODEL
    JEV_EVAL_BACKEND=openrouter:<id>[@<provider slug>]
                                       -> OpenRouter, e.g.
                                          `openrouter:openai/gpt-oss-20b@groq`
    JEV_EVAL_BACKEND=openrouter+vote:<id>[@<slug>]
                                       -> the same model asked five times at
                                          temperature 1, the answer a vote
    JEV_EVAL_BACKEND=openrouter+logprob:<id>[@<slug>]
                                       -> the action as usual, each noul read
                                          off a one-token yes/no's logprobs

The `+<mode>` suffix is the elicitation mode (`evals/elicitation.py`): how a
probability is got out of a chat model, as opposed to which model is asked.

`JEV_EVAL_EXTRA` is a JSON object merged into the OpenRouter request body, for
per-model settings such as `{"reasoning": {"effort": "minimal"}}`.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from typing import Any, Final, Mapping, Protocol

from agents.jev_agent.jevclient import DEFAULT_MODEL, JevClient

from .elicitation import (
    DEFAULT_VOTES,
    LOGPROB_MODE,
    VOTE_MODE,
    LogprobJevClient,
    VoteJevClient,
)
from .openrouter_client import API_KEY_VARIABLE as OPENROUTER_KEY_VARIABLE
from .openrouter_client import OpenRouterJevClient

BACKEND_VARIABLE: Final = "JEV_EVAL_BACKEND"
MODEL_VARIABLE: Final = "JEV_EVAL_MODEL"
EXTRA_VARIABLE: Final = "JEV_EVAL_EXTRA"
REPEAT_VARIABLE: Final = "JEV_EVAL_REPEAT"
RUN_ID_VARIABLE: Final = "JEV_EVAL_RUN_ID"

TYPESAFE_KEY_VARIABLE: Final = "TYPESAFE_API_KEY"
JEV_BACKEND: Final = "jev"
OPENROUTER_BACKEND: Final = "openrouter"

# The elicitation modes an `openrouter` backend can carry, written
# `openrouter+<mode>:`. The empty string is the single-JSON-reply default.
ELICITATION_MODES: Final[tuple[str, ...]] = ("", VOTE_MODE, LOGPROB_MODE)

# The stint's own budget is under a second; an eval is not racing a tick.
JEV_TIMEOUT_SECONDS: Final = 30.0
OPENROUTER_TIMEOUT_SECONDS: Final = 60.0

_UNSAFE = re.compile(r"[^A-Za-z0-9._-]+")


class ClosableJevClient(JevClient, Protocol):
    """A `JevClient` the fixture owns and has to close."""

    async def aclose(self) -> None:
        """Release the client's network resources."""


@dataclass(frozen=True)
class BackendSpec:
    """Which backend, which model, which provider, and what else to send."""

    kind: str
    model: str
    provider: str = ""
    extra: dict[str, Any] = field(default_factory=dict)
    elicitation: str = ""

    @property
    def backend_name(self) -> str:
        """`openrouter`, or `openrouter+vote` when an elicitation mode is set."""
        if self.elicitation:
            return f"{self.kind}+{self.elicitation}"
        return self.kind

    @property
    def label(self) -> str:
        """The backend as it is written in `JEV_EVAL_BACKEND` and in results."""
        if self.kind == JEV_BACKEND:
            return f"{JEV_BACKEND}:{self.model}"
        suffix = f"@{self.provider}" if self.provider else ""
        return f"{self.backend_name}:{self.model}{suffix}"

    @property
    def slug(self) -> str:
        """The label as a file name: no slashes, colons or at-signs."""
        return _UNSAFE.sub("_", self.label)

    @property
    def api_key_variable(self) -> str:
        """The environment variable this backend needs a key in."""
        if self.kind == JEV_BACKEND:
            return TYPESAFE_KEY_VARIABLE
        return OPENROUTER_KEY_VARIABLE

    def environment(self) -> dict[str, str]:
        """The environment variables that reproduce this spec in a subprocess."""
        if self.kind == JEV_BACKEND:
            return {BACKEND_VARIABLE: JEV_BACKEND, MODEL_VARIABLE: self.model}
        suffix = f"@{self.provider}" if self.provider else ""
        return {
            BACKEND_VARIABLE: f"{self.backend_name}:{self.model}{suffix}",
            EXTRA_VARIABLE: json.dumps(self.extra),
        }


def parse_backend(value: str, *, extra: Mapping[str, Any] | None = None) -> BackendSpec:
    """Parse a `JEV_EVAL_BACKEND` value. An empty value means Jev.

    Raises:
        ValueError: the value names a backend that does not exist, or names
            OpenRouter without a model id.
    """
    text = value.strip()
    if not text or text == JEV_BACKEND:
        return BackendSpec(
            kind=JEV_BACKEND, model=os.environ.get(MODEL_VARIABLE, "") or DEFAULT_MODEL
        )
    kind, separator, rest = text.partition(":")
    if kind == JEV_BACKEND:
        return BackendSpec(kind=JEV_BACKEND, model=rest or DEFAULT_MODEL)
    kind, _, elicitation = kind.partition("+")
    if (
        kind != OPENROUTER_BACKEND
        or not separator
        or elicitation not in ELICITATION_MODES
    ):
        raise ValueError(
            f"{BACKEND_VARIABLE}={value!r}: expected 'jev' or "
            "'openrouter[+vote|+logprob]:<model id>[@<provider slug>]'"
        )
    model, _, provider = rest.partition("@")
    if not model:
        raise ValueError(
            f"{BACKEND_VARIABLE}={value!r}: no model id after 'openrouter:'"
        )
    return BackendSpec(
        kind=OPENROUTER_BACKEND,
        model=model,
        provider=provider,
        extra=dict(extra or {}),
        elicitation=elicitation,
    )


def spec_from_env() -> BackendSpec:
    """The backend this process was told to evaluate."""
    return parse_backend(
        os.environ.get(BACKEND_VARIABLE, ""),
        extra=parse_extra(os.environ.get(EXTRA_VARIABLE, "")),
    )


def parse_extra(value: str) -> dict[str, Any]:
    """Parse `JEV_EVAL_EXTRA`. Empty means no extra request fields.

    Raises:
        ValueError: the value is not a JSON object.
    """
    text = value.strip()
    if not text:
        return {}
    parsed = json.loads(text)
    if not isinstance(parsed, dict):
        raise ValueError(
            f"{EXTRA_VARIABLE} must be a JSON object, got {type(parsed).__name__}"
        )
    return parsed


def row_fields(client: JevClient) -> dict[str, Any]:
    """Backend-specific columns for a results row.

    The OpenRouter backend negotiates with the endpoint - it may have had to
    drop `reasoning: {"enabled": false}` or step down from a strict schema -
    and a row that does not say so is not reproducible.
    """
    if not isinstance(client, (OpenRouterJevClient, VoteJevClient, LogprobJevClient)):
        return {}
    return {
        "reasoning_disabled": client.reasoning_disabled,
        "response_format": client.format_mode,
    }


def build_client(spec: BackendSpec) -> ClosableJevClient:
    """The client for `spec`, reading the API key it needs from the environment.

    Raises:
        RuntimeError: the backend's API key is not set.
    """
    key = os.environ.get(spec.api_key_variable, "")
    if not key:
        raise RuntimeError(f"{spec.api_key_variable} is not set")
    if spec.kind == JEV_BACKEND:
        # Imported late: the TypeSafe SDK is only needed for the Jev backend.
        from agents.jev_agent.jevclient import TypeSafeJevClient

        return TypeSafeJevClient(model=spec.model, timeout=JEV_TIMEOUT_SECONDS)
    if spec.elicitation == VOTE_MODE:
        return VoteJevClient(
            spec.model,
            api_key=key,
            provider=spec.provider,
            extra=spec.extra,
            votes=DEFAULT_VOTES,
            timeout=OPENROUTER_TIMEOUT_SECONDS,
        )
    if spec.elicitation == LOGPROB_MODE:
        return LogprobJevClient(
            spec.model,
            api_key=key,
            provider=spec.provider,
            extra=spec.extra,
            timeout=OPENROUTER_TIMEOUT_SECONDS,
        )
    return OpenRouterJevClient(
        spec.model,
        api_key=key,
        provider=spec.provider,
        extra=spec.extra,
        timeout=OPENROUTER_TIMEOUT_SECONDS,
    )
