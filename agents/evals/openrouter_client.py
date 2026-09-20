"""A `JevClient` backed by an ordinary chat model on OpenRouter.

The eval suite exists to measure Jev. To say anything about *how* good Jev is,
the same scenarios have to run against the models people would otherwise reach
for, asked the same five questions in the same words - so the question strings
are imported from `jevclient.py` rather than restated here.

The difference the numbers cannot hide: Jev returns a real distribution over
the enumerated options, while a chat model is asked to *write down* what it
thinks its probabilities are. Treat the confidences from this backend as
self-reports.

`httpx` is used directly rather than pydantic-ai because the comparison needs
raw control of three things pydantic-ai abstracts away: provider pinning,
`response_format` (and the fallbacks for endpoints that reject a strict
schema), and `usage: {"include": true}` so the reply carries its dollar cost.
"""

from __future__ import annotations

import json
import time
from typing import Any, Final, Mapping, Sequence

import httpx

from agents.jev_agent.jevclient import (
    ACTION_QUESTION,
    DANGER_QUESTION,
    DONE_QUESTION,
    JevDecision,
    LOST_QUESTION,
    STUCK_QUESTION,
)

OPENROUTER_URL: Final = "https://openrouter.ai/api/v1/chat/completions"
API_KEY_VARIABLE: Final = "OPENROUTER_API_KEY"
DEFAULT_TIMEOUT_SECONDS: Final = 60.0
RANKED_LIMIT: Final = 5

# The order the `response_format` settings are tried in. Groq's Llama endpoints
# answer "No endpoints found" to a strict schema pinned to a provider, and some
# endpoints have no structured output at all, so each step is weaker than the
# last and the final one is "just write JSON and we will find it".
FORMAT_MODES: Final[tuple[str, ...]] = ("json_schema", "json_object", "text")

DECISION_SCHEMA: Final[dict[str, Any]] = {
    "type": "object",
    "properties": {
        "ranked": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "action": {"type": "string"},
                    "probability": {"type": "number"},
                },
                "required": ["action", "probability"],
                "additionalProperties": False,
            },
        },
        "done": {"type": "number"},
        "stuck": {"type": "number"},
        "lost": {"type": "number"},
        "danger": {"type": "number"},
    },
    "required": ["ranked", "done", "stuck", "lost", "danger"],
    "additionalProperties": False,
}

SYSTEM_PROMPT: Final = f"""\
You judge one tick of a survival simulation. You are given the world state as
JSON and the legal actions as a JSON object mapping an action key to what that
action does. Answer five questions about this one state.

1. action: {ACTION_QUESTION}
2. done: {DONE_QUESTION}
3. stuck: {STUCK_QUESTION}
4. lost: {LOST_QUESTION}
5. danger: {DANGER_QUESTION}

Reply with a single JSON object and nothing else:

{{"ranked": [{{"action": "<option key>", "probability": <0..1>}}, ...],
 "done": <0..1>, "stuck": <0..1>, "lost": <0..1>, "danger": <0..1>}}

"ranked" holds the {RANKED_LIMIT} most likely actions, most likely first, and
their probabilities, which should sum to about 1 across all options. Every
"action" must be one of the given option keys, copied exactly. "done",
"stuck", "lost" and "danger" are your probabilities that the answer to that
question is yes."""


class OpenRouterError(RuntimeError):
    """An OpenRouter call that cannot be turned into a `JevDecision`."""


class OpenRouterJevClient:
    """`JevClient` backed by a chat model on OpenRouter. One call per decide.

    Args:
        model: the OpenRouter model id, e.g. `openai/gpt-4.1-nano`.
        api_key: the OpenRouter key.
        provider: a provider slug to pin routing to, or "" for OpenRouter's
            own routing. A slug becomes `allow_fallbacks: false`, so a run
            measures the endpoint it names and not a silent substitute.
        extra: extra request-body fields, merged over the defaults. This is
            where a per-model `reasoning` setting goes.
        timeout: per-request timeout in seconds.
        transport: an `httpx` transport, for tests.
    """

    def __init__(
        self,
        model: str,
        *,
        api_key: str,
        provider: str = "",
        extra: Mapping[str, Any] | None = None,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.model = model
        self.provider = provider
        self._extra: dict[str, Any] = dict(extra or {})
        # Non-reasoning is the default: the comparison is about judgement per
        # dollar, and a reasoning trace changes both sides of that. A model
        # whose endpoint refuses the setting drops it and says so in the row.
        self._reasoning: Any = self._extra.pop("reasoning", {"enabled": False})
        self.reasoning_disabled = True
        self._format_index = 0
        self._client = httpx.AsyncClient(
            timeout=timeout,
            transport=transport,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
        )

    @property
    def format_mode(self) -> str:
        """The `response_format` setting currently in use."""
        return FORMAT_MODES[self._format_index]

    async def decide(
        self, state: Mapping[str, Any], options: Mapping[str, str]
    ) -> JevDecision:
        """Ask the chat model for a ranked action and the four judgements."""
        if not options:
            raise ValueError("the model needs at least one option to choose from")
        started = time.monotonic()
        payload = await self._complete(state, options)
        latency_ms = int((time.monotonic() - started) * 1000)
        return self._to_decision(payload, options, latency_ms)

    async def aclose(self) -> None:
        """Close the underlying HTTP client."""
        await self._client.aclose()

    async def _complete(
        self, state: Mapping[str, Any], options: Mapping[str, str]
    ) -> dict[str, Any]:
        """Post one request, stepping down through the fallbacks on refusal."""
        while True:
            response = await self._send(self._body(state, options))
            if response.status_code < 400:
                body = response.json()
                if not isinstance(body, dict):
                    raise OpenRouterError(
                        f"{self.model}: expected a JSON object from OpenRouter, "
                        f"got {type(body).__name__}"
                    )
                return body
            detail = _error_detail(response)
            if self.reasoning_disabled and _mentions_reasoning(detail):
                self.reasoning_disabled = False
                continue
            if self._format_index + 1 < len(FORMAT_MODES):
                self._format_index += 1
                continue
            raise OpenRouterError(
                f"{self.model}: OpenRouter refused the request "
                f"({response.status_code}): {detail}"
            )

    async def _send(self, body: Mapping[str, Any]) -> httpx.Response:
        """POST the body, retrying once on a 429 or a 5xx."""
        response = await self._client.post(OPENROUTER_URL, json=dict(body))
        if response.status_code == 429 or response.status_code >= 500:
            response = await self._client.post(OPENROUTER_URL, json=dict(body))
        return response

    def _body(
        self, state: Mapping[str, Any], options: Mapping[str, str]
    ) -> dict[str, Any]:
        """The request body for the current fallback and reasoning settings."""
        user = (
            "STATE:\n"
            + json.dumps(dict(state), sort_keys=True)
            + "\n\nOPTIONS:\n"
            + json.dumps(dict(options), sort_keys=True)
        )
        body: dict[str, Any] = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user},
            ],
            "temperature": 0,
            "usage": {"include": True},
        }
        if self.provider:
            body["provider"] = {
                "order": [self.provider],
                "allow_fallbacks": False,
            }
        if self.reasoning_disabled:
            body["reasoning"] = self._reasoning
        mode = self.format_mode
        if mode == "json_schema":
            body["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": "jev_decision",
                    "strict": True,
                    "schema": DECISION_SCHEMA,
                },
            }
        elif mode == "json_object":
            body["response_format"] = {"type": "json_object"}
        body.update(self._extra)
        return body

    def _to_decision(
        self,
        payload: Mapping[str, Any],
        options: Mapping[str, str],
        latency_ms: int,
    ) -> JevDecision:
        """Turn one OpenRouter reply into a `JevDecision`, or refuse it."""
        reply = _parse_reply(_content(payload, self.model), self.model)
        probabilities = _valid_probabilities(reply.get("ranked"), options, self.model)
        ranked = sorted(probabilities.items(), key=lambda kv: -kv[1])
        usage: Mapping[str, Any] = payload.get("usage") or {}
        return JevDecision(
            action=ranked[0][0],
            probabilities=probabilities,
            confidence=ranked[0][1],
            done=_probability(reply.get("done")),
            stuck=_probability(reply.get("stuck")),
            lost=_probability(reply.get("lost")),
            danger=_probability(reply.get("danger")),
            input_tokens=_count(usage.get("prompt_tokens")),
            output_tokens=_count(usage.get("completion_tokens")),
            latency_ms=latency_ms,
            cost_usd=_cost(usage.get("cost")),
            provider=str(payload.get("provider") or self.provider),
        )


def _content(payload: Mapping[str, Any], model: str) -> str:
    """The assistant message text of a chat-completions reply."""
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices:
        raise OpenRouterError(f"{model}: the reply carried no choices")
    first = choices[0]
    if not isinstance(first, dict):
        raise OpenRouterError(f"{model}: the reply's first choice is not an object")
    message = first.get("message")
    if not isinstance(message, dict):
        raise OpenRouterError(f"{model}: the reply's first choice carried no message")
    content = message.get("content")
    if not isinstance(content, str) or not content.strip():
        raise OpenRouterError(f"{model}: the reply's message carried no text")
    return content


def _parse_reply(content: str, model: str) -> Mapping[str, Any]:
    """The JSON object in an assistant message, fenced or prefaced or bare."""
    text = content.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:]
        text = text.strip()
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        parsed = _extract_object(text, model)
    if not isinstance(parsed, dict):
        raise OpenRouterError(
            f"{model}: expected a JSON object in the reply, "
            f"got {type(parsed).__name__}"
        )
    return parsed


def _extract_object(text: str, model: str) -> Any:
    """The outermost `{...}` in free text, for models that will not stay quiet."""
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end <= start:
        raise OpenRouterError(f"{model}: no JSON object in the reply: {text[:200]!r}")
    try:
        return json.loads(text[start : end + 1])
    except json.JSONDecodeError as error:
        raise OpenRouterError(
            f"{model}: the reply is not valid JSON ({error}): {text[:200]!r}"
        ) from error


def _valid_probabilities(
    ranked: Any, options: Mapping[str, str], model: str
) -> dict[str, float]:
    """The ranked list as a normalised dict, keeping only real option keys.

    A key the model invented is dropped rather than chosen, which is what "if
    the chosen action is not an option, take the next ranked one that is"
    amounts to. If nothing survives there is no decision to make, so this
    raises: a silently substituted action would score as a real answer.
    """
    if not isinstance(ranked, Sequence) or isinstance(ranked, (str, bytes)):
        raise OpenRouterError(f"{model}: 'ranked' is not a list: {ranked!r}")
    probabilities: dict[str, float] = {}
    for entry in ranked[:RANKED_LIMIT]:
        if not isinstance(entry, Mapping):
            continue
        action = entry.get("action")
        if not isinstance(action, str) or action not in options:
            continue
        probabilities.setdefault(action, _weight(entry.get("probability")))
    if not probabilities:
        raise OpenRouterError(
            f"{model}: none of the ranked actions is a legal option: {ranked!r}"
        )
    total = sum(probabilities.values())
    if total <= 0.0:
        share = 1.0 / len(probabilities)
        return {key: share for key in probabilities}
    return {key: value / total for key, value in probabilities.items()}


def _weight(value: Any) -> float:
    """A ranked action's reported likelihood, before normalisation.

    Only the lower bound is enforced: a model that answers with scores rather
    than probabilities is normalised, not flattened by a clamp at 1.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0.0
    return max(0.0, float(value))


def _probability(value: Any) -> float:
    """A reported probability, clamped to [0, 1]. Anything unusable is 0.0."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0.0
    return min(1.0, max(0.0, float(value)))


def _count(value: Any) -> int:
    """A token count from a usage block, 0 when the provider reported none."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0
    return int(value)


def _cost(value: Any) -> float:
    """The dollar cost from a usage block, 0.0 when the provider reported none."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0.0
    return float(value)


def _error_detail(response: httpx.Response) -> str:
    """The human-readable part of an OpenRouter error response."""
    try:
        body = response.json()
    except ValueError:
        return response.text[:300]
    if isinstance(body, dict):
        error = body.get("error")
        if isinstance(error, dict):
            return str(error.get("message", error))
        if error is not None:
            return str(error)
    return str(body)[:300]


def _mentions_reasoning(detail: str) -> bool:
    """Whether an error is about the `reasoning` field we added ourselves."""
    return "reasoning" in detail.lower()
