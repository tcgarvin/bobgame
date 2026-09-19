# The sleep-time journal

A settler's memory used to be `memory.md`, an append-only list of bullets that
grew all run and was pasted whole into every planner turn. It is now a journal
the settler rewrites itself, once a day, as it falls asleep.

The file keeps its name (`runs/<run>/agents/agent-<id>/memory.md`) and its place
in the prompt; what changed is its shape, who writes it, and when.

## 1. The file

Markdown, exactly six `## ` headings, in this order:

```markdown
## Story so far
## Me
## Others
## Learnings
## Tomorrow
## Today's notes
```

- The first five are **only** written by a journal rewrite. Nothing else ever
  touches them.
- `Today's notes` is the scratch section. The planner's `remember` tool and the
  closing note of a conversation append one `- ` bullet each; a rewrite folds
  them into the five sections above and clears them.
- A missing file is created on the first read or append, seeded with
  `Story so far` = "<id> woke up on a large, wild island with eleven other
  people and no memory of how they got there." and the rest empty.
- `read_memory` (planner) and `read_notes` (conversation) return the rendered
  journal, scratch included, so the planner still sees everything.

`agents/src/agents/jev_agent/journal.py` owns the format: a `Journal` dataclass
with `parse`, `render`, `load` and `save`. Saving is atomic - a temporary file
in the same directory, then `os.replace` - because a planner turn may read the
file at any moment.

## 2. The sections and their budget

| Section | What it holds |
| --- | --- |
| Story so far | A running history, compressed as it grows; most detail on recent days |
| Me | Who I am becoming, what I want, my role among the others |
| Others | One entry per person I know: what they do, what we agreed, what I owe |
| Learnings | What I have found true about the island, including what did not work |
| Tomorrow | Concrete intentions, specific enough that a plan could start from them |

Each section is capped at `SECTION_TOKEN_LIMIT = 600` tokens, counted with
`tiktoken`'s `o200k_base` encoding (loaded lazily, once per process). The
counter is injectable (`TokenCounter = Callable[[str], int]`) so tests use a
fake and never touch the encoding.

Enforcement is two-stage: a pydantic-ai output validator raises `ModelRetry`
naming each over-limit section and by how much (`retries=1`), and a section
still over after that retry is hard-truncated to the limit - by decoding the
first 600 tokens with the real encoder, or by words with any other counter. The
names of the truncated sections go into the trace record.

The writer's system prompt gives the setting, the sections and the limit, and
nothing else: per "tools, not rules" it never tells the settler what to want or
whom to work with.

## 3. The day log

The rewrite's input, held in memory by the planner (`DayLog` in `journal.py`)
and cleared on every rewrite. Entries are `(tick, kind, text)`:

| kind | what |
| --- | --- |
| `call` | every planner tool call, as `tool({"compact":"args"})` |
| `result` | the tool's own result string, **before** `BudgetedToolset` appends the clock, alert and budget lines |
| `reflection` | the turn's closing paragraph |
| `note` | reflex lines and conversation reports queued for the planner |
| `event` | falling asleep, waking, dying, respawning |

Rendering (`render_day_log`) strips duplication three ways:

1. only the **last** `look` result is kept, and `recall` results are dropped
   entirely (both are just the world model or the journal restated);
2. runs of identical consecutive entries collapse to one line with `(xN)`;
3. the whole thing is capped at 60,000 characters, oldest entries dropped
   first, with a `[the oldest N entries ... were dropped to fit]` header.

Each entry renders as `t<tick> <kind>: <text>`, with later lines of a
multi-line text indented two spaces.

## 4. Triggers

- **Sleep start**: the tick loop sees the actor asleep (`_note_sleep_transitions`).
- **Death**: `TickDigest.self_died`.

There is no dawn trigger: an actor still asleep when the day ends does nothing.

The rewrite runs as a background task. **Exactly one is in flight per agent**; a
second trigger while one is running is dropped with a debug log, because the day
log it would have taken is already inside the running call. The day log is
snapshotted and cleared at trigger time.

The task reads the journal from disk, calls the writer, and saves atomically.
On failure it logs a warning, traces `journal_rewrite_failed`, leaves the file
untouched, and puts the snapshotted entries back in front of the day log so the
next rewrite still sees them. It never takes the agent down.

## 5. The planner turn ends with the day

- The `sleep` tool, after the wake returns, spends the whole tool budget (so
  `BudgetedToolset` refuses every later call) and appends a line telling the
  model its turn ends here and that its next turn starts from the journal.
- A death mid-turn does the same through the agent: the note
  `you died at tick N; your turn ends here...` is queued on the ordinary notes
  queue and the budget is spent.
- Falling asleep any other way (a collapse at fatigue 100, or a `sleep` action
  Jev chose inside a stint) does the same, with the note `you fell asleep on
  <place> at tick N; your turn ends here...`. No note is queued while the
  `sleep` tool itself is waiting, since its own result says so.
- **No turn while the body is out**: `Planner.take_turn` first awaits
  `AgentBridge.await_active()`, which returns on the first tick the settler is
  both alive and awake. So a turn never starts, and its prompt is never built,
  while the settler is asleep, collapsed or waiting to respawn; the wake or
  respawn it waited through is what resets the history below.
- **History reset**: once a sleep has ended or a respawn has happened,
  `Planner.history` is emptied and `history_reset` is traced with `reason`
  (`woke` or `respawned`). A wake or respawn inside a turn resets at that
  turn's end; one that lands between turns resets before the next turn
  starts. Otherwise the model would carry both the journal and the raw
  messages the journal was distilled from.
- The next turn's `build_prompt` first awaits `AgentBridge.await_journal()`, a
  bounded wait (`JOURNAL_WAIT_SECONDS = 120`) on the rewrite in flight; on
  timeout it logs a warning and reads the file as it stands.

## 6. The model

`--journal-model`, else `$JOURNAL_MODEL`, else whatever the planner resolved to
(default `qwen/qwen3.7-flash`). Settings are `planner_model_settings` with
`openrouter_reasoning={"effort": "low"}` always on: the journal is written once
a day and its quality matters more than its latency.

`JournalWriter` is a `Protocol` (one `rewrite` method) so tests inject a fake;
`ModelJournalWriter` is the pydantic-ai implementation, one agent, no tools,
structured five-field output.

## 7. Trace and cost

`planner.jsonl.gz` gains two records:

```json
{"event": "journal_rewrite", "entity_id": "ada", "tick": 612,
 "trigger": "sleep", "sections": {"Me": 210, "Tomorrow": 143},
 "journal": {"Story so far": "…", "Me": "…", "Others": "…", "Learnings": "…",
             "Tomorrow": "…", "Today's notes": ""},
 "truncated": ["Story so far"], "duration_ms": 4200,
 "usage": {"input_tokens": 9000, "requests": 1, "cost_usd": 0.002},
 "model": "openrouter:qwen/qwen3.7-flash"}
```

`sections` stays the token count per section; `journal` is the text, all six
sections keyed by their heading (`Journal.all_sections()`). After a rewrite
`Today's notes` is empty, because the rewrite folded it in.

`turn_start` carries the same `journal` block beside its `prompt`: the journal
the turn was built from, read once in `build_prompt`. A `remember` line
therefore shows up in the next turn's `turn_start` and needs no record of its
own. Together the two events make the journal readable at any tick of a run,
which is what the viewer's agent panel shows (`agent_detail.journal` in
[docs/07_replay.md](07_replay.md)).

```json
{"event": "journal_rewrite_failed", "entity_id": "ada", "tick": 612,
 "trigger": "sleep", "error": "...", "duration_ms": 900}
```

`CostLedger` gains `journal_usd` and `journal_rewrites`, both in `as_json()`
and in `total_usd()`, so the viewer's live cost readout covers it.
`tools/analyze_run.py` adds a `journal` column to the cost totals and the
per-agent table, and a `== journal ==` section with rewrites, failures,
triggers and truncated sections per settler
(see [docs/11_cost_accounting.md](11_cost_accounting.md)).

## 8. Tests

`agents/tests/test_journal.py` covers the format, `all_sections()` and the
token cap and the day log; `test_planner.py` the `remember` target, the `sleep` tool's budget, the
history reset and the prompt's wait; `test_agent.py` the triggers, the
single-flight rule, the failure path and the `journal` block on the rewrite
record; `test_planner.py` also the `journal` block on `turn_start`;
`world/tests/test_replay.py` the `agent_detail.journal` payload, the
`memory.md` fallback and the live re-read; `tools/tests/test_analyze_run.py` the
roll-up. No test makes a model call.
