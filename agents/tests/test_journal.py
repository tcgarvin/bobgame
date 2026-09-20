"""The sleep-time journal: the file format, the day log and the rewrite.

Contract: docs/12_sleep_journal.md. No test here makes a model call; the
writer is a fake and the token counter is word count.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

import pytest

from agents.jev_agent.journal import (
    ALL_SECTIONS,
    DAY_LOG_CHAR_LIMIT,
    KIND_CALL,
    KIND_EVENT,
    KIND_NOTE,
    KIND_REFLECTION,
    KIND_RESULT,
    SECTION_LEARNINGS,
    SECTION_ME,
    SECTION_OTHERS,
    SECTION_SCRATCH,
    SECTION_STORY,
    SECTION_TOKEN_LIMIT,
    SECTION_TOMORROW,
    DayLog,
    DayLogEntry,
    Journal,
    JournalRewrite,
    JournalSections,
    append_scratch_line,
    compact_args,
    count_tokens,
    finish_rewrite,
    over_limit_message,
    read_journal,
    render_day_log,
    truncate_to_tokens,
)


def words(text: str) -> int:
    """A token counter for tests: one token per whitespace-separated word."""
    return len(text.split())


class FakeJournalWriter:
    """A `JournalWriter` that returns fixed sections and records its calls."""

    def __init__(
        self,
        sections: Mapping[str, str] | None = None,
        error: Exception | None = None,
    ) -> None:
        self.sections = dict(
            sections
            or {
                SECTION_STORY: "Day one happened.",
                SECTION_ME: "I am a builder.",
                SECTION_OTHERS: "bo mines stone.",
                SECTION_LEARNINGS: "Wolves bite.",
                SECTION_TOMORROW: "Chop six wood.",
            }
        )
        self.error = error
        self.calls: list[tuple[str, str]] = []
        self.clock_facts: list[str] = []

    async def rewrite(
        self, journal: Journal, day_log: str, entity_id: str, clock_fact: str = ""
    ) -> JournalRewrite:
        self.calls.append((day_log, entity_id))
        self.clock_facts.append(clock_fact)
        if self.error is not None:
            raise self.error
        return finish_rewrite(
            journal, self.sections, words, {"cost_usd": 0.001}, "fake-model"
        )


# --- the file format --------------------------------------------------------


def test_the_journal_renders_every_heading_in_order() -> None:
    text = Journal.seed("ada").render()
    headings = [line[3:] for line in text.splitlines() if line.startswith("## ")]
    assert headings == list(ALL_SECTIONS)


def test_parse_and_render_round_trip_with_empty_sections_and_scratch() -> None:
    journal = Journal(
        story_so_far="Two days in.\nStill hungry.",
        me="A gatherer.",
        others="",
        learnings="",
        tomorrow="Build the wall.",
        scratch=["- bo owes me 3 planks", "- the lake is east"],
    )

    parsed = Journal.parse(journal.render())

    assert parsed == journal
    assert Journal.parse(Journal().render()) == Journal()


def test_all_sections_keys_every_heading_and_joins_the_scratch_lines() -> None:
    journal = Journal(
        story_so_far="Two days in.",
        me="A gatherer.",
        others="bo chops wood.",
        learnings="Wolves bite.",
        tomorrow="Build the wall.",
        scratch=["- bo owes me 3 planks", "", "- the lake is east"],
    )

    sections = journal.all_sections()

    assert list(sections) == list(ALL_SECTIONS)
    assert sections[SECTION_STORY] == "Two days in."
    assert sections[SECTION_SCRATCH] == "- bo owes me 3 planks\n- the lake is east"


def test_a_rewritten_journal_has_no_scratch_left_in_its_sections() -> None:
    journal = Journal(story_so_far="Old.", scratch=["- a note"])

    sections = journal.with_sections({SECTION_STORY: "New."}).all_sections()

    assert sections[SECTION_STORY] == "New."
    assert sections[SECTION_SCRATCH] == ""


def test_a_missing_journal_is_seeded_with_the_premise(tmp_path: Path) -> None:
    path = tmp_path / "memory.md"

    journal = Journal.load(path, "ada")

    assert journal.story_so_far.startswith("ada woke up on a large, wild island")
    assert "with the others who woke there" in journal.story_so_far
    assert "eleven" not in journal.story_so_far
    assert journal.me == journal.others == journal.learnings == journal.tomorrow == ""
    assert journal.scratch == []
    assert path.exists(), "the seed is written out, not only returned"
    assert Journal.parse(path.read_text(encoding="utf-8")) == journal


def test_reading_the_journal_gives_the_whole_file_including_scratch(
    tmp_path: Path,
) -> None:
    path = tmp_path / "memory.md"
    append_scratch_line(path, "bo will bring stone", "ada")

    text = read_journal(path, "ada")

    assert f"## {SECTION_SCRATCH}" in text
    assert "- bo will bring stone" in text
    assert "ada woke up" in text


def test_saving_is_atomic_and_leaves_no_temporary_behind(tmp_path: Path) -> None:
    path = tmp_path / "memory.md"
    Journal.seed("ada").save(path)

    Journal(story_so_far="Rewritten.").save(path)

    assert Journal.parse(path.read_text(encoding="utf-8")).story_so_far == "Rewritten."
    assert [entry.name for entry in tmp_path.iterdir()] == ["memory.md"]


def test_scratch_lines_accumulate_and_do_not_touch_the_written_sections(
    tmp_path: Path,
) -> None:
    path = tmp_path / "memory.md"
    Journal(story_so_far="Day one.", me="A builder.").save(path)

    append_scratch_line(path, "first")
    append_scratch_line(path, "second")

    journal = Journal.load(path)
    assert journal.scratch == ["- first", "- second"]
    assert journal.story_so_far == "Day one."
    assert journal.me == "A builder."


# --- the token cap ----------------------------------------------------------


def test_the_real_counter_and_truncation_agree_on_the_limit() -> None:
    long_text = "wood " * 2_000

    cut = truncate_to_tokens(long_text, SECTION_TOKEN_LIMIT, count_tokens)

    assert count_tokens(cut) <= SECTION_TOKEN_LIMIT
    assert count_tokens(long_text) > SECTION_TOKEN_LIMIT


def test_a_fake_counter_truncates_by_words() -> None:
    assert truncate_to_tokens("a b c d e", 3, words) == "a b c"


def test_the_retry_message_names_every_over_limit_section() -> None:
    message = over_limit_message({SECTION_ME: 12, SECTION_STORY: 3}, 600)

    assert "Me is 12 tokens over" in message
    assert "Story so far is 3 tokens over" in message
    assert "600 tokens" in message


def test_a_section_still_over_the_limit_is_cut_and_recorded() -> None:
    sections = {
        SECTION_STORY: "one two three four five six",
        SECTION_ME: "short",
        SECTION_OTHERS: "",
        SECTION_LEARNINGS: "",
        SECTION_TOMORROW: "",
    }

    # 200 "tokens" per word, so four words already blow the 600-token limit.
    counter = lambda text: words(text) * 200  # noqa: E731
    rewrite = finish_rewrite(Journal(), sections, counter, {}, "m")

    assert rewrite.truncated == (SECTION_STORY,)
    assert rewrite.journal.story_so_far == "one two three"
    assert rewrite.token_counts[SECTION_STORY] == SECTION_TOKEN_LIMIT
    assert rewrite.token_counts[SECTION_ME] == 200


def test_the_model_output_validator_asks_for_a_retry_then_accepts() -> None:
    from pydantic_ai import ModelRetry

    from agents.jev_agent.journal import ModelJournalWriter

    writer = ModelJournalWriter("test", "test", token_counter=lambda _text: 10_000)
    sections = JournalSections(
        story_so_far="x", me="x", others="x", learnings="x", tomorrow="x"
    )

    with pytest.raises(ModelRetry) as raised:
        writer._reject_long_sections(sections)
    assert "tokens over" in str(raised.value)

    writer.count = words
    assert writer._reject_long_sections(sections) is sections


# --- the day log ------------------------------------------------------------


def entry(tick: int, kind: str, text: str, tool: str = "") -> DayLogEntry:
    return DayLogEntry(tick=tick, kind=kind, text=text, tool=tool)


def test_only_the_last_look_result_survives_and_recall_results_do_not() -> None:
    entries = [
        entry(1, KIND_CALL, "look({})"),
        entry(1, KIND_RESULT, "first look", tool="look"),
        entry(2, KIND_RESULT, "the journal", tool="recall"),
        entry(3, KIND_RESULT, "last look", tool="look"),
        entry(4, KIND_RESULT, "crafted plank", tool="craft"),
    ]

    rendered = render_day_log(entries)

    assert "first look" not in rendered
    assert "the journal" not in rendered
    assert "t3 result: last look" in rendered
    assert "t4 result: crafted plank" in rendered
    assert "t1 call: look({})" in rendered, "the calls themselves are kept"


def test_consecutive_identical_entries_collapse_into_one_line() -> None:
    entries = [
        entry(1, KIND_RESULT, "extract -> accepted", tool="x"),
        entry(2, KIND_RESULT, "extract -> accepted", tool="x"),
        entry(3, KIND_RESULT, "extract -> accepted", tool="x"),
        entry(4, KIND_RESULT, "extract -> failed", tool="x"),
    ]

    lines = render_day_log(entries).splitlines()

    assert lines == [
        "t1 result: extract -> accepted (x3)",
        "t4 result: extract -> failed",
    ]


def test_a_multi_line_entry_is_indented_under_its_header() -> None:
    rendered = render_day_log([entry(7, KIND_REFLECTION, "Line one.\nLine two.")])

    assert rendered == "t7 reflection: Line one.\n  Line two."


def test_the_oldest_entries_are_dropped_to_fit_the_character_cap() -> None:
    entries = [
        entry(tick, KIND_EVENT, f"event {tick} " + "x" * 40) for tick in range(20)
    ]

    rendered = render_day_log(entries, limit=200)

    header, _, body = rendered.partition("\n")
    assert "were dropped to fit" in header
    assert len(body) <= 200
    assert "event 19" in rendered
    assert "event 0 " not in rendered


def test_the_day_log_is_emptied_by_take_and_restored_in_front() -> None:
    log = DayLog()
    log.add(1, KIND_CALL, "look({})")
    log.add(2, KIND_NOTE, "")

    taken = log.take()
    assert [e.text for e in taken] == ["look({})"], "empty text is not a line"
    assert log.entries == []

    log.add(3, KIND_EVENT, "you died at tick 3")
    log.restore(taken)
    assert [e.tick for e in log.entries] == [1, 3]


def test_tool_arguments_are_compacted_for_the_log() -> None:
    assert compact_args({"b": 1, "a": "x"}) == '{"a":"x","b":1}'
    assert compact_args({"a": "y" * 900}).endswith("...]")
    assert len(compact_args({"a": "y" * 900})) <= 404


# --- the rewrite -----------------------------------------------------------


async def test_a_rewrite_replaces_the_sections_and_clears_the_scratch(
    tmp_path: Path,
) -> None:
    path = tmp_path / "memory.md"
    journal = Journal(story_so_far="Old story.", scratch=["- bo owes me planks"])
    journal.save(path)
    writer = FakeJournalWriter()

    rewrite = await writer.rewrite(Journal.load(path), "t1 event: you slept", "ada")
    rewrite.journal.save(path)

    saved = Journal.load(path)
    assert saved.story_so_far == "Day one happened."
    assert saved.tomorrow == "Chop six wood."
    assert saved.scratch == [], "the day's notes were folded in and cleared"
    assert writer.calls == [("t1 event: you slept", "ada")]
    assert rewrite.usage == {"cost_usd": 0.001}
    assert rewrite.model == "fake-model"
    assert rewrite.token_counts[SECTION_STORY] == 3


async def test_a_failed_rewrite_leaves_the_journal_alone(tmp_path: Path) -> None:
    path = tmp_path / "memory.md"
    Journal(story_so_far="Old story.").save(path)
    writer = FakeJournalWriter(error=RuntimeError("no route to host"))

    with pytest.raises(RuntimeError):
        await writer.rewrite(Journal.load(path), "log", "ada")

    assert Journal.load(path).story_so_far == "Old story."


def test_the_char_cap_default_is_the_documented_one() -> None:
    assert DAY_LOG_CHAR_LIMIT == 60_000
    assert SECTION_TOKEN_LIMIT == 600
