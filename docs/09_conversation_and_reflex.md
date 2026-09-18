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

## 8. Invitations: "open to talk" (added 2026-09-18)

### 8.1 Why

In the 2026-09-17 settlement runs settlers coordinated over `shout` and almost
never opened a conversation (1 in 800 ticks), while their reflections talked
about wanting to coordinate. `open_conversation` is a blocking commitment of
unknown length that needs a free anchor tile and a listener within ten tiles,
and briefs that said "ask so-and-so for stone" gave Jev nothing it could do.
An invitation is a flag on ordinary speech: it costs the speaker nothing, it
does not block the planner, and it gives Jev (the speaker's own and the
hearer's) a concrete option: walking up to the speaker starts a conversation.

### 8.2 World

**Saying it.** `SayIntent` gains `open_to_talk` (bool, proto field 3). It is
honoured on the `local` and `shout` channels and ignored on `thought`. A
successful say with the flag sets the speaker's invitation:
`Entity.open_until_tick = tick + INVITATION_TICKS`. The `Utterance` event
carries `open_to_talk` (proto field 6) so hearers learn it with the text and
the position. `Entity` carries `open_to_talk` (bool, proto field 16) computed
at observation time (`open_until_tick > tick`), so anyone who can see the
speaker sees the invitation too, and the viewer payload and the recorded tick
carry the same boolean under `open_to_talk`.

The invitation lives on the entity in three fields that are not in the proto:
`open_until_tick` (the first tick on which the invitation is gone, so it is
live while `tick < open_until_tick`; -1 means none), `invitation_text` and
`invitation_tick` (the line that opened it and the tick it was said, which an
`accept` needs for the first transcript entry). `Entity.is_open_to_talk(tick)`
is the predicate everything else reads.

An invitation ends when it expires, when the inviter takes any seat in a
conversation (open, join, accept, or being accepted), or when the inviter
dies. Saying again with the flag renews it; saying without the flag leaves it.

**Accepting it.** `ConverseIntent` gains the action `accept` and the field
`target_entity_id` (proto field 5). Conditions, each a failure reason in
`EntityActed.details` when it fails:

- the target is alive, is a player, and has a live invitation
  (`no invitation from <id>`);
- the accepter is adjacent to the target (Chebyshev 1) (`not next to <id>`);
- neither is in a conversation (`already in a conversation`, `<id> is already
  in a conversation`);
- there is an **anchor** tile adjacent (Chebyshev 1) to both, walkable, with
  no blocking object, no entity and no conversation (`no free tile next to
  both of you`). Candidates are tried in ascending (x, y) order so the choice
  is deterministic.

On success, in the conversation phase of that tick, the world creates the
conversation on the anchor exactly as `open` would, with `participants =
[target, accepter]`, `opened_by = target`, transcript entry 0 = the target's
invitation line (`{"tick": <tick it was said>, "speaker": target, "text":
<the invitation text>}`), and the turn going to the target (the opener gets
the first turn, section 2.4). Both entities get an `EntityActed` for action
type `converse`: the accepter's details are `accept conv_N <target>` and the
target's are `join conv_N`, so the agent-side join detection sees both. No
utterance is emitted (the invitation was already heard). The target's
invitation is cleared.

Two accepters of the same target in one tick: the lexicographically smaller
entity id wins; the other fails with `conv_N already started` and may `join`
on a later tick like anyone else.

Constants (`world/src/world/items.py`, mirrored in `agents/.../items.py`):

| name | value | meaning |
|---|---|---|
| `INVITATION_TICKS` | 40 | how long an invitation stays open |
| `CONVERSE_ACCEPT` / `ACTION_ACCEPT` | `"accept"` | the converse action; the world keeps it in `types.py` beside the other `CONVERSE_*` actions, the agents in `conversation.py` beside `ACTION_OPEN` |

### 8.3 Agent side

**World model.** `HeardUtterance.open_to_talk` and `EntityInfo.open_to_talk`.
`WorldModel.open_invitations()` returns, for every other living player either
visible with the flag or heard with the flag within `INVITATION_TICKS`, its id,
its best-known position (visible position if in view, else where it was heard)
and the invitation text (the most recent flagged line heard, or `""`).
`WorldModel.my_invitation_live()` is true while this actor's own flagged say is
younger than `INVITATION_TICKS`.

**Jev options** (`options.py`), offered only while the actor is in no
conversation:

- `talk_to:<entity_id>`, one per open invitation. Adjacent to the inviter it is
  the `accept` intent, described as `accept <name>'s invitation to talk: a
  conversation with the two of you starts on a free tile next to you both`.
  Otherwise it is a code-owned walk to the inviter's best-known position,
  `stop_adjacent`, described as `walk to <name> at dx X dy Y, who said
  "<text>" and is open to talk; next to them you can accept and a conversation
  starts` (a settler with no heard line reads `..., who is open to talk; ...`).
  It is grouped with the conversation options (after survival, before travel
  control).
- `invite:<n>`, one per phrase in `Brief.invitations`, offered when at least
  one other living player is within `SAY_RADIUS` and the actor's own
  invitation is not live. The intent is a `local` say with `open_to_talk`.
  Described as `say "<phrase>" and stay open to talk for
  {INVITATION_TICKS} ticks: anyone who hears it can walk up and start a
  conversation with you`.

**Jev state** (`jevstate.py`): an `invitations` list of lines beside the
`entities` block (the state is JSON, so there is no settlers block to hang them
under), one per open invitation, `<name> at dx X dy Y is open to talk:
"<text>"`, plus `you are open to talk (N ticks left)` while the actor's own
invitation is live. The key is left out entirely when there is neither.

**Brief.** `Brief.invitations: tuple[str, ...]`, in `as_payload()` as
`invitations` and in the stint trace. The limits (at most `MAX_BRIEF_SHOUTS`
phrases, each at most `CONVERSATION_TEXT_LIMIT` characters, blanks dropped) are
enforced where the planner writes them, in `_validated_invitations`, exactly as
`_validated_shouts` does for shouts; a breach is a `ModelRetry`. A successful `accept`, or being accepted,
ends a running stint with `joined_conversation` exactly as a `join` does.

**Planner tools.**

- `say(text, open_to_talk: bool = False)`. The docstring states what the flag
  does (the physics above) and nothing about when to use it.
- `start_stint(..., invitations: Sequence[str] = ())`: the lines Jev may say
  with the flag, like `shouts` for shouting.
- `talk_to(entity_id, max_ticks=40)`: code walks next to the inviter (like
  `join_conversation`), submits `accept`, and blocks until the conversation is
  over; returns the conversation report. Refuses at once when no invitation
  from that settler is known.
- `look` marks settlers who are open to talk: `open to talk: "<text>"` on the
  entity line and in the met list.

**Non-blocking conversations.** A conversation can now begin while the planner
is thinking (someone accepted the planner's own `say(open_to_talk=True)`), or
while a single-tick tool or `wait` is in flight. Then:

- conversation mode begins as for a join (section 4.3); the planner turn is
  not interrupted;
- the in-flight and every queued single-tick action resolve with
  `<description> -> interrupted: conversation conv_N started` (same mechanism
  as the reflex interruption, new constant `INTERRUPTED_BY_CONVERSATION`).
  This happens only for a seat the actor did not ask for: when its own
  `open`, `join` or `accept` made the seat, the tool result stays the world's
  own outcome, which is what `_sit_through` reads;
  single-tick actions requested while the conversation runs get the same
  answer at once; a queued `start_stint`, `travel_to` or `build` waits until
  the conversation has ended and then runs;
- the conversation report is delivered to the planner the way reflex reports
  are: appended to the next tool result and to the next turn prompt
  (`drain_reflex_notes` becomes `drain_notes`, carrying both kinds).

During a stint, an accept by Jev or an acceptance of Jev's invitation ends the
stint with `joined_conversation` and `start_stint` returns after the
conversation with the report appended (unchanged).

**Implementation notes** (where the code settled differently from the lines
above):

- `ACTION_ACCEPT` lives in `agents/.../items.py` with the other mirrored world
  constants, because `options.py` needs it and cannot import `conversation.py`
  (that import runs the other way). `conversation.py` still names it beside
  `ACTION_OPEN` and `ACTION_JOIN`, as `ACTION_ACCEPT = items.ACTION_ACCEPT`.
- `open_invitations()` drops a settler that is in view on this very tick
  *without* the flag even when a flagged line from it was heard inside the
  window: the world computes `Entity.open_to_talk` at observation time, so it
  is the newer fact, and an accept offered against it could only fail.
- `joined_conversation_id` keeps its name and is joined by
  `joined_conversation(digest) -> (conversation_id, action)`, which carries the
  detail's first word. `JevAgent` turns that into the trace's `via`: the word
  itself for `open`, `join` and `accept`, and `accepted` for a `join` the actor
  did not submit a `ConverseIntent` for on the previous tick (it records the
  action and tick of every converse intent it sends).
- `_detect_join` runs before the in-flight single-tick action is resolved, for
  the same reason the reflex check does: otherwise the conversation's own
  `EntityActed` would be handed to an unrelated tool call as its outcome.
- `talk_to` and `join_conversation` share one code-owned walk,
  `_walk_next_to(target)`, which is the old `_walk_to_anchor` generalised: it
  walks onto a free tile next to a position, the anchor for one and the
  inviter for the other.

**Prompt.** The narrative's "Conversations" section gains the invitation
physics (what the flag does, how long it lasts, what accepting does, that a
conversation may start while the planner is mid-turn and what that does to
in-flight tools). The brief examples gain `invitations` in the same way they
show `shouts`. No sentence says when to invite or whom to talk to.

### 8.4 Viewer, analysis, traces

- Viewer: an entity with `open_to_talk` shows a small speech-bubble marker
  above its sprite, live and in replay.
- `tools/analyze_run.py` conversations section adds `opened by invitation: n`
  and `invitations said: n`, and a notable moment `invitation_accepted`.
- Trace: `stint_start` briefs carry `invitations`; the conversation trace's
  `conversation_start` carries `"via": "open" | "join" | "accept" |
  "accepted"`.
