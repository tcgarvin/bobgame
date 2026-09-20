# Conversations, giving, and the reflex brief

Contract for three linked mechanics added on 2026-09-17. World code, agent code
and the viewer are all built against this file. Where this file and the code
disagree, fix one of them in the same change.

Design rule ([tools, not rules]): agent-facing text describes what a mechanic
does and how to operate it. It never says when to use it or what to conclude.

## 1. Why

- A planner turn lasts 150 to 330 ticks because stints run inside it, and a
  planner sees only the last five utterances it heard. Replies arrive minutes
  late, so speech collapsed into wolf alarms. A synchronous conversation fixes
  the latency.
- A settler whose planner is thinking submits `wait` every tick. When a wolf
  arrives, the planner must notice the alert and write a fighting brief while
  the settler is bitten. A pre-registered reflex brief removes the language
  model from that loop. Conversations would be unsafe without it.
- A trade cannot close unless items can change hands directly.

## 2. Conversations (world)

A conversation is a **world object** of type `conversation` on its **anchor
tile**. It is non-blocking, belongs to neither building layer, cannot be
extracted, and is recorded and replayed like any other object.

### 2.1 Object state (all values are strings, as for every object)

| key | value |
|---|---|
| `participants` | JSON list of entity ids in join order, opener first |
| `speaker` | entity id whose turn it is; `""` while fewer than 2 participants |
| `turn_started` | tick at which the current turn began |
| `opened_tick` | tick the conversation opened |
| `opened_by` | entity id |
| `utterances` | count of `speak` actions so far |
| `passes` | consecutive passes (explicit or timed out); a `speak` resets it |
| `transcript` | JSON list of the last 12 `{"tick": int, "speaker": str, "text": str}`; the opening line is entry 0 |

### 2.2 Constants (`bobgame_rules.social`, re-exported by `world/.../items.py` and `agents/.../items.py`)

| name | value | meaning |
|---|---|---|
| `CONVERSATION` | `"conversation"` | object type |
| `CONVERSATION_CHANNEL` | `"conversation"` | utterance channel |
| `CONVERSATION_MAX_PARTICIPANTS` | 4 | seats |
| `CONVERSATION_TURN_TICKS` | 10 | a turn not used in this many ticks counts as a pass |
| `CONVERSATION_LONELY_TICKS` | 20 | a conversation with one participant closes after this many ticks |
| `CONVERSATION_MAX_UTTERANCES` | 40 | closes after this many `speak` actions |
| `CONVERSATION_TEXT_LIMIT` | 300 | characters per line; longer text is truncated |
| `CONVERSATION_TRANSCRIPT_KEPT` | 12 | lines kept in object state |

### 2.3 `ConverseIntent` (proto field `converse`, action type `"converse"`)

One intent per tick as always. The actions are `open`, `hail` (section 9),
`join`, `speak`, `pass` and `leave`, applied in that order each tick.
`EntityActed.details` for a success starts with the action name (`open
conv_12`, `hail conv_12 dov`, `join conv_12`, `speak`, `pass`, `leave`); a
failure carries the reason.

- **open** (`direction`, `text`): the anchor is the neighbouring tile in
  `direction`. It must be walkable, hold no blocking object, no entity and no
  other conversation. The opener must not already be in a conversation. `text`
  is required (non-empty after trimming) and is emitted as an utterance on the
  `local` channel with `conversation_id` set, so everyone within earshot hears
  it and where it came from.
- **join** (`conversation_id`): the entity must be adjacent (Chebyshev 1) to
  the anchor and not on it, the conversation must have a free seat, and the
  entity must not already be in a conversation.
- **speak** (`text`): only the current `speaker` may speak. Emits an utterance
  on channel `conversation` with `conversation_id` set and the speaker's
  position. Earshot is the `local` radius, so bystanders can listen in.
  Advances the turn.
- **pass**: only the current `speaker`. Advances the turn.
- **leave**: any participant, any tick.

### 2.4 Turn order and lifecycle (run once per tick, after movement and combat)

1. Remove participants who are dead, who submitted `leave`, or who are no
   longer adjacent to the anchor. Anything else (eating, giving, attacking,
   waiting) keeps the seat.
2. Turn order is round robin in join order. When the second participant joins,
   the opener gets the first turn. If the current speaker is removed, the turn
   moves to the next participant.
3. A turn unused for `CONVERSATION_TURN_TICKS` counts as a pass.
4. The conversation closes (object removed) when any of these holds:
   - it has had 2 or more participants and now has fewer than 2;
   - every current participant has passed in a row (a full round of passes);
   - `utterances` reached `CONVERSATION_MAX_UTTERANCES`;
   - it has had only its opener for `CONVERSATION_LONELY_TICKS`.

   The world's own reasons, as `_closing_reason` returns them, are `empty`,
   `alone`, `utterance_cap`, `all_passed` and `nobody_joined`. There is one
   more, from outside the lifecycle: on a new-moon night
   ([docs/14](14_new_moon_and_saves.md), section 1) `moon.apply_new_moon`
   closes **every** open conversation with the reason `new_moon` on the tick
   everyone falls asleep.
5. Conflicts: two `open` intents for the same anchor in one tick, or more
   `join` intents than free seats, are resolved by lexicographic entity id like
   every other conflict.

A structure may not be placed on an anchor tile while the conversation exists
(the conversation object occupies the structure layer, so `place` refuses with
`target already holds an object`). A ground-layer item (road, floor) may still
be laid there. Wolves never join conversations.

Conversation object ids read `conv_<n>`; the object type is `conversation`.

## 3. `GiveIntent` (proto field `give`, action type `"give"`)

`target_entity_id`, `kind`, `amount` (default 1). The target must be a living
non-wolf entity that is adjacent (Chebyshev 1) or a participant in the same
conversation. The giver must hold `amount` of `kind`; a wielded item that would
drop below 1 is unequipped. Success details: `gave 3 stone to mira`. The target
sees the same `EntityActed` event (it is about another entity, so observation
filtering must let the target see it) and its inventory changes that tick.

## 4. Agent side

### 4.1 Modes

`JevAgent.mode` is one of `planning`, `stint`, `reflex`, `conversation`. Mode is
reported to the viewer through the existing status channel.

### 4.2 Reflex brief

- Planner tools: `set_reflex(instruction, success_condition, max_ticks,
  trigger_distance, notes="", shouts=())` and `clear_reflex()`.
  `trigger_distance` is 1 to 8 tiles. Stored on the agent, saved to
  `$BOBGAME_RUN_DIR/agents/agent-<id>/reflex.json`, reloaded at start, and
  shown in every planner turn prompt ("Your reflex brief: ..." or "You have no
  reflex brief.").
- There is no default reflex.
- **Trigger** (code, checked every tick while alive): a living wolf within
  `trigger_distance`, or damage from an attacker on the tick just observed.
- **Cooldown**: after a reflex stint ends, the wolf-distance trigger is
  ignored for `REFLEX_COOLDOWN_TICKS` (10). Damage ignores the cooldown.
- **Where it fires**
  - *planning* (the planner is thinking, or a single-tick tool or `wait` is in
    flight): the reflex stint starts on this tick. In-flight and queued direct
    actions resolve with `... -> interrupted: reflex stint started`. Direct
    actions requested while the reflex runs get the same answer at once. A
    queued `start_stint` waits until the reflex has ended.
  - *conversation*: the reflex stint starts; the seat is kept for as long as
    the world keeps it (section 2.4). When the reflex ends and the agent is
    still a participant, conversation mode resumes.
  - *driver stints* (`build`, and `travel_to` when code-driven): the driver
    stint ends with reason `reflex`, the reflex stint runs, and the planner's
    tool call returns after both, with the reflex report appended.
  - *ordinary Jev stints*: never pre-empted. Jev already sees the threat block
    and acts under the brief the planner wrote.
- **End**: no living wolf within view for `REFLEX_CLEAR_TICKS` (3) consecutive
  ticks (reason `threat_gone`), or the usual stint endings (`ticks_exhausted`,
  `death`, `eject`, `repeated_failure`).
- **What the planner sees**: the next tool result, and the next turn prompt,
  carry `[reflex ran ticks A-B: ended because R; health X -> Y]`.
- **Trace**: `stint_start` lines carry `"kind": "reflex"` or `"kind":
  "stint"`; reflex starts also carry `"trigger": "wolf_near" | "damage"` and
  `"interrupted": "planning" | "conversation" | "driver_stint"`.

### 4.3 Conversations

- World model: conversations are parsed from `conversation` objects.
  `WorldModel.my_conversation()` returns the one this actor sits in.
- Planner tools
  - `open_conversation(direction, opening_line)`: opens, then blocks until the
    conversation is over; returns the conversation report.
  - `join_conversation(conversation_id)`: walks to a free tile next to the
    anchor (code-owned), joins, then blocks like `open_conversation`.
  - `give(entity_id, kind, amount=1)`: single-tick tool.
  - `look` lists conversations in view: id, anchor, participants, free seats.
- Jev option `join_conversation:<id>`: offered when a conversation with a free
  seat is in view and the actor is in none. Adjacent to the anchor it is the
  join intent; otherwise it is a code-owned walk there, like the heard-shout
  option. When a join succeeds during a stint, the stint ends with reason
  `joined_conversation`, conversation mode begins, and `start_stint` returns
  after the conversation with the conversation report appended.
- **Conversation mode**: the tick loop submits `wait` except on the actor's
  turn. On its turn it asks the *converser*, a separate small pydantic-ai agent
  on the planner's model with structured output
  `{action: speak | pass | leave | give | eat, text, give_to, kind, amount}` and
  no tools. `eat` is an `EatIntent` and, like `give`, keeps the turn. `give` is
  submitted as a `GiveIntent` and does not use up the turn;
  the converser is asked again on the next tick. A `give` with no receiver or
  no item kind is refused in code, without an intent and without spending the
  turn; the reason becomes an outcome line and the converser is asked again.
  The call runs as a background task so the tick loop never misses a deadline.
  Every tick, including ticks that are not this actor's turn, a call whose turn
  the world has moved past is settled: a finished answer is traced `stale` and
  thrown away, a running one is cancelled (traced `stale` with `cancelled`).
  `latency_ms` is measured inside the call itself, so it is the length of the
  model call and not the wait until the tick loop collected it.
- The converser's prompt holds: the actor's name, who is seated, the full
  transcript heard so far (one entry per spoken line: the same speaker and text
  within two ticks is the object-state and the heard copy of one line), the
  actor's stats and inventory as they are on this tick, what the world made of
  every move this actor has already made on this turn ("Your moves so far this
  turn", each line `t<tick> <move> -> <the world's own details>`), its notes
  file, its reflex brief, and the threat alert line when there is one. Its
  system prompt gives the setting and the physics in section 2 and nothing else.
- **After the conversation**: one more model call asks the actor what, if
  anything, it wants to keep from the conversation. A non-empty answer is
  appended to `memory.md` as `- [conversation, tick N, with a, b] <text>`.
- **Conversation report** (returned to the planner): id, ticks, participants,
  why it ended for this actor, the transcript, items given and received, and
  the note written. The end reasons are the `END_*` constants in
  `agents/.../conversation/protocol.py`: `closed`, `left`, `removed`, `died`,
  `nobody joined`, plus `asleep` and `new_moon` — the actor fell asleep with a
  seat, so the session ends there, the second when the new moon put everyone to
  sleep and closed every conversation on the same tick (docs/14). Those two
  carry a sentence of physics into the report (`SLEEP_END_TEXT`), and
  `sleep_end_reason(model)` picks between them from the clock.
- **Trace**: `agents/agent-<id>/conversations.jsonl.gz` with events
  `conversation_start`, `turn` (prompt, move, latency, and `note` `stale` for a
  dropped call, with `cancelled` when it was still running), `conversation_end`
  (report fields, note).

### 4.4 Prompt text

The planner's system prompt gains a "Conversations" paragraph and a "Reflex"
paragraph. They state the physics above with the numbers and how to operate the
tools. They do not say what a good reflex is, when to talk, or what to talk
about. The reflex paragraph shows two example reflexes of opposite shape (one
walks away, one stands and attacks) because settlers copy a lone example: in
the first tools-not-rules run 144 of 510 shouts were the prompt's literal
example.

## 5. Viewer

- A `conversation` object renders as a marker on the anchor tile with a line to
  each participant.
- Utterances on the `conversation` channel show as speech bubbles like `local`
  speech.
- The agent panel shows the `reflex` and `conversation` modes, and for a
  conversation the transcript from object state.
- Replay: selecting a conversation in the object inspector shows its state.

## 6. Analysis

`tools/analyze_run.py` (over `tools/runlib/social.py`) reports conversations
(opened, hailed, joined, utterances, how they ended, notes written), gives
(count, kinds, pairs), and reflexes (settlers
with one registered, firings by trigger and by what they interrupted, outcomes,
ticks from trigger to first action), and adds conversation and reflex moments
to the notable-moments list with deep links.

## 7. First live results (2026-09-17)

Two 15-minute runs, about 440 ticks each.

- **Wolves on** (`20260917-172706-settlement`): all 12 settlers registered a
  reflex, the first at tick 32 and most between ticks 113 and 237. It fired 23
  times, always during planning, with a mean of 0.2 ticks from trigger to first
  action. Endings: 17 `threat_gone`, 4 `death`, 2 `eject`. Every reflex was a
  fight; none chose flight. 9 of 10 wolves died. Nobody opened a conversation.
- **Wolves off** (`20260917-174230-settlement_peaceful`): one conversation,
  opened at tick 144, filled all four seats (five settlers over its life), ran
  248 ticks and ended at the 40-line cap. 16 gives. Each participant wrote a
  note, and three of the five notes are judgements of another settler's
  reliability. One settler starved in its seat.
- Defects the transcript exposed, fixed the same day: seated settlers narrated
  leaving to chop wood because the converser prompt never said a seated body
  cannot act, and the converser could not eat.
- Still open: some converser calls took 34 to 48 s, longer than the 20 s turn,
  so those turns were lost as passes. The conversation only ended at the line
  cap: nobody chose `leave` or `pass` once the plan was agreed. One run each is
  not enough to say wolves are why the first run had no conversation.

## 8. Invitations: "open to talk" — REMOVED

**Removed on 2026-09-20 from the world, the proto (field numbers reserved), the
agents, the viewer and the analysis tools.** What this section described —
`SayIntent.open_to_talk`, `Entity.open_to_talk`, the `accept` converse action
and the invitation marker — no longer exists; conversations are started by
hailing (section 9). The record of the mechanic and of its removal is in
[../CHANGELOG.md](../CHANGELOG.md).

## 9. Hailing: starting a conversation unilaterally (added 2026-09-19)

### 9.1 Why

In the runs after section 8 shipped, `talk_to` almost always came back with
`no invitation from dov; settlers open to talk right now: nobody`. An
invitation lives for `INVITATION_TICKS` (40) ticks and planner turns are 100 or
more ticks apart, so two settlers were almost never in sync: starting a
conversation needed both of them to want one inside the same 40 ticks. A hail
removes the synchronisation: a settler walks up to another and addresses it,
like tapping someone on the shoulder, and the conversation starts.

### 9.2 World

`ConverseIntent` gains the action `hail`, carrying `target_entity_id` (the
settler addressed) and `text` (the opening line, same limit as any other line;
the proto needs no new field). It succeeds when, checked in this order and each
a failure reason in `EntityActed.details`:

- `text` is non-empty after trimming (`an opening line is required`);
- the target exists and is not a wolf (`there is no settler called <id>`);
- the target is alive (`<id> is dead`);
- the hailer is awake (`you are asleep`) and so is the target (`<id> is
  asleep`; a collapsed settler is asleep);
- neither holds a seat (`already in a conversation`, `<id> is already in
  conversation conv_N`);
- they are adjacent, Chebyshev 1 (`not next to <id>`);
- the target is out of its hail cooldown (`<id> was in a conversation N ticks
  ago and cannot be hailed for another M ticks`);
- a free anchor tile exists next to both (`_shared_anchor`, ascending
   `(x, y)`) (`no free tile next to you both`).

**Hail cooldown.** `HAIL_COOLDOWN_TICKS` (60). `Entity.last_conversation_end_tick`
is stamped for every participant when a conversation drops them or closes
(`Entity.with_conversation_ended`, read through `hail_cooldown_left`), so a
settler cannot be walked straight back into another conversation. It is world-
only state: not in the proto, the viewer payload or the recorded tick, because
only the world enforces it and the planner learns of it from the failure
reason. `open` and `join` are unaffected by it.

**Effect.** The conversation is created on the anchor with
`participants = [hailer, target]`, `opened_by = hailer`, transcript entry 0 =
the hailer's line at this tick, and `speaker = target`, because the hailer has
already spoken. The opening line also goes out as an ordinary `local` utterance with
`conversation_id` set, exactly as `open`'s does, so bystanders hear it. Two
`EntityActed`s are emitted: `hail conv_N <target>` for the hailer and
`hailed conv_N <hailer>` for the target — a distinct word, so the target's
agent can tell a seat it never asked for from one it did. Nothing else about
turn order, leaving or closing changes.

### 9.3 Agent side

- `agents/.../items.py` re-exports `ACTION_HAIL`, `ACTION_HAILED` and
  `HAIL_COOLDOWN_TICKS` from `bobgame_rules.social`, the same definitions the
  world enforces.
- `joined_conversation` accepts `hail` and `hailed` as seat-taking actions, and
  `UNASKED_VIA = (VIA_HAILED,)` is what `JevAgent._detect_join`
  treats as a seat the actor did not ask for: the in-flight and queued
  single-tick actions are answered with `INTERRUPTED_BY_CONVERSATION`, a
  running stint (ordinary or driver) ends with `joined_conversation`, and a
  queued stint waits. `conversation_start` records `via`: `hail` for the
  hailer, `hailed` for the target. The reflex fires inside such a conversation
  like any other (section 4.2).
- Planner tool `talk_to(entity_id, opening_line, purpose, max_ticks=40)`
  (section 11.4 added `purpose`). It walks next to
  the settler with the `ApproachDriver`, re-aiming at its latest known
  position for at most `WALK_LEGS` (3) legs out of one shared tick budget,
  then submits the hail and blocks until the conversation ends. Failures return
  the world's reason verbatim plus where the settler was last seen.
- Planner tool `start_stint(..., hails=[{"settler": "dov", "line": "Dov, can
  we split the wall work?"}])`. **Brief hails**, added 2026-09-19:
  `Brief.hails: tuple[BriefHail, ...]` (`BriefHail(settler, line, purpose)`
  lives in `briefs.py`, beside `Option` and `TravelState`: the shared
  vocabulary the `options/`, `stint/`, `planner/` and `agent/` packages all
  import, which imports none of them). At most `MAX_BRIEF_HAILS` (3) per brief; each `line` is at most
  `CONVERSATION_TEXT_LIMIT` characters; each `settler` must be one this actor
  has seen and must not be itself. `_validated_hails` raises `ModelRetry`
  naming who it has met. `ReflexBrief` has no hails. The payload and the trace
  carry `"hails": [{"settler": ..., "line": ...}]`.
- **Jev option** `hail:<settler>`, one per live brief hail, in its own
  `OPTION_SECTIONS` entry (`hail`, between `survival` and `conversation`) so
  `MAX_OPTIONS` truncation cannot drop it. Adjacent to the settler it is the
  `ConverseIntent(action="hail", target_entity_id, text=line)`, described as
  `say "<line>" to dov, next to you: a conversation with the two of you starts
  and dov answers first`; further off it is a code-owned `stop_adjacent` walk
  to the settler's latest known position, described as `walk to dov, 12 tiles
  away, to say "<line>" and start a conversation with them`. It is not offered
  while this actor holds a seat, nor for a settler that is dead, asleep, seated
  in a conversation, or has never been seen.
- **Spent hails.** `Stint` drops a hail from the offer for the rest of the
  stint once it succeeded, and once the world has refused it
  `HAIL_REFUSAL_LIMIT` (2) times. Both facts reach the `StintReport` `notable`
  list: `hailed dov at tick N` and `hail to dov refused: <world reason>`. Only
  the previous tick's option says which settler a converse failure was about,
  so `_absorb_hail_outcomes` reads `self._last_option`.
- A successful hail by Jev ends the stint with `joined_conversation`, exactly
  as a `join` does, and `start_stint` returns after the conversation with the
  report appended.
- **Jev state**: the `brief` block carries `hails` as
  `['say to dov: "<line>"']`. When to hail is the instruction's business, as
  with shouts.

### 9.4 Analysis

`tools/analyze_run.py` counts hails:
`hails: <attempted> attempted, <succeeded> succeeded`, `opened by hail: n` and
a `hails refused` bucket count. A second line reports the brief hails:
`brief hails granted: n` (summed over every `stint_start` brief),
`hail: chosen by Jev: n` (stint tick rows whose action starts `hail:`) and
`planner talk_to hails: n` (attempted minus Jev's). A failed converse action reports only the
world's reason and not which action asked for it, so the failures are matched
by wording (`HAIL_FAILURE_PATTERNS`); the two reasons `hail` shares with other
converse actions (`not next to <id>`, `already in a conversation`) are left
out, which makes the attempted count a lower bound. A successful hail is also a notable
moment.

## 10. "Good for" wording, hearer feedback and board unread tracking (2026-09-19)

### 10.1 Why

Live runs showed LLM settlers under-using conversations, boards and signs:
the prompt described only the physics of each channel, never what it was for,
and `say`/`shout` gave no feedback beyond `say ok: local`, so a settler had no
signal that a shout into an empty stretch of island had gone unheard.

### 10.2 Channel purposes ([tools, not rules] relaxed for this one case)

Section 1's design rule still holds for strategy and etiquette, but the owner
decided agent-facing text may now also say plainly what a channel is **good
for** and **not good for**, as long as that is a fact about the channel (one
line vs. back-and-forth, reaches a room vs. reaches whoever passes by) and
never a "you should". `SETTLEMENT_NARRATIVE`'s old "Voices and writing:"
section is now "Reaching the others, and what each way is good for:", with one
bullet per channel giving physics then purpose, plus the matching one-sentence
purpose in every communication tool's docstring (`shout`, `talk_to`,
`open_conversation`, `join_conversation`, `write_note`, `read_board`,
`place_sign`, `write_sign`, and the `shouts`/`hails` arguments of
`start_stint`/`set_reflex`). **Since 2026-09-19 there are four channels**:
`shout`, conversations, the message board and the sign. `say` was removed from
the agents' surface, as was the invitation mechanic (section 8); the
conversations bullet
states that the opening line is heard within `SAY_RADIUS` tiles and that
anyone who sees a conversation can join it, up to
`CONVERSATION_MAX_PARTICIPANTS`. `CONVERSER_NARRATIVE` gains one sentence that a
conversation is the settlers' one back-and-forth channel. `agents/CLAUDE.md`'s
"Tools, not rules" bullet records the decision.

### 10.3 `say`/`shout` hearer feedback

`tick._process_say_phase` (`world/src/world/tick.py`) now computes, for a
`local` or `shout` `SayIntent`, every living non-wolf entity other than the
speaker within that channel's earshot (`HEARING_RADIUS_BY_CHANNEL`, moved from
`services/observation_service.py` to `types.py` as the shared source of truth,
alongside the new `SAY_RADIUS`/`SHOUT_RADIUS` names) and puts them, sorted and
comma-separated, in the successful `EntityActed.details` as `"heard: <ids>"`
(empty after the colon when nobody was in range). `thought` is unaffected: its
detail stays the plain channel name, since it has no in-world hearers. This is
a pure `details`-string change; `action_type` stays `"say"` for every channel,
`ActionResult`/`EntityActed`/the recording format are untouched, and no test
or the viewer parsed the old `"local"`/`"shout"` value, so nothing else needed
to change for backward compatibility.

Agent side, the `shout` tool (`planner/tools/talking.py`; and, until
2026-09-19, `say`) no longer returns the world's
`"<description> -> say ok: heard: <ids>"` verbatim: `speak_result` rewrites it
to `"said to cleo, finn (within 10 tiles)"` / `"shouted to cleo, finn (within
60 tiles)"`, or `"nobody was within <radius> tiles to hear it"` when the id
list is empty. Anything that is not that exact successful shape (a failure, an
`interrupted: ...`, an asleep rejection) passes through unchanged.

### 10.4 Board unread tracking

`ObjectInfo.notes()` drops empty slots, so its list index was never the real
slot number; `notes_by_slot()` (`worldmodel/types.py`) parses the same `notes` JSON
into `{slot: note}` so read-tracking survives other slots filling or emptying.
`WorldModel` keeps two independent per-board `{slot: tick}` maps:

- `board_notes_read`, written only by the planner's `read_board` tool
  (`mark_board_read`), drives `unread_note_count(board_id)` and
  `is_note_unread(board_id, slot, note)`. `describe_world`'s `look` output
  marks every note whose current tick has not been read `(new)` and appends
  `", N new since you last read"` to the board's header line. A note is never
  marked read just by being seen in view; only reading it does that.
- `_board_notes_notified` (private) drives `_check_boards_in_view`, called
  from `update()` next to `_read_signs_in_view`: the first time a board with a
  note by another author comes into view, or a known note's tick changes, one
  line - `[board_1: new note by ada: 'title']` - is queued on
  `TickDigest.board_notes`, exactly the way `sign_notes` already worked. This
  is independent of the read map: it fires by being in view, once per
  `(board, slot, tick)`, whether or not the note has since been read.
  `JevAgent._handle_tick` (`agent/core.py`) drains `digest.board_notes` through
  `_note_for_planner`, the same path as a sign note, so it reaches the next
  tool result, the next turn prompt and the journal's `DayLog`.

### 10.5 Friendlier failures

- `read_board` on an id the actor has not seen now lists every board it knows
  with its position (`WorldModel.boards_known()`), or says none is known and
  that `place` can put one down, instead of a bare "have not seen" miss.
- `join_conversation` on an id the actor has not seen lists conversations in
  view with their anchors and mentions `talk_to` as the alternative that walks
  up to a settler directly.

## 11. Live-run fixes: purpose, the closing note, and asleep visibility (2026-09-19)

A live run (`runs/20260919-211823-settlement`) surfaced defects fixed the same
day.

### 11.1 Asleep settlers are visible, and cannot be hailed

`describe_world`'s "entities in view" line and `_roster_lines`
(`planner/describe.py`)
now print `asleep` for a living settler seen asleep, and `dead` (unchanged)
takes priority over it; the roster line reads `asleep as of <when>`. `talk_to`
refuses at once, without walking, when the target is in view and already
known to be asleep (`"<id> is asleep right now and cannot be hailed"`), and
says so plainly if it turns out asleep once the walk is done, instead of
spending the hail and reading the world's refusal. The docstring states a
sleeping settler cannot be hailed at all.

### 11.2 `write_sign`/`write_note` refuse the wrong object; `place_sign` crafts

Both tools now check, from what the actor has itself observed, whether the
named object is the kind they write to; a mismatch is refused with what the
object actually is and the right tool's name (`wrong_note_target`,
`actions.py`), before any intent is submitted. This is a planner-side
guardrail: `write_sign` always writes `WriteNoteIntent` slot 0, which is a
message board's own first note slot, so calling it on a board silently
overwrote that slot with a blank-titled note. The world side was already
correct and unchanged — `containers.process_write_note_phase` dispatches on
`board.object_type`, and a sign only ever accepts slot 0
(`world/CLAUDE.md`, "Signs").

`place_sign(direction, text)` is now self-sufficient: it checks the 80-character
limit first (before touching the pack), then crafts a sign from 2 wood if none
is carried but the wood is (reporting the craft step in the result), and
otherwise names the shortfall (`"a sign takes 2 wood; you carry N, and you
have no sign to place"`) without placing or writing anything. The recipe is
unchanged (2 wood, hand-crafted).

### 11.3 A bad tool-kwarg no longer risks the whole turn

pydantic-ai's own validation-error text names only the field that was wrong
(e.g. "Extra inputs are not permitted"), never what the tool actually takes,
so a model that guessed a parameter name (`sleep(bed_object_id=...)` for the
renamed `sleep(bed=...)`) got no way to self-correct and could burn every
retry. `BudgetedToolset.get_tools` (`planner/toolset.py`) wraps every tool's
`args_validator` in `_FriendlyArgsValidator`, which catches a
`pydantic.ValidationError` and re-raises it as a `ModelRetry` with a
`_tool_signature_line` appended (`"<tool> takes: a, b (optional)"`) — the one
hook point that reaches every planner tool's argument validation without
touching pydantic-ai itself, since validation runs in `ToolManager` before a
toolset's `call_tool` is ever invoked. The planner agent's `retries` rose from
2 to 3 (`PLANNER_TOOL_RETRIES`). `sleep`'s parameter is renamed
`bed_object_id` -> `bed` (default `""`, the ground).

Investigated and left as-is: once a tool's retries really are exhausted,
pydantic-ai raises `UnexpectedModelBehavior` from inside `ToolManager`
validation, before `BudgetedToolset.call_tool` (the only place this codebase
can intercept a tool result) ever runs — there is no clean hook to turn that
specific exception into a tool result instead of failing the call. The
existing top-level `Planner.run` loop already catches any turn failure,
traces `turn_failed`, and retries after `TURN_RETRY_SECONDS`, so the agent
does not crash; a turn is lost, but with the friendlier retry message and the
raised retry count this should now be rare.

### 11.4 Conversations carry a purpose

`talk_to` and `open_conversation` both gain a required `purpose` argument:
"what you want out of this conversation; only you see it; it is handed to you
while you are in the conversation." A hail Jev makes from the brief can carry
one too: `BriefHail` (`briefs.py`) gains an optional `purpose`, so
`start_stint(..., hails=[{"settler": "dov", "line": "...", "purpose": "..."}])`
flows it the same way. A settler that was hailed, or that only joined, has no
purpose — only the settler that opened or hailed sees one.

Mechanically: `AgentBridge.set_conversation_purpose` (implemented on
`JevAgent`) stashes the value the planner tool passed just before the `open`
or `hail` intent; `JevAgent._detect_join` reads it back (via
`_conversation_purpose`) when that actor's own seat lands, matching a
Jev-driven `hail:<settler>` option to its `BriefHail` through
`hailed_target(digest)` when the seat came from an active stint rather than a
direct planner tool call. `ConversationSession.purpose` renders as "You
started this conversation because: …" in `build_prompt`, for that settler's
own turns only, and `conversation_start` traces it.

### 11.5 The closing note asks for two things

`ModelConverser`'s closing call now returns a structured `ClosingNote`
(`agreed_or_learned`, `you_said_you_would`), replacing the old single free
line; `NoteCall` carries both as `agreed`/`commitment`. Both are appended to
the journal's `Today's notes` as their own
`[conversation, tick N, with a, b] agreed: …` / `... I said I would: …` lines
when non-empty (`append_conversation_note`), and `ConversationReport.to_text()`
leads with `you said you would: …` / `agreed or learned: …` before the
transcript, so the planner sees the takeaway without reading the whole
exchange. The note prompt is now traced too, in the `conversation_end` line
(`note_prompt`), which it was not before.

Also checked against `world/src/world/conversations.py` and `items.py` and
added to `CONVERSER_NARRATIVE`, as physics rather than strategy: leaving is
final for that settler (the conversation goes on without it, or closes if
fewer than two remain); after a conversation ends its members cannot be
hailed for `HAIL_COOLDOWN_TICKS` (60) ticks; a full round of passes closes it.

### 11.6 `start_stint` names what Jev can and cannot say

The `instruction` docstring now states plainly: "Jev cannot speak on its own:
it can only shout the `shouts` and hail the `hails` you give it here" — three
briefs in the diagnosed run told Jev to "talk to finn about…" with no hails
granted, which Jev has no way to act on.

### 11.7 Analysis

`tools/analyze_run.py`'s conversations section gains a per-conversation line
count distribution (`line_count_stats`: min/median/max over each
conversation's utterance count) and `purpose given: N of M` (`purpose_total`
counts every seat taken that wrote a `conversation_start` trace row across all
agents, i.e. per seat, not per conversation; `purpose_given` is how many of
those carried a non-empty `purpose`). Both read as zero on a run recorded
before this shipped.
