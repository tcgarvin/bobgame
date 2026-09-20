"""Two fairer ways to get a probability out of a chat model.

The default OpenRouter backend asks the model to *write down* its
probabilities, which is a self-report and the weakest number in the whole
comparison (docs/13, "Approach D"). Two alternatives live here, both
`JevClient` implementations so every scenario, fixture and report works
unchanged:

- `VoteJevClient`: ask k times at temperature 1 for one action and four
  yes/no answers. The probability is the vote fraction, which is a real
  frequency the model produced rather than a number it chose to type. Costs k
  calls, and the cost column says so.
- `LogprobJevClient`: ask each noul as a single `yes`/`no` token and read the
  probability off the token logprobs. This is the closest analogue to what Jev
  returns - but only where the endpoint returns logprobs at all, and where it
  does not this raises rather than reporting a zero that would read as "no".

Both compose `OpenRouterJevClient` rather than re-implementing its HTTP:
provider pinning, the reasoning setting, `usage: {"include": true}`, the
retry on 429/5xx and the reply parsing all stay in one place. Several of the
helpers reused are private there (`_body`, `_send`, `_content`, `_parse_reply`,
`_error_detail`, `_cost`, `_count`); they are imported anyway, and should be
made public if this module survives the experiment.
"""

from __future__ import annotations

import asyncio
import math
import time
from collections import Counter
from dataclasses import dataclass
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

from .openrouter_client import (
    DEFAULT_TIMEOUT_SECONDS,
    OpenRouterError,
    OpenRouterJevClient,
    _content,
    _cost,
    _count,
    _error_detail,
    _parse_reply,
)

DEFAULT_VOTES: Final = 5
TOP_LOGPROBS: Final = 5

VOTE_MODE: Final = "vote"
LOGPROB_MODE: Final = "logprob"

# The four noul questions in the order they appear on a `JevDecision`.
NOUL_QUESTIONS: Final[dict[str, str]] = {
    "done": DONE_QUESTION,
    "stuck": STUCK_QUESTION,
    "lost": LOST_QUESTION,
    "danger": DANGER_QUESTION,
}

VOTE_SYSTEM_PROMPT: Final = f"""\
You judge one tick of a survival simulation. You are given the world state as
JSON and the legal actions as a JSON object mapping an action key to what that
action does. Answer five questions about this one state.

1. action: {ACTION_QUESTION}
2. done: {DONE_QUESTION}
3. stuck: {STUCK_QUESTION}
4. lost: {LOST_QUESTION}
5. danger: {DANGER_QUESTION}

Reply with a single JSON object and nothing else:

{{"action": "<option key>", "done": "yes", "stuck": "no", "lost": "no",
 "danger": "yes"}}

"action" must be one of the given option keys, copied exactly. The other four
are exactly "yes" or "no": commit to one, do not hedge and do not explain."""

NOUL_SYSTEM_TEMPLATE: Final = """\
You judge one tick of a survival simulation. You are given the world state as
JSON and the legal actions as a JSON object mapping an action key to what that
action does. Answer one question about this one state.

{question}

Answer with a single word, either yes or no. Output nothing else."""


class ElicitationError(RuntimeError):
    """An elicitation call that cannot be turned into a `JevDecision`."""


def vote_provider(provider: str, votes: int) -> str:
    """The provider string a vote row is labelled with, e.g. `OpenAI/vote5`.

    `JevDecision` is frozen and shared with the live agent, so the number of
    samples rides in the provider column rather than in a new field.
    """
    return f"{provider}/{VOTE_MODE}{votes}"


def logprob_provider(provider: str) -> str:
    """The provider string a logprob row is labelled with."""
    return f"{provider}/{LOGPROB_MODE}"


class VoteJevClient:
    """`JevClient` that turns k sampled answers into frequencies.

    Args:
        model: the OpenRouter model id.
        api_key: the OpenRouter key.
        provider: a provider slug to pin routing to, or "" for OpenRouter's
            own routing.
        extra: extra request-body fields, merged over the defaults.
        votes: how many samples to draw per decide.
        timeout: per-request timeout in seconds.
        transport: an `httpx` transport, for tests.

    Raises:
        ValueError: `votes` is less than one.
    """

    def __init__(
        self,
        model: str,
        *,
        api_key: str,
        provider: str = "",
        extra: Mapping[str, Any] | None = None,
        votes: int = DEFAULT_VOTES,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        if votes < 1:
            raise ValueError(f"votes must be at least 1, got {votes}")
        self.model = model
        self.provider = provider
        self.votes = votes
        self._inner = OpenRouterJevClient(
            model,
            api_key=api_key,
            provider=provider,
            extra=extra,
            timeout=timeout,
            transport=transport,
        )

    @property
    def reasoning_disabled(self) -> bool:
        """Whether the endpoint still accepts `reasoning: {"enabled": false}`."""
        return self._inner.reasoning_disabled

    @property
    def format_mode(self) -> str:
        """Reported for the results row; vote asks for JSON in the prompt."""
        return "vote"

    async def decide(
        self, state: Mapping[str, Any], options: Mapping[str, str]
    ) -> JevDecision:
        """Sample the model `votes` times and count the answers."""
        if not options:
            raise ValueError("the model needs at least one option to choose from")
        started = time.monotonic()
        payloads = await asyncio.gather(
            *(self._sample(state, options) for _ in range(self.votes))
        )
        latency_ms = int((time.monotonic() - started) * 1000)
        return self._tally(payloads, options, latency_ms)

    async def aclose(self) -> None:
        """Close the underlying HTTP client."""
        await self._inner.aclose()

    async def _sample(
        self, state: Mapping[str, Any], options: Mapping[str, str]
    ) -> Mapping[str, Any]:
        """One sampled answer, at temperature 1 so the k differ."""
        body = _prompt_body(
            self._inner, state, options, system=VOTE_SYSTEM_PROMPT, temperature=1.0
        )
        return await _post(self._inner, body, self.model)

    def _tally(
        self,
        payloads: Sequence[Mapping[str, Any]],
        options: Mapping[str, str],
        latency_ms: int,
    ) -> JevDecision:
        """Turn k replies into one decision.

        A sample whose action is not a legal option, or whose reply is not
        usable JSON, is dropped from the action histogram: counting it would
        invent a vote for something the world cannot do. Its noul answers are
        still counted when they parsed, because the five questions are asked
        and answered independently.
        """
        actions: Counter[str] = Counter()
        yes: Counter[str] = Counter()
        answered: Counter[str] = Counter()
        for payload in payloads:
            reply = _usable_reply(payload, self.model)
            if reply is None:
                continue
            action = reply.get("action")
            if isinstance(action, str) and action in options:
                actions[action] += 1
            for noul in NOUL_QUESTIONS:
                verdict = _yes_or_no(reply.get(noul))
                if verdict is None:
                    continue
                answered[noul] += 1
                yes[noul] += int(verdict)
        if not actions:
            raise ElicitationError(
                f"{self.model}: none of the {len(payloads)} votes named a legal "
                "option"
            )
        total = sum(actions.values())
        probabilities = {key: count / total for key, count in actions.items()}
        best = max(sorted(probabilities), key=lambda key: probabilities[key])
        fractions = {
            noul: yes[noul] / answered[noul] if answered[noul] else 0.0
            for noul in NOUL_QUESTIONS
        }
        return JevDecision(
            action=best,
            probabilities=probabilities,
            confidence=probabilities[best],
            done=fractions["done"],
            stuck=fractions["stuck"],
            lost=fractions["lost"],
            danger=fractions["danger"],
            input_tokens=sum(_input_tokens(payload) for payload in payloads),
            output_tokens=sum(_output_tokens(payload) for payload in payloads),
            latency_ms=latency_ms,
            cost_usd=sum(_call_cost(payload) for payload in payloads),
            provider=vote_provider(
                _reply_provider(payloads, self.provider), self.votes
            ),
        )


@dataclass(frozen=True)
class NoulReading:
    """One noul asked as a single yes/no token, and what the call cost."""

    probability: float
    input_tokens: int
    output_tokens: int
    cost_usd: float


class LogprobJevClient:
    """`JevClient` that reads each noul off the `yes`/`no` token logprobs.

    The action still comes from the ranked-JSON reply the default backend
    uses: a `Choice` over many keys has no one-token answer to read.

    Args:
        model: the OpenRouter model id.
        api_key: the OpenRouter key.
        provider: a provider slug to pin routing to, or "" for OpenRouter's
            own routing.
        extra: extra request-body fields, merged over the defaults.
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
        self._inner = OpenRouterJevClient(
            model,
            api_key=api_key,
            provider=provider,
            extra=extra,
            timeout=timeout,
            transport=transport,
        )

    @property
    def reasoning_disabled(self) -> bool:
        """Whether the endpoint still accepts `reasoning: {"enabled": false}`."""
        return self._inner.reasoning_disabled

    @property
    def format_mode(self) -> str:
        """The `response_format` the action call settled on."""
        return self._inner.format_mode

    async def decide(
        self, state: Mapping[str, Any], options: Mapping[str, str]
    ) -> JevDecision:
        """One action call and four one-token noul calls, all concurrent."""
        if not options:
            raise ValueError("the model needs at least one option to choose from")
        started = time.monotonic()
        action_call = self._inner.decide(state, options)
        noul_calls = [
            self._noul(state, options, noul, question)
            for noul, question in NOUL_QUESTIONS.items()
        ]
        results = await asyncio.gather(action_call, *noul_calls)
        latency_ms = int((time.monotonic() - started) * 1000)
        action = results[0]
        if not isinstance(action, JevDecision):
            raise ElicitationError(
                f"{self.model}: the action call returned no decision"
            )
        readings = {
            noul: reading
            for noul, reading in zip(NOUL_QUESTIONS, results[1:], strict=True)
            if isinstance(reading, NoulReading)
        }
        return self._combine(action, readings, latency_ms)

    async def aclose(self) -> None:
        """Close the underlying HTTP client."""
        await self._inner.aclose()

    async def _noul(
        self,
        state: Mapping[str, Any],
        options: Mapping[str, str],
        noul: str,
        question: str,
    ) -> NoulReading:
        """Ask one noul as a single token and read P(yes) off the logprobs."""
        body = _prompt_body(
            self._inner,
            state,
            options,
            system=NOUL_SYSTEM_TEMPLATE.format(question=question),
            temperature=0,
            logprobs=True,
            top_logprobs=TOP_LOGPROBS,
            max_tokens=1,
        )
        payload = await _post(self._inner, body, self.model)
        return NoulReading(
            probability=_yes_probability(payload, self.model, noul),
            input_tokens=_input_tokens(payload),
            output_tokens=_output_tokens(payload),
            cost_usd=_call_cost(payload),
        )

    def _combine(
        self,
        action: JevDecision,
        readings: Mapping[str, NoulReading],
        latency_ms: int,
    ) -> JevDecision:
        """The action decision with the logprob-read nouls and summed cost."""
        return JevDecision(
            action=action.action,
            probabilities=action.probabilities,
            confidence=action.confidence,
            done=readings["done"].probability,
            stuck=readings["stuck"].probability,
            lost=readings["lost"].probability,
            danger=readings["danger"].probability,
            input_tokens=action.input_tokens
            + sum(reading.input_tokens for reading in readings.values()),
            output_tokens=action.output_tokens
            + sum(reading.output_tokens for reading in readings.values()),
            latency_ms=latency_ms,
            cost_usd=action.cost_usd
            + sum(reading.cost_usd for reading in readings.values()),
            provider=logprob_provider(action.provider or self.provider),
        )


def _prompt_body(
    inner: OpenRouterJevClient,
    state: Mapping[str, Any],
    options: Mapping[str, str],
    *,
    system: str,
    **overrides: Any,
) -> dict[str, Any]:
    """A request body with a different prompt but the same plumbing.

    Built from the base client's own body so provider pinning, the reasoning
    setting, `usage: {"include": true}` and `JEV_EVAL_EXTRA` are not restated
    here. The ranked-JSON `response_format` is dropped: neither mode asks for
    that shape.
    """
    body = dict(inner._body(state, options))
    user = body["messages"][1]["content"]
    body["messages"] = [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]
    body.pop("response_format", None)
    body.update(overrides)
    return body


async def _post(
    inner: OpenRouterJevClient, body: Mapping[str, Any], model: str
) -> Mapping[str, Any]:
    """POST one body through the base client's retrying sender.

    Raises:
        ElicitationError: OpenRouter refused the request, or answered with
            something that is not a JSON object.
    """
    response = await inner._send(body)
    if response.status_code >= 400:
        raise ElicitationError(
            f"{model}: OpenRouter refused the request "
            f"({response.status_code}): {_error_detail(response)}"
        )
    payload = response.json()
    if not isinstance(payload, dict):
        raise ElicitationError(
            f"{model}: expected a JSON object from OpenRouter, "
            f"got {type(payload).__name__}"
        )
    return payload


def _usable_reply(payload: Mapping[str, Any], model: str) -> Mapping[str, Any] | None:
    """One sample's JSON object, or None when the sample cannot be read.

    A sample that came back as prose or broken JSON is one vote lost, not a
    failed tick: it is dropped exactly as an illegal action is, and the caller
    raises only if nothing usable survives.
    """
    try:
        return _parse_reply(_content(payload, model), model)
    except OpenRouterError:
        return None


def _yes_or_no(value: Any) -> bool | None:
    """A sampled yes/no answer, or None when it is neither."""
    if isinstance(value, bool):
        return value
    if not isinstance(value, str):
        return None
    text = value.strip().strip('".').lower()
    if text in ("yes", "y", "true"):
        return True
    if text in ("no", "n", "false"):
        return False
    return None


def _token_word(token: Any) -> str:
    """A generated token reduced to a bare lowercase word."""
    if not isinstance(token, str):
        return ""
    return token.strip().strip('".').lower()


def _yes_probability(payload: Mapping[str, Any], model: str, noul: str) -> float:
    """P(yes) / (P(yes) + P(no)) from the first token's top logprobs.

    Raises:
        ElicitationError: the endpoint returned no logprobs, or neither `yes`
            nor `no` appears in them and the generated token is neither. A
            guessed 0.0 would read as a confident "no", so this refuses.
    """
    entry = _first_token(payload, model, noul)
    yes_mass = 0.0
    no_mass = 0.0
    for candidate in entry.get("top_logprobs") or ():
        if not isinstance(candidate, Mapping):
            continue
        logprob = candidate.get("logprob")
        if isinstance(logprob, bool) or not isinstance(logprob, (int, float)):
            continue
        word = _token_word(candidate.get("token"))
        if word == "yes":
            yes_mass += math.exp(float(logprob))
        elif word == "no":
            no_mass += math.exp(float(logprob))
    if yes_mass + no_mass > 0.0:
        return yes_mass / (yes_mass + no_mass)
    generated = _token_word(entry.get("token"))
    if generated == "yes":
        return 1.0
    if generated == "no":
        return 0.0
    raise ElicitationError(
        f"{model}: the {noul} reply carried no 'yes' or 'no' token in its top "
        f"logprobs and answered {generated!r}; this endpoint cannot be asked "
        "for logprobs"
    )


def _first_token(
    payload: Mapping[str, Any], model: str, noul: str
) -> Mapping[str, Any]:
    """The logprobs entry for the first generated token.

    Raises:
        ElicitationError: the reply carried no per-token logprobs at all,
            which is how most non-OpenAI endpoints answer `logprobs: true`.
    """
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices:
        raise ElicitationError(f"{model}: the {noul} reply carried no choices")
    first = choices[0]
    if not isinstance(first, Mapping):
        raise ElicitationError(
            f"{model}: the {noul} reply's first choice is not an object"
        )
    logprobs = first.get("logprobs")
    content = logprobs.get("content") if isinstance(logprobs, Mapping) else logprobs
    if not isinstance(content, list) or not content:
        raise ElicitationError(
            f"{model}: the {noul} reply carried no token logprobs; this "
            "endpoint does not support logprobs"
        )
    entry = content[0]
    if not isinstance(entry, Mapping):
        raise ElicitationError(
            f"{model}: the {noul} reply's first token entry is not an object"
        )
    return entry


def _usage(payload: Mapping[str, Any]) -> Mapping[str, Any]:
    """The usage block of a reply, empty when the provider reported none."""
    usage = payload.get("usage")
    return usage if isinstance(usage, Mapping) else {}


def _input_tokens(payload: Mapping[str, Any]) -> int:
    """This call's prompt tokens."""
    return _count(_usage(payload).get("prompt_tokens"))


def _output_tokens(payload: Mapping[str, Any]) -> int:
    """This call's completion tokens."""
    return _count(_usage(payload).get("completion_tokens"))


def _call_cost(payload: Mapping[str, Any]) -> float:
    """This call's dollar cost, as OpenRouter reported it."""
    return _cost(_usage(payload).get("cost"))


def _reply_provider(payloads: Sequence[Mapping[str, Any]], fallback: str) -> str:
    """The endpoint the samples were served by, or the pinned slug."""
    for payload in payloads:
        provider = payload.get("provider")
        if isinstance(provider, str) and provider:
            return provider
    return fallback
