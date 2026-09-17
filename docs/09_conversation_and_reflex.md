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

### 2.2 Constants (`world/src/world/items.py`, mirrored in `agents/.../items.py`)

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

One intent per tick as always. `EntityActed.details` for a success starts with
the action name (`open conv_12`, `join conv_12`, `speak`, `pass`, `leave`); a
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
  why it ended for this actor (`closed`, `left`, `removed`, `died`, `nobody
  joined`), the transcript, items given and received, and the note written.
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

`tools/analyze_run.py` reports conversations (opened, joined, utterances, how
they ended, notes written), gives (count, kinds, pairs), and reflexes (settlers
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
