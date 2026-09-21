"""The settler's journal: five sections, rewritten at the end of every day.

Contract: [docs/12_sleep_journal.md](../../../../docs/12_sleep_journal.md).

`memory.md` used to be an append-only list of bullets that grew until it
dominated every planner prompt. It is now a markdown journal with five sections
the settler rewrites when it falls asleep or dies, plus a scratch section that
the `remember` tool and the closing note of a conversation append to during the
day. The rewrite is one model call: it is given the current journal and a log
of everything the day contained, and it returns the five sections.

Nothing here talks to the world. The agent owns the trigger, the planner owns
the day log, and this module owns the file format, the token budget and the
model call.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol, Sequence

import structlog
from pydantic import BaseModel, Field
from pydantic_ai import Agent, ModelRetry

from . import items
from .llm import journal_model_settings, resolve_journal_model_name
from .pricing import usage_from_messages

logger = structlog.get_logger(__name__)

# The five rewritten sections, in the order they appear in the file.
SECTION_STORY = "Story so far"
SECTION_ME = "Me"
SECTION_OTHERS = "Others"
SECTION_LEARNINGS = "Learnings"
SECTION_TOMORROW = "Tomorrow"
# The sixth section: what the day appended, folded in and cleared by a rewrite.
SECTION_SCRATCH = "Today's notes"

WRITTEN_SECTIONS: tuple[str, ...] = (
    SECTION_STORY,
    SECTION_ME,
    SECTION_OTHERS,
    SECTION_LEARNINGS,
    SECTION_TOMORROW,
)
ALL_SECTIONS: tuple[str, ...] = WRITTEN_SECTIONS + (SECTION_SCRATCH,)

# Each rewritten section is capped at this many tokens. Five sections plus the
# day's scratch is then about 3k tokens in every planner prompt, which is what
# the old unbounded notes file cost after half a day.
SECTION_TOKEN_LIMIT = 600

# How long a planner turn waits for a rewrite that is still running before it
# gives up and reads the journal as it stands.
JOURNAL_WAIT_SECONDS = 120.0
# How long one rewrite may run before it is given up as failed (the day log is
# kept for the next one). It has to end inside a settler's save wait (170 s,
# `agent/saving.py`): a model call that hung for eleven minutes left its settler
# undrained at the new moon and the whole save was abandoned. Rewrites
# normally take 35-100 s.
JOURNAL_REWRITE_TIMEOUT_SECONDS = 150.0


class JournalRewriteTimeout(Exception):
    """A journal rewrite that did not answer in time and was given up."""


# The day log is rendered for the model; anything past this is dropped oldest
# first. 60k characters is roughly 15k tokens, comfortably inside one call.
DAY_LOG_CHAR_LIMIT = 60_000
DAY_LOG_DROPPED_NOTE = (
    "[the oldest {count} entries of this day's log were dropped to fit]"
)

# Day-log entry kinds.
KIND_CALL = "call"
KIND_RESULT = "result"
KIND_REFLECTION = "reflection"
KIND_NOTE = "note"
KIND_EVENT = "event"

# Results worth exactly one copy (the last), and results worth none.
LAST_RESULT_ONLY_TOOLS = frozenset({"look"})
DROPPED_RESULT_TOOLS = frozenset({"recall"})

# What triggered a rewrite.
TRIGGER_SLEEP = "sleep"
TRIGGER_DEATH = "death"

TokenCounter = Callable[[str], int]

_ENCODING_NAME = "o200k_base"
_encoding: Any = None


def _tiktoken_encoding() -> Any:
    """The `o200k_base` encoding, loaded once per process.

    Loading it reads a cached vocabulary from disk, which is slow enough that
    doing it per section would show up in a rewrite's latency.
    """
    global _encoding
    if _encoding is None:
        import tiktoken

        _encoding = tiktoken.get_encoding(_ENCODING_NAME)
    return _encoding


def count_tokens(text: str) -> int:
    """How many tokens `text` is, by the encoding the models here use."""
    if not text:
        return 0
    return len(_tiktoken_encoding().encode(text))


def truncate_to_tokens(text: str, limit: int, counter: TokenCounter) -> str:
    """The longest prefix of `text` that `counter` scores at or below `limit`.

    With the real encoder this decodes the first `limit` tokens. Any other
    counter (a test double, say) is fed whole words until one more would go
    over, because only the encoder can split a word safely.
    """
    if counter is count_tokens:
        encoding = _tiktoken_encoding()
        return str(encoding.decode(encoding.encode(text)[:limit]))
    words = text.split()
    kept: list[str] = []
    for word in words:
        candidate = " ".join(kept + [word])
        if counter(candidate) > limit:
            break
        kept.append(word)
    return " ".join(kept)


def seed_story(entity_id: str) -> str:
    """The `Story so far` a settler starts with, before it has lived a day."""
    return (
        f"{entity_id} woke up on a large, wild island with the others "
        "who woke there, and no memory of how they got there."
    )


@dataclass
class Journal:
    """One settler's journal: five written sections and the day's scratch lines.

    The five sections are only ever replaced wholesale by a rewrite; `scratch`
    is the bullet list the `remember` tool and conversation notes append to
    during the day, and a rewrite folds it in and clears it.
    """

    story_so_far: str = ""
    me: str = ""
    others: str = ""
    learnings: str = ""
    tomorrow: str = ""
    scratch: list[str] = field(default_factory=list)

    @classmethod
    def seed(cls, entity_id: str) -> "Journal":
        """A brand new journal: the premise, and nothing else yet."""
        return cls(story_so_far=seed_story(entity_id))

    def written_sections(self) -> dict[str, str]:
        """The five rewritten sections, keyed by their heading."""
        return {
            SECTION_STORY: self.story_so_far,
            SECTION_ME: self.me,
            SECTION_OTHERS: self.others,
            SECTION_LEARNINGS: self.learnings,
            SECTION_TOMORROW: self.tomorrow,
        }

    def all_sections(self) -> dict[str, str]:
        """All six sections keyed by their heading, the scratch lines included.

        This is the shape the planner trace carries (docs/12_sleep_journal.md),
        so the viewer can show the journal as it stood at any tick.
        """
        sections = self.written_sections()
        sections[SECTION_SCRATCH] = "\n".join(
            line for line in self.scratch if line.strip()
        )
        return sections

    def with_sections(self, sections: Mapping[str, str]) -> "Journal":
        """This journal with the five sections replaced and the scratch cleared."""
        return Journal(
            story_so_far=sections.get(SECTION_STORY, self.story_so_far).strip(),
            me=sections.get(SECTION_ME, self.me).strip(),
            others=sections.get(SECTION_OTHERS, self.others).strip(),
            learnings=sections.get(SECTION_LEARNINGS, self.learnings).strip(),
            tomorrow=sections.get(SECTION_TOMORROW, self.tomorrow).strip(),
        )

    def render(self) -> str:
        """The whole journal as markdown, headings and all."""
        parts: list[str] = []
        for heading, body in self.written_sections().items():
            parts.append(f"## {heading}\n{body.strip()}".rstrip() + "\n")
        scratch = "\n".join(line for line in self.scratch if line.strip())
        parts.append(f"## {SECTION_SCRATCH}\n{scratch}".rstrip() + "\n")
        return "\n".join(parts)

    @classmethod
    def parse(cls, text: str) -> "Journal":
        """Read a journal back from its markdown.

        Unknown headings and anything before the first heading are dropped:
        the file is written by this module and read by it, and a stray line is
        not worth carrying into the model's prompt.
        """
        bodies: dict[str, list[str]] = {name: [] for name in ALL_SECTIONS}
        current = ""
        for line in text.splitlines():
            if line.startswith("## "):
                current = line[3:].strip()
                continue
            if current in bodies:
                bodies[current].append(line)
        journal = cls()
        journal.story_so_far = "\n".join(bodies[SECTION_STORY]).strip()
        journal.me = "\n".join(bodies[SECTION_ME]).strip()
        journal.others = "\n".join(bodies[SECTION_OTHERS]).strip()
        journal.learnings = "\n".join(bodies[SECTION_LEARNINGS]).strip()
        journal.tomorrow = "\n".join(bodies[SECTION_TOMORROW]).strip()
        journal.scratch = [line for line in bodies[SECTION_SCRATCH] if line.strip()]
        return journal

    @classmethod
    def load(cls, path: Path, entity_id: str = "") -> "Journal":
        """Read the journal at `path`, seeding and writing the file if it is new."""
        if not path.exists():
            journal = cls.seed(entity_id)
            journal.save(path)
            return journal
        return cls.parse(path.read_text(encoding="utf-8"))

    def save(self, path: Path) -> None:
        """Write the journal to `path`, atomically.

        A planner turn may read the file at any moment, so it is written to a
        temporary file in the same directory and renamed over the old one.
        """
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f"{path.name}.{os.getpid()}.tmp")
        temporary.write_text(self.render(), encoding="utf-8")
        os.replace(temporary, path)


def append_scratch_line(path: Path, text: str, entity_id: str = "") -> None:
    """Append one bullet to `Today's notes`, creating the journal if needed."""
    journal = Journal.load(path, entity_id)
    journal.scratch.append(f"- {text.strip()}")
    journal.save(path)


def read_journal(path: Path, entity_id: str = "") -> str:
    """The whole journal as text, for a prompt."""
    return Journal.load(path, entity_id).render().strip()


# --------------------------------------------------------------------------
# the day log
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class DayLogEntry:
    """One thing that happened between two journal rewrites."""

    tick: int
    kind: str
    text: str
    tool: str = ""


@dataclass
class DayLog:
    """Everything the settler did since the last rewrite, in order.

    Mutable on purpose: the planner appends to one of these all day, and the
    rewrite takes the whole thing and leaves it empty.
    """

    entries: list[DayLogEntry] = field(default_factory=list)

    def add(self, tick: int, kind: str, text: str, tool: str = "") -> None:
        """Record one entry; empty text is not worth a line."""
        if not text.strip():
            return
        self.entries.append(
            DayLogEntry(tick=tick, kind=kind, text=text.strip(), tool=tool)
        )

    def take(self) -> list[DayLogEntry]:
        """Everything recorded so far, removed from the log."""
        taken = self.entries
        self.entries = []
        return taken

    def restore(self, entries: Sequence[DayLogEntry]) -> None:
        """Put a failed rewrite's snapshot back in front of what came since."""
        self.entries = list(entries) + self.entries

    def to_payload(self) -> list[dict[str, Any]]:
        """The log as JSON-safe data, for an agent snapshot (docs/14)."""
        return [
            {
                "tick": entry.tick,
                "kind": entry.kind,
                "text": entry.text,
                "tool": entry.tool,
            }
            for entry in self.entries
        ]

    def load_payload(self, payload: Sequence[Mapping[str, Any]]) -> None:
        """Replace the log with what `to_payload` produced."""
        self.entries = [
            DayLogEntry(
                tick=int(row["tick"]),
                kind=str(row["kind"]),
                text=str(row["text"]),
                tool=str(row["tool"]),
            )
            for row in payload
        ]


def _keep_entries(entries: Sequence[DayLogEntry]) -> list[DayLogEntry]:
    """Drop the entries that only repeat what another entry already says.

    A `look` result is the whole world model in text and changes little from
    call to call, so only the last one is kept; `recall` results are the
    journal itself, which the rewrite is given anyway.
    """
    last_look = -1
    for index, entry in enumerate(entries):
        if entry.kind == KIND_RESULT and entry.tool in LAST_RESULT_ONLY_TOOLS:
            last_look = index
    kept: list[DayLogEntry] = []
    for index, entry in enumerate(entries):
        if entry.kind == KIND_RESULT:
            if entry.tool in DROPPED_RESULT_TOOLS:
                continue
            if entry.tool in LAST_RESULT_ONLY_TOOLS and index != last_look:
                continue
        kept.append(entry)
    return kept


def _entry_lines(entry: DayLogEntry, repeats: int) -> str:
    """`t12 call: look({})`, with later lines of a multi-line text indented."""
    head, *rest = entry.text.splitlines()
    suffix = f" (x{repeats})" if repeats > 1 else ""
    lines = [f"t{entry.tick} {entry.kind}: {head}{suffix}"]
    lines.extend(f"  {line}" for line in rest)
    return "\n".join(lines)


def render_day_log(
    entries: Sequence[DayLogEntry], limit: int = DAY_LOG_CHAR_LIMIT
) -> str:
    """The day log as the text a rewrite is given.

    Duplication is stripped three ways: only the last `look` result survives
    and `recall` results do not, runs of identical entries collapse into one
    line with a repeat count, and the oldest lines are dropped until the whole
    thing fits in `limit` characters.
    """
    kept = _keep_entries(entries)
    blocks: list[str] = []
    index = 0
    while index < len(kept):
        entry = kept[index]
        repeats = 1
        while (
            index + repeats < len(kept)
            and kept[index + repeats].kind == entry.kind
            and kept[index + repeats].text == entry.text
        ):
            repeats += 1
        blocks.append(_entry_lines(entry, repeats))
        index += repeats

    dropped = 0
    while blocks and len("\n".join(blocks)) > limit:
        blocks.pop(0)
        dropped += 1
    if dropped:
        blocks.insert(0, DAY_LOG_DROPPED_NOTE.format(count=dropped))
    return "\n".join(blocks)


# --------------------------------------------------------------------------
# the journal writer
# --------------------------------------------------------------------------


def journal_narrative(
    settler_count: int = items.DEFAULT_SETTLER_COUNT,
) -> str:
    """The journal writer's system prompt for a scenario of this size."""
    return f"""\
{items.island_opening(settler_count)}

The day is over. You are lying down, and this is the moment you write in your
journal. You are given the journal as it stands and a log of everything this
day held: the tools you called, what came back, what you said to yourself at
the end of each turn, and what happened to you. Write the journal you want to
wake up to. Nobody else reads it.

Write in the first person, as yourself. You may rewrite any section completely,
keep a section word for word, or anything in between; what you return replaces
what is there, so a section you do not carry forward is gone.

The sections:
- Story so far: the running history of your life on this island. It grows all
  the time, so compress as you go: the further back a day is, the fewer words
  it deserves. Recent days keep their detail.
- Me: who you are becoming. What you want, what you are good at, what you have
  taken on, where you stand among the others.
- Others: one entry per person you know, by name. What they do, what you agreed
  with them, what you owe them and what they owe you.
- Learnings: what you have found to be true about this island and how it works,
  including the things that did not work and cost you a day.
- Tomorrow: what you mean to do next, concrete enough that you could start on
  it the moment you wake.

Each section must fit in about {SECTION_TOKEN_LIMIT} tokens; you will be asked to cut one that
runs over. `Today's notes`, the scratch lines at the end of the journal, are
yours to fold into the sections above: they are cleared once you have written.
"""


# The default-sized scenario's prompt, for tests and for anything that reads
# the narrative without building a writer.
JOURNAL_NARRATIVE = journal_narrative()


class JournalSections(BaseModel):
    """The five sections a rewrite returns."""

    story_so_far: str = Field(description="The running history of your life here.")
    me: str = Field(description="Who you are becoming and what you want.")
    others: str = Field(
        description="Each person you know, and what stands between you."
    )
    learnings: str = Field(
        description="What you have found to be true about this world."
    )
    tomorrow: str = Field(description="What you mean to do next, concretely.")

    def as_mapping(self) -> dict[str, str]:
        """The sections keyed by the headings they are written under."""
        return {
            SECTION_STORY: self.story_so_far,
            SECTION_ME: self.me,
            SECTION_OTHERS: self.others,
            SECTION_LEARNINGS: self.learnings,
            SECTION_TOMORROW: self.tomorrow,
        }


@dataclass(frozen=True)
class JournalRewrite:
    """One finished rewrite: the new journal and what the call cost."""

    journal: Journal
    token_counts: Mapping[str, int]
    truncated: tuple[str, ...] = ()
    usage: Mapping[str, Any] = field(default_factory=dict)
    model: str = ""


class JournalWriter(Protocol):
    """The language-model half of the journal."""

    async def rewrite(
        self, journal: Journal, day_log: str, entity_id: str, clock_fact: str = ""
    ) -> JournalRewrite:
        """Rewrite the five sections from the journal and the day's log.

        `clock_fact` is what the world clock says about the new moon, so a
        `Tomorrow` section can be written around it (docs/14 section 1).
        """


def over_limit_message(overruns: Mapping[str, int], limit: int) -> str:
    """The retry the model is given when a section is too long."""
    parts = ", ".join(
        f"{name} is {excess} tokens over" for name, excess in sorted(overruns.items())
    )
    return (
        f"Each section must fit in {limit} tokens: {parts}. "
        "Return all five sections again, with those ones cut down."
    )


def build_journal_prompt(
    journal: Journal, day_log: str, entity_id: str, clock_fact: str = ""
) -> str:
    """The user message for one rewrite."""
    parts = [
        f"You are {entity_id}.",
        "Your journal as it stands:\n" + journal.render().strip(),
        "This day's log:\n" + (day_log.strip() or "(nothing was recorded)"),
    ]
    if clock_fact:
        parts.append(clock_fact)
    parts.append("Write your journal now: all five sections.")
    return "\n\n".join(parts)


class ModelJournalWriter:
    """A `JournalWriter` backed by one pydantic-ai agent with no tools.

    The agent has an output validator that asks for a retry when a section is
    over the token limit; a section still over after that retry is cut to the
    limit by the caller, which is recorded rather than hidden.
    """

    def __init__(
        self,
        model_name: str = "",
        planner_model_name: str = "",
        *,
        token_counter: TokenCounter = count_tokens,
        settler_count: int = items.DEFAULT_SETTLER_COUNT,
    ) -> None:
        self.model_name = resolve_journal_model_name(model_name, planner_model_name)
        self.count = token_counter
        self.settler_count = settler_count
        self.agent: Agent[None, JournalSections] = Agent(
            self.model_name,
            output_type=JournalSections,
            system_prompt=journal_narrative(settler_count),
            model_settings=journal_model_settings(self.model_name),
            retries=1,
        )
        self.agent.output_validator(self._reject_long_sections)

    def _reject_long_sections(self, output: JournalSections) -> JournalSections:
        """Ask for one retry when a section is over `SECTION_TOKEN_LIMIT`."""
        overruns = {
            name: self.count(body) - SECTION_TOKEN_LIMIT
            for name, body in output.as_mapping().items()
            if self.count(body) > SECTION_TOKEN_LIMIT
        }
        if overruns:
            raise ModelRetry(over_limit_message(overruns, SECTION_TOKEN_LIMIT))
        return output

    async def rewrite(
        self, journal: Journal, day_log: str, entity_id: str, clock_fact: str = ""
    ) -> JournalRewrite:
        """Ask the model for the five sections, then enforce the token limit."""
        result = await self.agent.run(
            build_journal_prompt(journal, day_log, entity_id, clock_fact)
        )
        usage = usage_from_messages(result.new_messages())
        return finish_rewrite(
            journal, result.output.as_mapping(), self.count, usage, self.model_name
        )


def finish_rewrite(
    journal: Journal,
    sections: Mapping[str, str],
    counter: TokenCounter,
    usage: Mapping[str, Any],
    model_name: str,
) -> JournalRewrite:
    """Cut any section still over the limit and build the rewrite record."""
    kept: dict[str, str] = {}
    truncated: list[str] = []
    counts: dict[str, int] = {}
    for name, body in sections.items():
        text = body.strip()
        if counter(text) > SECTION_TOKEN_LIMIT:
            text = truncate_to_tokens(text, SECTION_TOKEN_LIMIT, counter)
            truncated.append(name)
        kept[name] = text
        counts[name] = counter(text)
    if truncated:
        logger.warning("journal_sections_truncated", sections=truncated)
    return JournalRewrite(
        journal=journal.with_sections(kept),
        token_counts=counts,
        truncated=tuple(truncated),
        usage=usage,
        model=model_name,
    )


def compact_args(args: Mapping[str, Any], limit: int = 400) -> str:
    """Tool arguments as one short JSON line, for the day log."""
    text = json.dumps(args, separators=(",", ":"), default=str, sort_keys=True)
    if len(text) <= limit:
        return text
    return text[:limit] + "...]"


def rewrite_duration_ms(started: float) -> int:
    """Milliseconds since `started` (a `time.monotonic()` reading)."""
    return int((time.monotonic() - started) * 1000)
