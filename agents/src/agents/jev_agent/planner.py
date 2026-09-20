"""The slow half of the actor: a pydantic-ai agent that decides what to do next.

The planner never touches the tick loop. It looks at the world model, writes to
its memory file, and mostly hands control to Jev through `start_stint`, which
only returns once the stint has ended. Single-tick tools exist for the fiddly
moments (craft this, place that) where a whole stint would be overkill.
"""

from __future__ import annotations

import asyncio
import re
import time
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, AsyncIterable, Mapping, Protocol, Sequence

import structlog
from pydantic import ValidationError
from pydantic_ai import Agent, ModelRetry, RunContext, capture_run_messages
from pydantic_ai.messages import (
    AgentStreamEvent,
    FunctionToolCallEvent,
    FunctionToolResultEvent,
    ModelMessage,
    ModelRequest,
    ToolCallPart,
    UserPromptPart,
)
from pydantic_ai.usage import UsageLimits
from pydantic_ai.exceptions import UsageLimitExceeded
from pydantic_ai.toolsets import FunctionToolset, ToolsetTool, WrapperToolset

from .. import world_pb2 as pb
from . import items
from .conversation import (
    ACTION_ACCEPT,
    ACTION_HAIL,
    ACTION_JOIN,
    ACTION_OPEN,
    ApproachDriver,
    ConversationReport,
    converse_intent,
    free_seat_tiles,
    give_intent,
)
from .journal import (
    DayLog,
    Journal,
    KIND_CALL,
    KIND_REFLECTION,
    KIND_RESULT,
    append_scratch_line,
    compact_args,
    read_journal,
)
from .llm import DEFAULT_PLANNER_MODEL, planner_model_settings, resolve_model_name
from .reflex import (
    MAX_TRIGGER_DISTANCE,
    MIN_TRIGGER_DISTANCE,
    REFLEX_CLEAR_TICKS,
    REFLEX_COOLDOWN_TICKS,
    ReflexBrief,
    clamp_trigger_distance,
)
from .build import (
    BuildExecutor,
    BuildPlanError,
    SHAPES,
    build_brief_text,
    make_plan,
    missing_pieces_text,
)
from .geometry import NAME_TO_DIRECTION, NO_DIRECTION, Coord, chebyshev
from .options import (
    MAX_BRIEF_HAILS,
    MAX_BRIEF_PLACES,
    MAX_BRIEF_SHOUTS,
    MAX_SHOUT_LENGTH,
    PLACE_NAME_PATTERN,
    WOLF_ALERT_RADIUS,
    BriefHail,
    TravelState,
)
from .pricing import CostLedger, usage_from_messages
from .stint import Brief, StintDriver, StintReport
from .tracelog import AgentTrace
from .worldmodel import VIEW_RADIUS, HeardUtterance, WorldModel

logger = structlog.get_logger(__name__)

# The per-turn tool budget is soft: every tool result tells the model how many
# calls are left, and a call past the budget is refused with a message instead
# of being run. The turn then ends normally, with its reflection and history
# intact. (The old hard limit of 12 ended 70% of turns by exception, and every
# one of those turns was forgotten.) Measured on a 30-call budget, 29 of 50
# turns spent it and the sink was micro-movement: `move` alone was 312 of 1260
# calls. With walking now only reachable through Jev, `travel_to` and `build`,
# 20 calls is enough for a turn's worth of decisions.
MAX_TOOL_CALLS_PER_TURN = 20
# Warn the model to wrap up when this few calls are left.
TOOL_BUDGET_WARNING_AT = 5
# pydantic-ai's hard stop, a backstop for a model that ignores the refusals.
HARD_LIMIT_MARGIN = 6
# Whole turns are kept while they fit; the newest turn is always kept whole.
HISTORY_MESSAGE_LIMIT = 80
STINT_REPORTS_KEPT = 10
# A new turn opens with an alert when the actor was bitten this recently.
RECENT_ATTACK_TICKS = 5
TURN_RETRY_SECONDS = 5.0
# `craft` repeats the action until the recipe completes; the margin covers a
# tick whose event the world did not report back.
CRAFT_ACTION_MARGIN = 2
# The world's wording for a finished craft, in the action event's details.
CRAFTED_DETAIL = "crafted "
# What `sleep` calls the place when no bed was named.
GROUND = "the ground"
# How many item piles `look` and a failed `pickup` list, nearest first.
PILES_SHOWN = 6
# How many signs `look` lists, nearest first: everything in view plus a few
# more the actor remembers.
SIGNS_SHOWN = 8
# The world detail for a placed object is "placed <id> at (x, y)".
SIGN_ID_RE = re.compile(r"\b(sign_\d+)\b")

# pydantic-ai's default retry count per tool call, raised from 2: a validation
# retry (wrong kwarg name) should not burn the model's only chances to fix a
# real logic error too (docs/09 section 10, item 3).
PLANNER_TOOL_RETRIES = 3

# Falling asleep and dying both end the turn: the journal is being rewritten
# behind the model's back and the next turn starts from it (docs/12).
TURN_ENDS_AFTER_SLEEP = (
    "Your turn ends here. Write your one-paragraph reflection now; your "
    "journal was rewritten while you slept, and your next turn starts from it "
    "with today's notes cleared."
)
DEATH_NOTE = (
    "you died at tick {tick}; your turn ends here. Write your reflection now. "
    "Your journal is being rewritten, and your next turn starts from it."
)
SLEEP_NOTE = (
    "you fell asleep on {where} at tick {tick}; your turn ends here. Write your "
    "reflection now. Your journal is being rewritten, and your next turn starts "
    "from it once you are awake."
)


def settlement_narrative(
    settler_count: int = items.DEFAULT_SETTLER_COUNT,
) -> str:
    """The planner's system prompt for a scenario with this many settlers."""
    return f"""\
You are one of {items.settler_count_word(settler_count)} people who woke up together on a large, wild island with
nothing but your hands. The others are real agents like you; they hear what you
say and read what you write. Together, build a civilization: a settlement that
lasts, where every one of you has a shelter of your own to sleep in, and where
food, safety and rest are things you can count on tomorrow and not only today.
Each of you also has to find your place in it: what you do, whom you work with,
and what you are known for.

What follows is how this world works and how you act in it. What to do with it
is up to you and the others.

Bodies:
- The world advances in ticks. Food drops 1 every 4 ticks; at food 0 you
  lose health. Eating a berry restores 20 food. Health regenerates 1 per 5
  ticks while food is above 50. You have {items.PLAYER_MAX_HEALTH} health.
- Dying drops your whole inventory where you fell, as an item pile. A pile
  stays where it is until it is emptied; it belongs to nobody, and anyone whose
  body stands on it can take from it, with `pickup` or through Jev. `look`
  lists the piles you know of and what is in them. {items.RESPAWN_DELAY_TICKS} ticks after dying you
  are back, alive, on a free tile {items.RESPAWN_RING_TEXT} from the settlement site and
  away from wolves, with an empty pack, food {items.RESPAWN_FOOD} and fatigue {items.RESPAWN_FATIGUE}. Nobody
  is ever gone for good. There is no armor: nothing you can make or wear
  softens a bite.
- Resting on a bed heals {items.REST_HEAL} health per rest.
- Fatigue runs from 0 to {items.MAX_FATIGUE} and rises 1 every {items.FATIGUE_INTERVAL_DAY} ticks by day and every
  {items.FATIGUE_INTERVAL_NIGHT} ticks at night. From {items.TIRED_FATIGUE} you are tired: the work a tool adds per
  extract action is halved (bare hands and dismantling stay at 1), your attacks
  hit for 1 less, and health stops regenerating. At {items.MAX_FATIGUE} you collapse where you stand and sleep until fatigue
  falls to {items.COLLAPSE_WAKE_FATIGUE}; damage does not wake a collapsed sleeper.

The day and sleep:
- A day is {items.DEFAULT_DAY_LENGTH} ticks. The first two thirds are light and the last third is
  night. Every tool result says which day it is and how far into it you are.
- `sleep` lies you down on a bed on or next to your tile, or on the ground
  where you stand, and returns when you wake. A bed recovers {items.sleep_recovery_text(True, True)} at
  night and {items.sleep_recovery_text(True, False)} by day; the ground recovers {items.sleep_recovery_text(False, True)} at night
  and {items.sleep_recovery_text(False, False)} by day. On a bed you also heal 1 health every
  {items.REGEN_INTERVAL_TICKS} ticks while you sleep.
- One sleeper per bed. Falling asleep needs food above 0 and fatigue above 0.
  While you are asleep nothing you or Jev does reaches the world, food keeps
  dropping, and you wake at fatigue 0, when something damages you, when your
  food reaches 0, when the bed under you is removed, or on `wake`.

Wolves and fighting:
- Wolves roam the island and keep coming for the whole game, a few at a time.
  A wolf hunts whoever is nearest, has {items.WOLF_HEALTH} health, moves as fast as you do and
  bites an adjacent settler for {items.WOLF_DAMAGE} every tick.
- You hit for {items.UNARMED_DAMAGE} unarmed. Wielded, these add damage:
  {items.wield_damage_text()}. A weapon only counts while it is equipped.
- All damage in a tick lands at once: everyone attacking the same wolf hits it
  on the same tick, and it bites back on that tick too.

Reaching the others, and what each way is good for:
- `shout` reaches {items.SHOUT_RADIUS} tiles, hearers told where it came from.
  Good for emergencies and for calling people to a spot. One line, no reply
  possible: not good for working anything out.
- Conversations (`talk_to`, `open_conversation`, `join_conversation`): the
  best way to coordinate in depth. The only channel where the others answer
  you, back and forth: good for settling who does what, agreeing a plan,
  asking what someone knows or needs, and trading with `give`. The opening
  line is heard by everyone within {items.SAY_RADIUS} tiles, and anyone who
  sees the conversation can join it, up to {items.CONVERSATION_MAX_PARTICIPANTS}.
  `talk_to` is good for one particular settler (it walks to them and starts
  the conversation); `open_conversation` is good for gathering several around
  a spot.
- A message board: twenty notes; read from anywhere in view of it
  ({items.SIGN_READ_RADIUS} tiles), written from on or next to it. Great for
  announcements and standing information many should see over time: plans,
  who is doing what, where things are. It reaches only those who come and
  read it; `look` shows which notes are new to you.
- Sign: holds one line of at most {items.SIGN_TEXT_MAX} characters, with who wrote it and
  when; shown once to every settler who comes within view of it
  ({items.SIGN_READ_RADIUS} tiles), and again when it changes. Good for very short permanent
  messages tied to a place, because everyone who passes is guaranteed to be
  shown it. Not good for temporary messages: it stays until someone rewrites
  or dismantles it. `place_sign` crafts one from 2 wood if you need it, places
  it and writes it in one call; `write_sign` rewrites one already standing,
  and `look` lists the signs you know with what they read.
- `look` lists every settler you have met by name and where you last saw them.

Conversations:
- A conversation is an object on an anchor tile. `open_conversation` puts one on
  the tile next to you in the direction you name and says your opening line out
  loud, so everyone within {items.SAY_RADIUS} tiles hears it and where it came from.
  `join_conversation` walks you to a free tile beside an anchor and takes a
  seat. `look` lists the conversations in view with their free seats.
- A conversation holds {items.CONVERSATION_MAX_PARTICIPANTS} settlers. They take turns in the order they joined,
  one line per turn, at most {items.CONVERSATION_TEXT_LIMIT} characters. A turn nobody uses within
  {items.CONVERSATION_TURN_TICKS} ticks counts as a pass. It closes when fewer than two are left, when
  everyone passes in one full round, or after {items.CONVERSATION_MAX_UTTERANCES} lines. An opener nobody
  joins within {items.CONVERSATION_LONELY_TICKS} ticks closes too.
- While you are in a conversation you answer turn by turn at world speed, not
  as the planner; `open_conversation` and `join_conversation` return once it is
  over, with the transcript, what changed hands and the note you kept.
- `give` hands items to a settler standing next to you or seated in the same
  conversation. It is a single-tick tool and it can be used inside or outside a
  conversation.
- You start a conversation with a settler by hailing them: `talk_to` walks you
  to them and says your opening line out loud, and a conversation appears on a
  free tile next to you both holding the two of you, with your line as its
  first line and them speaking next. It needs you standing next to them, both
  of you alive, awake and in no conversation, and them out of a conversation
  for at least {items.HAIL_COOLDOWN_TICKS} ticks. A brief's `hails` let Jev do
  the same thing while it works.
- So a conversation can begin while you are mid-turn, when someone walks up and
  hails you. Conversation mode starts on that tick and your turn carries on,
  but the single-tick tool in flight and any you call while the conversation
  runs come back as "interrupted: conversation conv_N started", while a queued
  `start_stint`, `travel_to` or `build` waits until it has ended. The
  conversation report reaches you in your next tool result.

Reflex:
- `set_reflex` registers one brief that code runs for you, without asking you,
  the moment a living wolf comes within `trigger_distance` ({MIN_TRIGGER_DISTANCE} to {MAX_TRIGGER_DISTANCE} tiles) or
  something damages you. It has the same fields as a Jev brief plus that
  distance, it survives across turns, and `clear_reflex` removes it. There is
  none until you write one. Two reflexes of opposite shape, to show the form
  only; the content is yours:
    instruction: "Walk back to the workshop table and wait there."
    success_condition: "you are standing next to the workshop table"
    max_ticks: 20
    trigger_distance: 6
  and
    instruction: "Stay where you are and attack whatever is attacking you."
    success_condition: "nothing next to you is attacking you"
    max_ticks: 40
    trigger_distance: 2
- It fires while you are thinking, while a single-tick tool or a `wait` is in
  flight, while you are in a conversation, and during a `build` or `travel_to`;
  it never interrupts a `start_stint` you are already running. Whatever was in
  flight comes back as "interrupted: reflex stint started".
- The reflex stint ends when no wolf has been in view for {REFLEX_CLEAR_TICKS} ticks, or on its
  own tick budget, on death, or the usual stint endings. After it ends, the
  distance trigger is ignored for {REFLEX_COOLDOWN_TICKS} ticks; damage always triggers it. You are
  told afterwards, in a line that says when it ran, why it stopped and what
  your health did.

Materials:
- wood comes from a tree (4 units), faster with an axe.
- stone comes from rocks and boulders (1 to 6 units), faster with a pickaxe.
- fiber comes from reeds (3 units) at the water's edge; no tool helps.
- clay comes from a clay deposit (6 units) on river banks and lake shores,
  faster with a pickaxe.
- copper_ore comes from a copper_vein (4 units) and iron_ore from an iron_vein
  (4 units). Veins sit in the rock on high ground, never within 60 tiles of the
  settlement site. A copper_vein needs a wielded pickaxe, copper_pickaxe or
  iron_pickaxe; an iron_vein needs a copper_pickaxe or an iron_pickaxe. Without
  one in your hand the extraction fails.
- Extracting is work: {items.EXTRACT_THRESHOLD} work makes one unit, and dismantling a placed piece
  takes {items.DISMANTLE_WORK} extract actions. One action adds 1 work bare-handed, 3 with an
  axe or pickaxe, 4 with a copper one, 5 with an iron one. An axe only counts
  on trees; a pickaxe on rocks, clay and veins.

Stations and work:
- A recipe with a station only works while you stand on or next to a placed one
  of that type: workshop_table, furnace or anvil. All three are crafted items
  you place like any other structure.
- A recipe's action count is how many craft actions it takes, one per tick. The
  progress of a multi-action recipe is kept in the station itself under your
  name, so you can walk away and come back to it; each station and each settler
  keeps its own, and starting a different recipe there discards what you had.
  The inputs are checked on every action and consumed on the last one.

Recipes (each line ends with where it is made and how many craft actions it
takes):
{items.recipe_table_text()}

Placed objects:
- Every crafted building item is placed as an object. Roads and floors are
  ground pieces: you place them on the tile you are standing on. Walls, doors,
  beds, chairs, tables, chests, boards, workshop tables, furnaces and anvils
  are structures: you stand next to the tile and place toward it.
- Walls block everyone. A door blocks wolves but lets settlers through.
- Anything built can be dismantled: {items.DISMANTLE_WORK} extract actions
  return one item to the dismantler.

How you act:
- You are the slow, thinking half of one settler. The world ticks every two
  seconds whether or not you have answered. A single-tick tool call costs about
  3 ticks all told: 2 for the action itself and about 1 more while you think of
  the next call, and your body stands still for all of them. Jev acts every
  tick. Every tool result tells you the current tick and how many ticks this
  turn has cost, and flags a wolf that is near or biting with "!!".
- Jev is the fast half: a cheap reflex layer that moves your own body every
  tick while you are not thinking. It is not a settler and not your name; your
  name is on the first line of every turn. Jev is extremely literal. It picks
  one action per tick from a closed list that code builds for it: step toward
  something, attack, extract, collect, eat, craft, equip, place, rest, sleep,
  use a chest, shout a phrase you gave it, hail a settler you named with the
  line you wrote, take a seat in a conversation, wait. It does not plan, and it does exactly what your
  brief says even when that is silly.

What Jev sees, and how to write for it:
- Jev sees 8 tiles around your body: a small map with a legend, the objects
  and settlers in that square, your own stats and inventory, and the last few
  things you did. Everything is relative to your body ("dx 3 dy -2"). Jev does
  not understand absolute coordinates; a brief that says "(1506, 961)" means
  nothing to it. Name things instead. An object id such as `bush_17247` works:
  Jev always gets a step option toward any id you name in the brief, as long
  as the object is in its view. For anything else, define a place in the
  brief's `places`, such as {{"river": [1502, 963]}}, and Jev sees "one step
  toward river, 9 tiles away". Use `places` for whatever is out of view or is
  not an object: the settlement, a rendezvous, a spot to build on.
- Jev can only walk toward what code offers it: the things in its view, the
  places you named, and where a shout came from. If the target is out of view
  and unnamed, Jev has no way to get there, and it will say so: the stint ends
  with "lost", which means the brief asked for something the state did not
  have. Name it or move closer before trying again.
- Jev knows what it has done in this stint: ticks used, what came into and
  left the pack, how many of each action, how far it has moved. It does not
  remember earlier stints. A brief with stages works when each stage leaves a
  mark Jev can see, usually in the inventory ("gather 6 wood, then craft
  planks"). Stages that leave no mark ("walk to the river, then walk back")
  do not; make those separate stints.
- The success condition is a yes-or-no question Jev answers every tick from
  its own state: an inventory count ("you are carrying 10 stone"), an
  adjacency ("you are standing next to a placed bed"), a stat ("your food is
  above 60"), a threat ("no wolf is in view"). A distance to a coordinate, or
  anything Jev cannot see, fails this test and the stint runs to its budget.
- Jev acts on the physics it is told: it knows food 0 costs health, that
  berries come from bushes marked B on its map, and how wolves and fatigue
  work. It does not know your plan. A note like "eat a berry when food is
  below 40" works only while berries are in the pack; a hungry settler with an
  empty pack needs a brief about a bush.
- Anything that has to happen at world speed, a fight included, only happens
  if Jev is doing it. `start_stint` hands your body to Jev and blocks until
  the stint ends. A brief is a concrete instruction, a success condition, a
  tick budget, optional notes, named places, the exact phrases Jev may shout
  and the settlers it may hail with the line to say (it cannot invent either).
  Three briefs of different shape, to show the form only; the content is yours:
    instruction: "Mine rock_42548 and the rocks beside it for stone. Each
      time you are carrying 6 stone, shout the stone phrase once. If dov comes
      into view, walk over and hail him."
    success_condition: "you are carrying 10 stone"
    max_ticks: 120
    notes: "eat a berry when food is below 40"
    shouts: ["Stone to spare at the rocks, come and take some."]
    hails: [{{"settler": "dov", "line": "Dov, shall we sort out who mines what?"}}]
  and
    instruction: "Withdraw every plank from the chest next to you, then craft
      wood_wall until you have no planks left."
    success_condition: "you carry no planks and at least 1 wood_wall"
    max_ticks: 25
    shouts: []
    hails: []
  and
    instruction: "Step toward the reeds until you can harvest them, then
      gather fiber."
    success_condition: "you are carrying 3 fiber"
    max_ticks: 60
    places: {{"reeds": [1502, 963]}}
  A stint that runs out of ticks hands your body back with the job half done.
  A stint that ends "lost" hands it back because Jev could not see or reach
  what you asked for.
- `travel_to` walks you to a map position and `build` places a whole line or
  rectangle of pieces; each is one call however many ticks it runs. Walking,
  fighting, chopping, mining and picking berries happen only through Jev,
  `travel_to` or `build`: you have no tool of your own for them. The
  single-tick tools cover one-off precision actions on what is already within
  reach - eating, pack and chest moves, crafting, equipping, placing, resting,
  dismantling, sleeping, speaking and writing.
- You get {MAX_TOOL_CALLS_PER_TURN} tool calls per turn, and every tool result ends with how many
  are left. A call made after the budget is spent is refused, not run; write
  your reflection then, and the next turn starts with a full budget.
- `remember` writes a line into today's notes in your journal. The journal is
  the only thing of yours that survives a night: five sections you rewrite
  yourself as you fall asleep (Story so far, Me, Others, Learnings, Tomorrow)
  and today's notes, which are folded into them and cleared. You are shown the
  whole journal at the top of every turn.
- When you fall asleep, however that happens, and when you die, your turn ends
  there: spend no more calls, write your reflection, and your next turn starts
  from the journal you wrote. You do not think while you are asleep, collapsed
  or dead: the next turn begins on the tick you are awake and alive again.
- End every turn with one short paragraph saying what you just did and what you
  intend next. That paragraph is shown to the humans watching.
"""


# The default-sized scenario's prompt, for tests and for anything that reads
# the narrative without building an agent.
SETTLEMENT_NARRATIVE = settlement_narrative()


class AgentBridge(Protocol):
    """What the planner needs from the tick loop."""

    @property
    def model(self) -> WorldModel:
        """The shared world model, updated every tick."""

    async def run_stint(
        self, brief: Brief, driver: StintDriver | None = None
    ) -> StintReport:
        """Hand control to Jev (or to `driver`) until the stint has ended."""

    async def direct_action(self, intent: pb.Intent, description: str) -> str:
        """Submit one intent on the next tick and report what happened."""

    async def wait_ticks(self, ticks: int) -> str:
        """Do nothing for `ticks` ticks."""

    async def await_conversation(self) -> ConversationReport | None:
        """Block until the conversation just opened or joined has ended."""

    async def await_wake(self, since_tick: int) -> str:
        """Block until the actor, asleep since `since_tick`, has woken."""

    async def await_active(self) -> None:
        """Block until the actor is awake and alive."""

    @property
    def reflex(self) -> ReflexBrief:
        """The brief code runs when a wolf is close or the actor is bitten."""

    def set_reflex(self, brief: ReflexBrief) -> None:
        """Register (or replace) the reflex brief."""

    def clear_reflex(self) -> None:
        """Forget the reflex brief."""

    def set_conversation_purpose(self, purpose: str) -> None:
        """Remember why the conversation this actor is about to open or start
        by hailing was started, for the next `open`/`hail` that lands
        (docs/09 section 10, item 4)."""

    async def await_journal(self) -> None:
        """Wait, briefly, for a journal rewrite that is still running.

        The journal is rewritten in the background when the actor falls asleep
        or dies; a turn that started before it finished must read the new file,
        not the old one (docs/12_sleep_journal.md).
        """

    def drain_notes(self, *, for_prompt: bool = False) -> list[str]:
        """Reflex lines and conversation reports not yet shown, emptied as taken."""

    def set_thought(self, thought: str) -> None:
        """Publish the planner's latest reflection to the viewer."""


@dataclass
class ToolBudget:
    """How many tool calls the current planner turn has made.

    Mutable on purpose: the planner resets it at the start of each turn and the
    toolset wrapper counts calls into it. One planner, one event loop.
    """

    limit: int = MAX_TOOL_CALLS_PER_TURN
    used: int = 0
    turn_start_tick: int = 0

    @property
    def left(self) -> int:
        """Calls still allowed this turn."""
        return max(0, self.limit - self.used)

    def reset(self, limit: int, tick: int) -> None:
        """Start a new turn at world tick `tick` with `limit` calls to spend."""
        self.limit = limit
        self.used = 0
        self.turn_start_tick = tick

    def spend(self) -> None:
        """Spend the whole budget, so every later call this turn is refused."""
        self.used = self.limit

    def footer(self) -> str:
        """The line appended to every tool result."""
        line = f"[tool budget: {self.left} of {self.limit} calls left this turn]"
        if self.left == 0:
            return f"{line} That was your last call: write your reflection now."
        if self.left <= TOOL_BUDGET_WARNING_AT:
            return (
                f"{line} Nearly spent: hand the work to Jev with one start_stint "
                "or write your reflection."
            )
        return line


def turn_clock_line(model: WorldModel, turn_start_tick: int) -> str:
    """The tick, the world clock, and how much time this planner turn has cost."""
    elapsed = max(0, model.tick - turn_start_tick)
    return (
        f"[tick {model.tick} · {model.clock.as_text()}; "
        f"this turn has cost {elapsed} ticks so far]"
    )


def alert_window_start(tick: int, turn_start_tick: int) -> int:
    """The first tick whose bites still count as "under attack" right now.

    A turn lasts hundreds of ticks because stints run inside it, so damage
    from early in the turn says nothing about now: only the last few ticks do.
    """
    return max(turn_start_tick, tick - RECENT_ATTACK_TICKS)


def threat_alert(model: WorldModel, since_tick: int) -> str:
    """A loud line when the actor is being bitten or a wolf is close, else `""`.

    The planner cannot act every tick, so the alert always ends the same way:
    whatever the response is, it has to be handed to Jev.
    """
    info = model.self_info
    hits = [hit for hit in model.damage_since(since_tick) if hit.attacker_id]
    if hits and info.alive:
        lost = sum(hit.amount for hit in hits)
        attackers = ", ".join(sorted({hit.attacker_id for hit in hits}))
        return (
            f"!! UNDER ATTACK: {attackers} took {lost} health from you in the "
            f"last {max(1, model.tick - since_tick)} ticks "
            f"(health {info.health}/{info.max_health}). "
            f"{HAND_BACK_ADVICE}"
        )
    wolves = model.wolves_near(WOLF_ALERT_RADIUS)
    if wolves and info.alive:
        nearest = wolves[0]
        distance = chebyshev(nearest.position, info.position)
        allies = len(model.allies_near(info.position, 3))
        return (
            f"!! WOLF NEAR: {nearest.entity_id} is {distance} tiles away at "
            f"{nearest.position}, {allies} settlers within 3 tiles of you. "
            f"{HAND_BACK_ADVICE}"
        )
    return ""


HAND_BACK_ADVICE = (
    "It acts every tick and you only act every few ticks. Whatever you want "
    "your body to do about it has to go to Jev in a start_stint brief; nothing "
    "you do from here happens at world speed."
)


BUDGET_SPENT_MESSAGE = (
    "NOT EXECUTED: your tool budget for this turn is spent. Make no more tool "
    "calls. Write your one-paragraph reflection now; your next turn starts "
    "with a full budget."
)


@dataclass
class PlannerDeps:
    """Dependencies handed to every tool call."""

    bridge: AgentBridge
    memory_path: Path
    budget: ToolBudget = field(default_factory=ToolBudget)
    # Everything this day has held, for the next journal rewrite.
    day_log: DayLog = field(default_factory=DayLog)


def _tool_signature_line(tool_def: Any) -> str:
    """`sleep takes: bed (optional), other_arg` from a tool's JSON schema.

    pydantic-ai's own validation-error text names only the field that was
    wrong (e.g. "Extra inputs are not permitted"), never what the tool
    actually accepts, so a model that guessed a kwarg name gets no way to
    self-correct. This is appended to every validation retry.
    """
    schema = tool_def.parameters_json_schema or {}
    properties = schema.get("properties", {})
    if not properties:
        return f"{tool_def.name} takes no arguments"
    required = set(schema.get("required", ()))
    names = ", ".join(
        name if name in required else f"{name} (optional)" for name in properties
    )
    return f"{tool_def.name} takes: {names}"


class _FriendlyArgsValidator:
    """Wraps a tool's args validator to name its parameters on failure.

    `ToolManager._validate_tool_args` calls `validate_json`/`validate_python`
    directly on `ToolsetTool.args_validator`; wrapping it here is the one place
    that reaches every planner tool's validation without touching pydantic-ai
    itself (docs/09 section 10, item 3).
    """

    def __init__(self, inner: Any, tool_def: Any) -> None:
        self._inner = inner
        self._tool_def = tool_def

    def _retry(self, error: Exception) -> None:
        raise ModelRetry(f"{error}\n{_tool_signature_line(self._tool_def)}") from error

    def validate_json(self, *args: Any, **kwargs: Any) -> Any:
        try:
            return self._inner.validate_json(*args, **kwargs)
        except ValidationError as error:
            self._retry(error)

    def validate_python(self, *args: Any, **kwargs: Any) -> Any:
        try:
            return self._inner.validate_python(*args, **kwargs)
        except ValidationError as error:
            self._retry(error)


@dataclass
class BudgetedToolset(WrapperToolset[PlannerDeps]):
    """Counts tool calls into `deps.budget` and tells the model what is left.

    A call past the budget is refused with a message rather than an exception,
    so the turn ends with its reflection and its history instead of vanishing.
    """

    async def get_tools(
        self, ctx: RunContext[PlannerDeps]
    ) -> dict[str, ToolsetTool[PlannerDeps]]:
        tools = await self.wrapped.get_tools(ctx)
        return {
            name: replace(
                tool,
                args_validator=_FriendlyArgsValidator(
                    tool.args_validator, tool.tool_def
                ),
            )
            for name, tool in tools.items()
        }

    async def call_tool(
        self,
        name: str,
        tool_args: dict[str, Any],
        ctx: RunContext[PlannerDeps],
        tool: ToolsetTool[PlannerDeps],
    ) -> Any:
        budget = ctx.deps.budget
        if budget.left == 0:
            return BUDGET_SPENT_MESSAGE
        budget.used += 1
        model = ctx.deps.bridge.model
        day_log = ctx.deps.day_log
        day_log.add(model.tick, KIND_CALL, f"{name}({compact_args(tool_args)})")
        result = await self.wrapped.call_tool(name, tool_args, ctx, tool)
        # Recorded before the footer lines below: the journal wants what the
        # tool said, not the clock and the budget.
        day_log.add(model.tick, KIND_RESULT, str(result), tool=name)
        lines = [str(result)]
        # A reflex may have run inside the tool call, or a conversation may
        # have started and ended (or while the model was writing it); the
        # planner is told as soon as it asks anything.
        lines.extend(ctx.deps.bridge.drain_notes())
        lines.append(turn_clock_line(model, budget.turn_start_tick))
        since_tick = alert_window_start(model.tick, budget.turn_start_tick)
        alert = threat_alert(model, since_tick)
        if alert:
            lines.append(alert)
        lines.append(budget.footer())
        return "\n".join(lines)


# The one place name the code-driven `travel_to` walk uses.
DESTINATION_PLACE = "destination"


def _validated_shouts(shouts: Sequence[str]) -> tuple[str, ...]:
    """The brief's shout phrases, trimmed; too many or too long is a retry."""
    phrases = tuple(phrase.strip() for phrase in shouts if phrase.strip())
    if len(phrases) > MAX_BRIEF_SHOUTS:
        raise ModelRetry(f"at most {MAX_BRIEF_SHOUTS} shout phrases per stint")
    too_long = [phrase for phrase in phrases if len(phrase) > MAX_SHOUT_LENGTH]
    if too_long:
        raise ModelRetry(
            f"a shout phrase is at most {MAX_SHOUT_LENGTH} characters: {too_long[0]!r}"
        )
    return phrases


def settlers_met(model: WorldModel) -> list[str]:
    """Every settler this actor has ever seen, by id, sorted."""
    return sorted(
        entity.entity_id
        for entity in model.entities.values()
        if entity.entity_id != model.entity_id
        and entity.entity_type != WOLF_ENTITY_TYPE
    )


def _validated_hails(
    hails: Sequence[Mapping[str, str]], model: WorldModel
) -> tuple[BriefHail, ...]:
    """The brief's hails, checked against who this actor has actually met.

    Jev is given the settler's name and the line and nothing else, so a name it
    has never seen would be an option code could never build.
    """
    if len(hails) > MAX_BRIEF_HAILS:
        raise ModelRetry(f"at most {MAX_BRIEF_HAILS} hails per stint")
    met = settlers_met(model)
    checked: list[BriefHail] = []
    for entry in hails:
        settler = str(entry.get("settler", "")).strip()
        line = str(entry.get("line", "")).strip()
        if not settler or not line:
            raise ModelRetry(
                "each hail needs a `settler` and a `line`, for example "
                '{"settler": "dov", "line": "Dov, can we split the wall work?"}'
            )
        if settler == model.entity_id:
            raise ModelRetry("you cannot hail yourself")
        if settler not in met:
            known = ", ".join(met) or "nobody yet"
            raise ModelRetry(
                f"you have never seen a settler called {settler}; "
                f"settlers you have met: {known}"
            )
        if len(line) > items.CONVERSATION_TEXT_LIMIT:
            raise ModelRetry(
                f"a hail line is at most {items.CONVERSATION_TEXT_LIMIT} "
                f"characters: {line!r}"
            )
        purpose = str(entry.get("purpose", "")).strip()
        checked.append(BriefHail(settler=settler, line=line, purpose=purpose))
    return tuple(checked)


def _validated_places(
    places: Mapping[str, Sequence[int]], model: WorldModel
) -> dict[str, Coord]:
    """The brief's named places, checked; anything wrong is a retry.

    Jev is given the offset to each place and never the coordinate, so a name
    that is also an object or settler id would be two different things in one
    option list.
    """
    if len(places) > MAX_BRIEF_PLACES:
        raise ModelRetry(f"at most {MAX_BRIEF_PLACES} places per brief")
    checked: dict[str, Coord] = {}
    for name, position in places.items():
        if not PLACE_NAME_PATTERN.match(name):
            raise ModelRetry(
                f"a place name is 1 to 24 characters of lowercase letters, "
                f"digits and underscores: {name!r}"
            )
        if name in model.objects or name in model.entities:
            raise ModelRetry(
                f"the place name {name!r} is already the id of a known object "
                "or settler; pick another name"
            )
        pair = list(position)
        if len(pair) != 2:
            raise ModelRetry(f"the place {name!r} needs an [x, y] pair")
        checked[name] = (int(pair[0]), int(pair[1]))
    return checked


def _direction_value(name: str) -> pb.Direction:
    key = name.strip().upper()
    if key not in NAME_TO_DIRECTION:
        raise ModelRetry(
            f"unknown direction {name!r}; use one of {sorted(NAME_TO_DIRECTION)}"
        )
    return NAME_TO_DIRECTION[key]


# Object types that show up in bulk once building starts; `describe_world`
# prints a count and one example for these instead of three.
BULK_OBJECT_TYPES: frozenset[str] = items.GROUND_LAYER_KINDS | frozenset(
    {items.WOOD_WALL, items.STONE_WALL}
)

# The things a builder keeps asking about: where can I craft, where can I heal,
# and where is the raw material for planks, rope and stone walls.
BUILD_SITE_TYPES: tuple[str, ...] = (
    items.WORKSHOP_TABLE,
    items.FURNACE,
    items.ANVIL,
    items.BED,
    items.DOOR,
    items.REEDS,
    items.CLAY_DEPOSIT,
    items.COPPER_VEIN,
    items.IRON_VEIN,
)


def _build_site_lines(model: WorldModel) -> list[str]:
    """One terse line per building landmark the actor knows about."""
    lines: list[str] = []
    origin = model.position
    for object_type in BUILD_SITE_TYPES:
        known = model.objects_by_type([object_type])
        if not known:
            continue
        nearest = known[0]
        distance = chebyshev(nearest.position, origin)
        reach = "here" if distance <= 1 else f"d{distance}"
        lines.append(
            f"  {object_type}: {len(known)} known; nearest {nearest.object_id} "
            f"at {nearest.position} ({reach})"
        )
    if not lines:
        return []
    return ["building landmarks:"] + lines


def _roster_lines(model: WorldModel) -> list[str]:
    """Every settler the actor has met, with where and when it last saw them."""
    met = sorted(
        (
            entity
            for entity in model.entities.values()
            if entity.entity_id != model.entity_id and entity.entity_type != "wolf"
        ),
        key=lambda entity: entity.entity_id,
    )
    if not met:
        return ["settlers you have met: none yet"]
    lines = ["settlers you have met (last seen):"]
    for entity in met:
        age = model.tick - entity.last_seen
        when = "now" if age <= 0 else f"{age} ticks ago"
        if not entity.alive:
            state = ", dead"
        elif entity.asleep:
            state = f", asleep as of {when}"
        else:
            state = ""
        lines.append(
            f"  {entity.entity_id} at {entity.position} "
            f"(d{chebyshev(entity.position, model.position)}, {when}{state})"
        )
    return lines


def _conversation_lines(model: WorldModel) -> list[str]:
    """The conversations in view: id, anchor, who is seated, free seats."""
    conversations = model.conversations()
    if not conversations:
        return ["conversations in view: none"]
    lines = ["conversations in view:"]
    lines.extend(f"  {conversation.summary()}" for conversation in conversations)
    return lines


def _heard_line(utterance: HeardUtterance, tick: int) -> str:
    """One heard line; shouts carry their origin so the planner can go there."""
    if utterance.channel != items.SHOUT_CHANNEL:
        return f'{utterance.speaker_id}: "{utterance.text}"'
    return (
        f"{utterance.speaker_id} SHOUTED from {utterance.position}, "
        f'{tick - utterance.tick} ticks ago: "{utterance.text}"'
    )


def parse_tile_list(text: str) -> list[Coord]:
    """Parse `"12,30; 13,30"` into coordinates; empty text yields no tiles.

    Raises:
        ModelRetry: when a pair is not two integers, so the model can fix it.
    """
    tiles: list[Coord] = []
    for chunk in text.replace("(", " ").replace(")", " ").split(";"):
        chunk = chunk.strip()
        if not chunk:
            continue
        parts = chunk.split(",")
        if len(parts) != 2:
            raise ModelRetry(
                f"{chunk!r} is not a tile; write tiles as 'x,y' separated by ';'"
            )
        try:
            tiles.append((int(parts[0].strip()), int(parts[1].strip())))
        except ValueError as error:
            raise ModelRetry(f"{chunk!r} is not a pair of integers: {error}") from error
    return tiles


def _pile_lines(model: WorldModel, limit: int) -> list[str]:
    """The nearest item piles and what each holds, one line per pile."""
    position = model.self_info.position
    lines: list[str] = []
    for pile in model.objects_by_type([items.ITEM_PILE])[:limit]:
        contents = pile.contents()
        if not contents:
            continue
        summary = ", ".join(f"{k} x{v}" for k, v in sorted(contents.items()))
        lines.append(
            f"item pile {pile.object_id} at {pile.position} "
            f"(d{chebyshev(pile.position, position)}): {summary}"
        )
    return lines


def _sign_lines(model: WorldModel, limit: int) -> list[str]:
    """Every sign in view plus the nearest few known, with what each reads."""
    signs = model.signs_known()
    if not signs:
        return []
    position = model.self_info.position
    lines = ["signs:"]
    for sign in signs[:limit]:
        distance = chebyshev(sign.position, position)
        where = "in view" if distance <= VIEW_RADIUS else f"d{distance}"
        if sign.sign_text:
            reads = (
                f'"{sign.sign_text}" (by {sign.sign_author or "nobody"}, '
                f"tick {sign.sign_tick})"
            )
        else:
            reads = "blank"
        lines.append(f"  {sign.object_id} at {sign.position} ({where}): {reads}")
    return lines


def describe_world(model: WorldModel) -> str:
    """The `look()` summary: everything the actor knows, in a readable block."""
    info = model.self_info
    dx, dy = model.settlement_offset()
    inventory = (
        ", ".join(f"{kind} x{count}" for kind, count in sorted(info.inventory.items()))
        or "empty"
    )
    lines = [
        f"tick {model.tick} · {model.clock.as_text()}, you are "
        f"{model.entity_id} at {info.position}",
        f"health {info.health}/{info.max_health}, food {info.food}/{info.max_food}"
        f", fatigue {info.fatigue}/{info.max_fatigue} ({info.fatigue_word})"
        f", wielded: {info.wielded or 'nothing'}, alive: {info.alive}"
        f"{', asleep' if info.asleep else ''}",
        f"inventory: {inventory}",
        f"settlement centre is dx {dx}, dy {dy} (distance "
        f"{chebyshev(info.position, model.settlement)})",
    ]

    grouped: dict[str, list[str]] = {}
    for obj in model.objects.values():
        grouped.setdefault(obj.object_type, []).append(obj.object_id)
    if grouped:
        lines.append("known objects:")
        for object_type in sorted(grouped):
            count = len(grouped[object_type])
            # Roads, floors and walls come in dozens; a count and one example is
            # all the planner can act on without drowning the prompt.
            shown = 1 if object_type in BULK_OBJECT_TYPES else 3
            nearest = model.objects_by_type([object_type])[:shown]
            examples = ", ".join(
                f"{o.object_id} at {o.position} "
                f"(d{chebyshev(o.position, info.position)})"
                for o in nearest
            )
            lines.append(f"  {object_type}: {count} known; nearest {examples}")
    else:
        lines.append("known objects: none yet")

    lines.extend(_build_site_lines(model))

    for chest in model.objects_by_type(["chest"])[:4]:
        contents = chest.contents() or {"(empty)": 0}
        summary = ", ".join(f"{k} x{v}" for k, v in sorted(contents.items()))
        lines.append(f"chest {chest.object_id} at {chest.position}: {summary}")

    lines.extend(_pile_lines(model, PILES_SHOWN))
    lines.extend(_sign_lines(model, SIGNS_SHOWN))

    for board in model.objects_by_type(["message_board"])[:2]:
        unread = model.unread_note_count(board.object_id)
        new_text = f", {unread} new since you last read" if unread else ""
        lines.append(f"message board {board.object_id} at {board.position}{new_text}:")
        by_slot = board.notes_by_slot()
        if not by_slot:
            lines.append("  (no notes yet)")
        for slot, note in sorted(by_slot.items()):
            title = str(note.get("title", ""))
            author = str(note.get("author", "?"))
            if not title:
                continue
            mark = " (new)" if model.is_note_unread(board.object_id, slot, note) else ""
            lines.append(f"  [{slot}] {title} - {author}{mark}")

    visible = model.entities_near(8)
    if visible:
        lines.append("entities in view:")
        for entity in visible:
            wielding = f", wielding {entity.wielded}" if entity.wielded else ""
            state = ""
            if not entity.alive:
                state = ", dead"
            elif entity.asleep:
                state = ", asleep"
            lines.append(
                f"  {entity.entity_id} ({entity.entity_type}) at {entity.position}, "
                f"hp {entity.health}/{entity.max_health}{wielding}{state}"
            )
    else:
        lines.append("entities in view: none")

    lines.extend(_roster_lines(model))
    lines.extend(_conversation_lines(model))

    heard = model.recent_utterances(5)
    if heard:
        lines.append("recently heard:")
        lines.extend(f"  {_heard_line(u, model.tick)}" for u in heard)

    recent = model.recent_history(8)
    if recent:
        lines.append("your recent ticks:")
        lines.extend(f"  {line}" for line in recent)

    return "\n".join(lines)


def build_planner_agent(
    model_name: str, settler_count: int = items.DEFAULT_SETTLER_COUNT
) -> Agent[PlannerDeps, str]:
    """Create the pydantic-ai agent with every planner tool registered."""
    tools: FunctionToolset[PlannerDeps] = FunctionToolset()
    agent: Agent[PlannerDeps, str] = Agent(
        model_name,
        deps_type=PlannerDeps,
        output_type=str,
        system_prompt=settlement_narrative(settler_count),
        retries=PLANNER_TOOL_RETRIES,
        model_settings=planner_model_settings(model_name),
        toolsets=[BudgetedToolset(tools)],
    )

    @tools.tool
    async def look(ctx: RunContext[PlannerDeps]) -> str:
        """Look around: your stats, inventory, known objects, and recent events."""
        return describe_world(ctx.deps.bridge.model)

    @tools.tool
    async def start_stint(
        ctx: RunContext[PlannerDeps],
        instruction: str,
        success_condition: str,
        max_ticks: int,
        notes: str = "",
        check_every: int = 1,
        shouts: Sequence[str] = (),
        hails: Sequence[Mapping[str, str]] = (),
        places: Mapping[str, Sequence[int]] = {},
    ) -> str:
        """Hand control to Jev until the brief is done, then read the report.

        Jev cannot speak on its own: the only lines it can say are the exact
        phrases you give it here in `shouts` and `hails`. An instruction like
        "talk to finn about the wall" does nothing by itself; give Jev a hail
        for finn.

        Args:
            instruction: what Jev should do, concretely, in one or two sentences.
            success_condition: what Jev should be able to see when it is done.
            max_ticks: hard tick budget; the stint ends when it runs out.
            notes: extra hints, for example "eat a berry when food is below 40".
            check_every: ask Jev every Nth tick and repeat the last action between.
            shouts: the exact phrases Jev may shout during this stint, for
                example ["Stone to spare at the rocks.", "Come to the workshop."].
                Good for calling people to Jev's spot or flagging an emergency
                while it works; not good for a back-and-forth, since nobody
                can answer it. Say in the instruction when to use each. Jev
                cannot shout anything else; leave it empty and Jev stays
                quiet.
            hails: settlers Jev may address, each as
                {"settler": "dov", "line": "Dov, can we split the wall work?",
                "purpose": "agree who builds which side"}, at most 3.
                `purpose` is optional: what you want out of the conversation
                the hail starts, shown only to this settler once it is seated.
                Next to that settler Jev says the line and a conversation with
                the two of them starts; further off it can walk over first.
                Good for having Jev start a conversation with a particular
                settler when it meets them, or at the point you name; say in
                the instruction when. Jev cannot invent one, and a settler you
                have not met is refused.
            places: named map positions Jev may walk to, for example
                {"the_lake_shore": [1539, 974]}. Jev is offered one step toward
                each and is shown how far off it is; it never sees the
                coordinate. Names are lowercase letters, digits and
                underscores, at most 6 per brief.
        """
        brief = Brief(
            instruction=instruction,
            success_condition=success_condition,
            max_ticks=max(1, max_ticks),
            notes=notes,
            check_every=max(1, check_every),
            shouts=_validated_shouts(shouts),
            hails=_validated_hails(hails, ctx.deps.bridge.model),
            places=_validated_places(places, ctx.deps.bridge.model),
        )
        report = await ctx.deps.bridge.run_stint(brief)
        return report.to_text()

    @tools.tool
    async def travel_to(
        ctx: RunContext[PlannerDeps], x: int, y: int, max_ticks: int
    ) -> str:
        """Walk to a map position, reacting to danger on the way."""
        brief = Brief(
            instruction="Walk to the destination.",
            success_condition="you are standing on or next to the destination",
            max_ticks=max(1, max_ticks),
            notes="Follow the path; react to danger; eject on arrival.",
            travel=TravelState(target=(x, y), label=DESTINATION_PLACE),
            # Jev reasons about offsets, never coordinates, so the target is a
            # named place and the numbers stay on this side of the call.
            places={DESTINATION_PLACE: (x, y)},
        )
        report = await ctx.deps.bridge.run_stint(brief)
        return report.to_text()

    @tools.tool
    async def build(
        ctx: RunContext[PlannerDeps],
        kind: str,
        shape: str,
        x1: int,
        y1: int,
        x2: int,
        y2: int,
        max_ticks: int = 60,
        skip: str = "",
        tiles: str = "",
    ) -> str:
        """Build a shape out of one kind of piece, placing it tile by tile.

        Code does the walking and the coordinates; Jev is not involved. You must
        already be carrying the pieces - craft them first and check how many the
        shape needs.

        Args:
            kind: the building item to place, for example wood_wall, door, road,
                wood_floor, stone_wall, stone_floor, bed, workshop_table.
            shape: "line" from (x1,y1) to (x2,y2), "rect" for the outline of the
                rectangle with those two corners, "rect_filled" for every tile
                inside it, or "tiles" to use the `tiles` argument instead.
            x1: first corner or line start, map x.
            y1: first corner or line start, map y.
            x2: second corner or line end, map x.
            y2: second corner or line end, map y.
            max_ticks: tick budget; roughly two ticks per tile plus the walk.
            skip: tiles to leave out, as "x,y; x,y" - this is how you leave the
                door gap in a wall ring.
            tiles: the explicit tile list for shape "tiles", as "x,y; x,y".

        Returns:
            The stint report plus what was placed, what was skipped and why the
            build stopped: build_done, build_out_of_items, build_blocked,
            build_danger, build_would_seal_you_in or ticks_exhausted. A build
            that runs out of pieces, or is called with none, says how many
            more it needs and the recipe for them.
        """
        try:
            plan = make_plan(
                kind=kind,
                shape=shape,
                start=(x1, y1),
                end=(x2, y2),
                explicit=parse_tile_list(tiles),
                skip=parse_tile_list(skip),
            )
        except BuildPlanError as error:
            raise ModelRetry(str(error)) from error
        inventory = ctx.deps.bridge.model.self_info.inventory
        if inventory.get(plan.kind, 0) <= 0:
            return missing_pieces_text(plan, inventory)
        executor = BuildExecutor(plan)
        instruction, success = build_brief_text(plan)
        brief = Brief(
            instruction=instruction,
            success_condition=success,
            max_ticks=max(1, max_ticks),
            notes=f"shapes: {sorted(SHAPES)}",
        )
        report = await ctx.deps.bridge.run_stint(brief, executor)
        return f"{report.to_text()}\n{executor.summary()}"

    _register_single_tick_tools(tools)
    _register_conversation_tools(tools)
    _register_reflex_tools(tools)
    _register_memory_tools(tools)
    return agent


def action_succeeded(outcome: str) -> bool:
    """Whether a direct-action result line reports a successful world action.

    `direct_action` renders the world's own event as `<what> -> <type> ok: ...`
    or `<what> -> <type> failed: ...`, and `-> submitted` when no event came
    back at all.
    """
    return " ok:" in outcome


# `tick._process_say_phase` (world/src/world/tick.py) reports a successful
# `say` as `"say ok: heard: <comma-separated entity ids>"` (empty when nobody
# was in earshot); `direct_action` wraps that as `"<description> -> <that>"`.
_HEARD_MARKER = "say ok: heard: "


def _speak_result(outcome: str, verb: str, radius: int) -> str:
    """Replace the raw hearer list with a plain sentence naming who heard.

    Anything that is not a successful say/shout (a failure, an
    `interrupted: ...`, an asleep rejection) passes through unchanged.
    """
    marker_index = outcome.find(_HEARD_MARKER)
    if marker_index == -1:
        return outcome
    ids_csv = outcome[marker_index + len(_HEARD_MARKER) :]
    heard = [entity_id.strip() for entity_id in ids_csv.split(",") if entity_id.strip()]
    if not heard:
        return f"nobody was within {radius} tiles to hear it"
    return f"{verb} to {', '.join(heard)} (within {radius} tiles)"


async def _sit_through(ctx: RunContext[PlannerDeps], outcome: str) -> str:
    """Wait out the conversation the last action started, then report it."""
    if not action_succeeded(outcome):
        return outcome
    report = await ctx.deps.bridge.await_conversation()
    if report is None:
        return f"{outcome}\nno conversation started"
    return f"{outcome}\n{report.to_text()}"


def _register_conversation_tools(tools: FunctionToolset[PlannerDeps]) -> None:
    @tools.tool
    async def open_conversation(
        ctx: RunContext[PlannerDeps], direction: str, opening_line: str, purpose: str
    ) -> str:
        """Open a conversation next to you and stay in it until it is over.

        Good for gathering people around a spot for back-and-forth: the only
        channel where the others can answer you.

        The anchor is the neighbouring tile in `direction`; it must be free.
        The opening line is said out loud, with the conversation attached to
        it, so everyone within ten tiles hears it and can walk over and join.
        This call returns when the conversation has ended, with the transcript,
        what changed hands and the note you kept.

        Args:
            direction: N, NE, E, SE, S, SW, W or NW: where the anchor tile goes.
            opening_line: what you say as you open it, at most 300 characters.
            purpose: what you want out of this conversation; only you see it,
                and it is handed back to you while you are in the conversation.
        """
        value = _direction_value(direction)
        ctx.deps.bridge.set_conversation_purpose(purpose)
        try:
            outcome = await ctx.deps.bridge.direct_action(
                converse_intent(ACTION_OPEN, text=opening_line, direction=value),
                f"open a conversation to the {direction.strip().upper()}",
            )
            return await _sit_through(ctx, outcome)
        finally:
            ctx.deps.bridge.set_conversation_purpose("")

    @tools.tool
    async def join_conversation(
        ctx: RunContext[PlannerDeps], conversation_id: str, max_ticks: int = 40
    ) -> str:
        """Walk to a conversation you can see, take a seat, and talk until it ends.

        Good for joining in on a conversation someone else already opened,
        rather than starting your own with `talk_to` or `open_conversation`.

        Code does the walking: it picks a free tile next to the anchor and goes
        there. The call returns when the conversation has ended.

        Args:
            conversation_id: the id `look` printed for it.
            max_ticks: tick budget for the walk there.
        """
        bridge = ctx.deps.bridge
        conversation = bridge.model.conversation_by_id(conversation_id)
        if conversation is None:
            known = bridge.model.conversations()
            if known:
                listing = ", ".join(f"{c.conversation_id} at {c.anchor}" for c in known)
                return (
                    f"you have not seen a conversation called {conversation_id!r}; "
                    f"in view: {listing}. talk_to walks up to a settler and "
                    "starts one instead of joining an existing anchor"
                )
            return (
                f"you have not seen a conversation called {conversation_id!r} "
                "and none is in view; talk_to walks up to a settler and starts "
                "one"
            )
        if conversation.free_seats <= 0:
            return (
                f"{conversation_id} has no free seat "
                f"({len(conversation.participants)} settlers in it)"
            )
        walked = ""
        if chebyshev(bridge.model.position, conversation.anchor) != 1:
            walked = await _walk_to_anchor(ctx, conversation_id, max_ticks)
            if chebyshev(bridge.model.position, conversation.anchor) != 1:
                return f"{walked}\nyou are not next to {conversation_id} yet"
        outcome = await bridge.direct_action(
            converse_intent(ACTION_JOIN, conversation_id=conversation_id),
            f"join {conversation_id}",
        )
        seated = await _sit_through(ctx, outcome)
        return f"{walked}\n{seated}" if walked else seated

    @tools.tool
    async def talk_to(
        ctx: RunContext[PlannerDeps],
        entity_id: str,
        opening_line: str,
        purpose: str,
        max_ticks: int = 40,
    ) -> str:
        """Walk up to a settler, start a conversation, and stay until it is over.

        Good for reaching one particular settler for back-and-forth, wherever
        they are, rather than waiting for them to come to you or to a board.

        Code walks you to a free tile next to them and hails them: you say
        `opening_line` out loud, a conversation appears on a free tile beside
        you both holding the two of you, your line is its first line and they
        speak next. They do not have to be expecting you, but a settler cannot
        be hailed until 60 ticks after its last conversation ended, and
        neither of you can already be in one, be asleep or be dead. A sleeping
        settler cannot be hailed at all; this refuses at once if you can see
        them asleep, and says so if they turn out to be asleep once you reach
        them. The call returns when the conversation has ended.

        Args:
            entity_id: the settler you want to talk to.
            opening_line: what you say to them as you walk up, at most 300
                characters.
            purpose: what you want out of this conversation; only you see it,
                and it is handed back to you while you are in the conversation.
            max_ticks: tick budget for the walk there.
        """
        ctx.deps.bridge.set_conversation_purpose(purpose)
        try:
            return await _hail(ctx, entity_id, opening_line, max_ticks)
        finally:
            ctx.deps.bridge.set_conversation_purpose("")

    @tools.tool
    async def give(
        ctx: RunContext[PlannerDeps], entity_id: str, kind: str, amount: int = 1
    ) -> str:
        """Hand items to a settler next to you or seated in your conversation.

        Args:
            entity_id: who receives them.
            kind: the item name, exactly as your inventory spells it.
            amount: how many.
        """
        return await ctx.deps.bridge.direct_action(
            give_intent(entity_id, kind, amount),
            f"give {max(1, amount)} {kind} to {entity_id}",
        )


# How many times the code-owned walk to a moving settler re-aims before it
# gives up; every leg spends part of the one tick budget.
WALK_LEGS = 3

# `EntityInfo.entity_type` for a wolf, which cannot be hailed.
WOLF_ENTITY_TYPE = "wolf"


async def _hail(
    ctx: RunContext[PlannerDeps], entity_id: str, opening_line: str, max_ticks: int
) -> str:
    """Walk up to a settler and address it, starting a conversation (docs/09, section 9)."""
    bridge = ctx.deps.bridge
    line = opening_line.strip()
    if not line:
        return f"a hail needs an opening line: what you say when you reach {entity_id}"
    known = bridge.model.entities.get(entity_id)
    if known is None or known.entity_type == WOLF_ENTITY_TYPE:
        return f"you have never seen a settler called {entity_id}"
    if known.asleep and known.last_seen == bridge.model.tick:
        return f"{entity_id} is asleep right now and cannot be hailed"

    walked = await _walk_to_settler(ctx, entity_id, max_ticks)
    known = bridge.model.entities.get(entity_id)
    head = f"{walked}\n" if walked else ""
    if known is None:
        return f"{head}you have lost track of {entity_id}"
    if chebyshev(bridge.model.position, known.position) != 1:
        return (
            f"{head}you are not next to {entity_id} yet; last seen at "
            f"{known.position} on tick {known.last_seen}"
        )
    if known.asleep and known.last_seen == bridge.model.tick:
        return f"{head}{entity_id} is asleep now that you have reached them"
    outcome = await bridge.direct_action(
        converse_intent(ACTION_HAIL, target_entity_id=entity_id, text=line),
        f"hail {entity_id} with {line!r}",
    )
    seated = await _sit_through(ctx, outcome)
    if not action_succeeded(outcome):
        return f"{head}{seated}; {entity_id} was last seen at {known.position}"
    return f"{head}{seated}"


async def _walk_to_settler(
    ctx: RunContext[PlannerDeps], entity_id: str, max_ticks: int
) -> str:
    """Walk onto a free tile next to a settler, re-aiming as it moves.

    The target has its own plans, so each leg walks to where it was last seen
    and the next leg aims again. The whole walk shares one tick budget.
    """
    bridge = ctx.deps.bridge
    legs: list[str] = []
    ticks_left = max(1, max_ticks)
    for _ in range(WALK_LEGS):
        known = bridge.model.entities.get(entity_id)
        if known is None:
            legs.append(f"you have lost track of {entity_id}")
            break
        if chebyshev(bridge.model.position, known.position) == 1:
            break
        tiles = free_seat_tiles(bridge.model, known.position)
        if not tiles:
            legs.append(f"every tile next to {entity_id} is taken or blocked")
            break
        driver = ApproachDriver(tiles, entity_id)
        brief = Brief(
            instruction=f"Walk to a free tile next to {entity_id}.",
            success_condition=f"you are standing next to {entity_id}",
            max_ticks=ticks_left,
        )
        report = await bridge.run_stint(brief, driver)
        legs.append(report.to_text())
        ticks_left -= report.ticks_used
        if ticks_left <= 0 or report.end_reason != ApproachDriver.ARRIVED:
            # Out of budget, no path, or the walk ended for a reason of its
            # own (a reflex, or a conversation that started on the way).
            break
    return "\n".join(legs)


async def _walk_to_anchor(
    ctx: RunContext[PlannerDeps], conversation_id: str, max_ticks: int
) -> str:
    """Run the code-owned walk to a free tile beside the conversation's anchor."""
    bridge = ctx.deps.bridge
    conversation = bridge.model.conversation_by_id(conversation_id)
    if conversation is None:
        return f"{conversation_id} is gone"
    return await _walk_next_to(ctx, conversation.anchor, conversation_id, max_ticks)


async def _walk_next_to(
    ctx: RunContext[PlannerDeps], target: Coord, label: str, max_ticks: int
) -> str:
    """Run the code-owned walk onto a free tile next to `target`.

    Shared by `join_conversation`, whose target is the anchor, and `talk_to`,
    whose target is the settler who is open to talk.
    """
    bridge = ctx.deps.bridge
    tiles = free_seat_tiles(bridge.model, target)
    if not tiles:
        return f"every tile next to {label} is taken or blocked"
    driver = ApproachDriver(tiles, f"a free tile next to {label}")
    brief = Brief(
        instruction=f"Walk to a free tile next to {label}.",
        success_condition=f"you are standing next to {target}",
        max_ticks=max(1, max_ticks),
    )
    report = await bridge.run_stint(brief, driver)
    return report.to_text()


def _register_reflex_tools(tools: FunctionToolset[PlannerDeps]) -> None:
    @tools.tool
    async def set_reflex(
        ctx: RunContext[PlannerDeps],
        instruction: str,
        success_condition: str,
        max_ticks: int,
        trigger_distance: int,
        notes: str = "",
        shouts: Sequence[str] = (),
        places: Mapping[str, Sequence[int]] = {},
    ) -> str:
        """Register the brief code runs for you when a wolf is near or you are hit.

        It replaces any reflex you had, it is kept across turns, and it fires
        while you are thinking, while a single-tick tool or a wait is in
        flight, while you are in a conversation, and during build or
        travel_to. It never interrupts a start_stint.

        Args:
            instruction: what Jev should do, concretely, when it fires.
            success_condition: what Jev should be able to see when it is done.
            max_ticks: tick budget for the reflex stint.
            trigger_distance: how close a living wolf must be to start it, 1 to 8
                tiles. Damage from an attacker starts it whatever the distance.
            notes: extra hints for Jev, as in start_stint.
            shouts: the exact phrases Jev may shout while the reflex runs.
                Good for raising the alarm or calling for help the moment the
                reflex fires; nobody can answer it.
            places: named map positions Jev may walk to, as in start_stint.
        """
        brief = ReflexBrief(
            instruction=instruction,
            success_condition=success_condition,
            max_ticks=max(1, max_ticks),
            trigger_distance=clamp_trigger_distance(trigger_distance),
            notes=notes,
            shouts=_validated_shouts(shouts),
            places=_validated_places(places, ctx.deps.bridge.model),
        )
        ctx.deps.bridge.set_reflex(brief)
        return f"reflex registered: {brief.prompt_line()}"

    @tools.tool
    async def clear_reflex(ctx: RunContext[PlannerDeps]) -> str:
        """Remove your reflex brief; nothing runs for you until you set another."""
        ctx.deps.bridge.clear_reflex()
        return "reflex cleared"


async def _craft_recipe(
    ctx: RunContext[PlannerDeps], recipe: str, known: items.Recipe
) -> str:
    """Repeat the craft action for `recipe` until it finishes or an action fails."""
    lines: list[str] = []
    for _ in range(known.work + CRAFT_ACTION_MARGIN):
        outcome = await ctx.deps.bridge.direct_action(
            pb.Intent(craft=pb.CraftIntent(recipe=recipe)), f"craft {recipe}"
        )
        lines.append(outcome)
        if not action_succeeded(outcome) or CRAFTED_DETAIL in outcome:
            break
    return "\n".join(lines)


def _refuse_wrong_note_target(
    ctx: RunContext[PlannerDeps], object_id: str, wanted_kind: str, right_tool: str
) -> str:
    """`""` when `object_id` is a `wanted_kind`, else why and what to use instead.

    Only checked against what this actor has actually seen; an object it has
    never observed is left to the world, which still refuses it by id.
    """
    obj = ctx.deps.bridge.model.objects.get(object_id)
    if obj is None or obj.object_type == wanted_kind:
        return ""
    return f"{object_id} is a {obj.object_type}, not a {wanted_kind}; use {right_tool}"


async def _submit_sign_text(
    ctx: RunContext[PlannerDeps], sign_id: str, text: str
) -> str:
    """Write `text` on `sign_id` with the world's one-slot note intent."""
    what = f'write "{text}" on {sign_id}' if text else f"blank {sign_id}"
    return await ctx.deps.bridge.direct_action(
        pb.Intent(
            write_note=pb.WriteNoteIntent(
                object_id=sign_id,
                slot=items.SIGN_SLOT,
                title="",
                text=text,
            )
        ),
        what,
    )


def _register_single_tick_tools(tools: FunctionToolset[PlannerDeps]) -> None:
    @tools.tool
    async def eat(ctx: RunContext[PlannerDeps], kind: str = "berry") -> str:
        """Eat something from your pack to restore food."""
        return await ctx.deps.bridge.direct_action(
            pb.Intent(eat=pb.EatIntent(item_type=kind, amount=1)), f"eat {kind}"
        )

    @tools.tool
    async def pickup(ctx: RunContext[PlannerDeps], kind: str, amount: int = 1) -> str:
        """Take items from the pile your body is standing on.

        A pile is on one tile, and you must be on that tile: `travel_to` its
        position first. If there is no pile under you, the result names the
        nearest piles you know of and what they hold.
        """
        outcome = await ctx.deps.bridge.direct_action(
            pb.Intent(pickup=pb.PickupIntent(kind=kind, amount=amount)),
            f"pickup {amount} {kind}",
        )
        if action_succeeded(outcome):
            return outcome
        piles = _pile_lines(ctx.deps.bridge.model, PILES_SHOWN)
        if not piles:
            return outcome
        return "\n".join([outcome, "piles you know of:", *piles])

    @tools.tool
    async def drop(ctx: RunContext[PlannerDeps], kind: str, amount: int = 1) -> str:
        """Drop items onto your tile as a pile."""
        return await ctx.deps.bridge.direct_action(
            pb.Intent(drop=pb.DropIntent(kind=kind, amount=amount)),
            f"drop {amount} {kind}",
        )

    @tools.tool
    async def deposit(
        ctx: RunContext[PlannerDeps], object_id: str, kind: str, amount: int = 1
    ) -> str:
        """Put items into a chest on your tile or next to it."""
        return await ctx.deps.bridge.direct_action(
            pb.Intent(
                deposit=pb.DepositIntent(object_id=object_id, kind=kind, amount=amount)
            ),
            f"deposit {amount} {kind} into {object_id}",
        )

    @tools.tool
    async def withdraw(
        ctx: RunContext[PlannerDeps], object_id: str, kind: str, amount: int = 1
    ) -> str:
        """Take items out of a chest on your tile or next to it."""
        return await ctx.deps.bridge.direct_action(
            pb.Intent(
                withdraw=pb.WithdrawIntent(
                    object_id=object_id, kind=kind, amount=amount
                )
            ),
            f"withdraw {amount} {kind} from {object_id}",
        )

    @tools.tool
    async def craft(ctx: RunContext[PlannerDeps], recipe: str) -> str:
        """Craft one recipe, repeating the craft action until the item is made.

        A station recipe needs that station on or next to your tile. A recipe of
        N actions costs N ticks here, and the call stops early on the first
        action that fails.

        Args:
            recipe: one of the names in the recipe table in your instructions.
        """
        known = items.RECIPES.get(recipe)
        if known is None:
            return f"no such recipe {recipe!r}; known: {sorted(items.RECIPES)}"
        return await _craft_recipe(ctx, recipe, known)

    @tools.tool
    async def sleep(ctx: RunContext[PlannerDeps], bed: str = "") -> str:
        """Lie down and sleep; the call returns when you wake, and says why.

        Args:
            bed: the id of a bed on or next to your tile. Leave it empty
                (the default) to sleep on the ground where you stand.
        """
        bridge = ctx.deps.bridge
        since_tick = bridge.model.tick
        where = bed or GROUND
        outcome = await bridge.direct_action(
            pb.Intent(sleep=pb.SleepIntent(object_id=bed)),
            f"sleep on {where}",
        )
        if not action_succeeded(outcome):
            return outcome
        slept = await bridge.await_wake(since_tick)
        woke = slept or "you did not stay asleep"
        # The turn is over whatever the model wanted next: the journal rewrite
        # started when the body lay down, and this turn's history is stale.
        ctx.deps.budget.spend()
        return f"{outcome}\n{woke}\n{TURN_ENDS_AFTER_SLEEP}"

    @tools.tool
    async def wake(ctx: RunContext[PlannerDeps]) -> str:
        """Stop sleeping. While you are asleep this is the only thing you can do."""
        if not ctx.deps.bridge.model.self_info.asleep:
            return "you are not asleep"
        return await ctx.deps.bridge.direct_action(
            pb.Intent(wake=pb.WakeIntent()), "wake up"
        )

    @tools.tool
    async def equip(ctx: RunContext[PlannerDeps], kind: str = "") -> str:
        """Wield an item from your pack, or pass an empty string to unequip."""
        return await ctx.deps.bridge.direct_action(
            pb.Intent(equip=pb.EquipIntent(kind=kind)), f"equip {kind or '(nothing)'}"
        )

    @tools.tool
    async def place(
        ctx: RunContext[PlannerDeps], kind: str, direction: str = ""
    ) -> str:
        """Put one carried item down as an object.

        For walls, floors and roads use `build` instead: one call places a
        whole line or rectangle.

        Args:
            kind: what to place: chest, message_board, or any building item.
            direction: N, NE, E, SE, S, SW, W or NW for structures, which are
                placed on the neighbouring tile. Leave it empty for road,
                wood_floor and stone_floor: those go on your own tile.
        """
        if kind == items.SIGN:
            return (
                "use place_sign to put a sign up: it places the sign and writes "
                "its line in one call, and a blank sign says nothing to anybody"
            )
        if direction:
            value = _direction_value(direction)
        elif items.is_ground_kind(kind):
            value = NO_DIRECTION
        else:
            return (
                f"{kind} is a structure and needs a direction; only "
                f"{sorted(items.GROUND_LAYER_KINDS)} go on your own tile"
            )
        return await ctx.deps.bridge.direct_action(
            pb.Intent(place=pb.PlaceIntent(kind=kind, direction=value)),
            f"place {kind} {direction or 'here'}",
        )

    @tools.tool
    async def place_sign(
        ctx: RunContext[PlannerDeps], direction: str, text: str
    ) -> str:
        """Craft a sign if you need one, put it on the neighbouring tile, and
        write its line on it: one call.

        Good for a very short permanent message tied to a place: everyone who
        passes is guaranteed to be shown it, once with no need to ask. Not
        good for a temporary message; it stays until rewritten or dismantled.

        A sign holds one line of at most 80 characters plus your name and the
        tick. It blocks nobody. Every settler who comes within view of it (8
        tiles) is shown the text once, and again when it changes; nobody has to
        ask for it. A sign takes 2 wood: if you are not carrying one but are
        carrying the wood, this call crafts it first (the result says so);
        without the sign or the wood it names the shortfall and does nothing.

        This is up to three world actions (craft, place, write), so it costs
        that many ticks. If the sign goes up but the writing fails, the result
        says so and names the sign.

        Args:
            direction: N, NE, E, SE, S, SW, W or NW: the tile to put it on.
            text: the line to write, at most 80 characters.
        """
        if len(text) > items.SIGN_TEXT_MAX:
            raise ModelRetry(
                f"a sign holds at most {items.SIGN_TEXT_MAX} characters; "
                f"that line is {len(text)}"
            )
        lines: list[str] = []
        bridge = ctx.deps.bridge
        if bridge.model.self_info.inventory.get(items.SIGN, 0) <= 0:
            recipe = items.RECIPES[items.SIGN]
            needed = recipe.inputs.get(items.WOOD, 0)
            have = bridge.model.self_info.inventory.get(items.WOOD, 0)
            if have < needed:
                return (
                    f"a sign takes {needed} wood; you carry {have}, and you "
                    f"have no sign to place"
                )
            lines.append(await _craft_recipe(ctx, items.SIGN, recipe))
            if bridge.model.self_info.inventory.get(items.SIGN, 0) <= 0:
                return "\n".join(lines)
        value = _direction_value(direction)
        placed = await bridge.direct_action(
            pb.Intent(place=pb.PlaceIntent(kind=items.SIGN, direction=value)),
            f"place sign {direction}",
        )
        lines.append(placed)
        if not action_succeeded(placed):
            return "\n".join(lines)
        match = SIGN_ID_RE.search(placed)
        if match is None:
            lines.append(
                "the sign is standing but the world did not name it; "
                "use look to find its id and write_sign to write on it"
            )
            return "\n".join(lines)
        sign_id = match.group(1)
        wrote = await _submit_sign_text(ctx, sign_id, text)
        lines.append(wrote)
        if not action_succeeded(wrote):
            lines.append(f"{sign_id} is standing but still blank; use write_sign")
            return "\n".join(lines)
        lines.append(f'{sign_id} now reads: "{text}"')
        return "\n".join(lines)

    @tools.tool
    async def write_sign(ctx: RunContext[PlannerDeps], sign_id: str, text: str) -> str:
        """Rewrite a sign on your tile or next to it; empty text blanks it.

        Good for correcting or retiring a sign's short, permanent message; use
        `place_sign` to put up a new one instead. Refuses anything that is not
        a sign; use `write_note` for a message board.

        Anyone may rewrite any sign. A sign holds one line of at most 80
        characters, and every settler who comes within view of it (8 tiles) is
        shown the text once, and again when it changes.

        Args:
            sign_id: the id of the sign, as `look` lists it.
            text: the new line, at most 80 characters; empty wipes the sign.
        """
        refusal = _refuse_wrong_note_target(ctx, sign_id, items.SIGN, "write_note")
        if refusal:
            return refusal
        if len(text) > items.SIGN_TEXT_MAX:
            raise ModelRetry(
                f"a sign holds at most {items.SIGN_TEXT_MAX} characters; "
                f"that line is {len(text)}"
            )
        return await _submit_sign_text(ctx, sign_id, text)

    @tools.tool
    async def rest(ctx: RunContext[PlannerDeps], object_id: str) -> str:
        """Rest on a bed you are standing on or next to, to heal a little.

        One settler per bed per tick; if someone beat you to it the world says
        the bed is taken.
        """
        return await ctx.deps.bridge.direct_action(
            pb.Intent(rest=pb.RestIntent(object_id=object_id)), f"rest on {object_id}"
        )

    @tools.tool
    async def dismantle(ctx: RunContext[PlannerDeps], object_id: str) -> str:
        """Take apart one placed building object on your tile or next to it.

        It takes three of these in a row to finish, and returns one item. Use it
        to fix your own mistakes, not to undo other settlers' work without
        saying so on the message board first.
        """
        return await ctx.deps.bridge.direct_action(
            pb.Intent(extract=pb.ExtractIntent(object_id=object_id)),
            f"dismantle {object_id}",
        )

    @tools.tool
    async def wait(ctx: RunContext[PlannerDeps], ticks: int = 1) -> str:
        """Do nothing for a few ticks."""
        return await ctx.deps.bridge.wait_ticks(max(1, ticks))

    @tools.tool
    async def shout(ctx: RunContext[PlannerDeps], text: str) -> str:
        """Shout; every settler within sixty tiles hears it and where it came from.

        Good for emergencies and for calling people to a spot. It is one line
        with no reply possible, so it is not good for working anything out.
        Say where and why: "Wolf at (1540, 970), two of us fighting, come!".
        """
        outcome = await ctx.deps.bridge.direct_action(
            pb.Intent(say=pb.SayIntent(text=text[:200], channel=items.SHOUT_CHANNEL)),
            f"shout {text[:60]!r}",
        )
        return _speak_result(outcome, "shouted", items.SHOUT_RADIUS)

    @tools.tool
    async def write_note(
        ctx: RunContext[PlannerDeps],
        board_id: str,
        slot: int,
        title: str,
        text: str,
    ) -> str:
        """Write one of a message board's twenty note slots (0-19).

        Good for announcements and standing information many should see over
        time: plans, who is doing what, where things are. It reaches only
        those who come and read it, unlike `say` or `shout`. Refuses anything
        that is not a message board; use `write_sign` for a sign.

        Args:
            board_id: the board's id, as `look` prints it. You must be on or
                next to it.
            slot: which of its 20 slots (0-19) to write; an existing note in
                that slot is overwritten.
            title: shown in `look`'s listing, at most 60 characters.
            text: the note's body, at most 500 characters.
        """
        refusal = _refuse_wrong_note_target(
            ctx, board_id, items.MESSAGE_BOARD, "write_sign"
        )
        if refusal:
            return refusal
        return await ctx.deps.bridge.direct_action(
            pb.Intent(
                write_note=pb.WriteNoteIntent(
                    object_id=board_id,
                    slot=slot,
                    title=title[:60],
                    text=text[:500],
                )
            ),
            f"write note {slot} on {board_id}",
        )

    @tools.tool
    async def read_board(ctx: RunContext[PlannerDeps], board_id: str) -> str:
        """Read every note on a message board you have seen, and mark them read.

        Good for catching up on announcements and standing information; `look`
        marks a note `(new)` until you read it here.
        """
        model = ctx.deps.bridge.model
        board = model.objects.get(board_id)
        if board is None:
            known = model.boards_known()
            if not known:
                return (
                    f"you have not seen a board called {board_id!r} and know of "
                    "none; place a message_board to put one down"
                )
            listing = ", ".join(f"{b.object_id} at {b.position}" for b in known)
            return (
                f"you have not seen a board called {board_id!r}; you know of: {listing}"
            )
        by_slot = board.notes_by_slot()
        if not by_slot:
            return f"{board_id} is empty"
        lines = []
        for slot, note in sorted(by_slot.items()):
            title = str(note.get("title", ""))
            if not title:
                continue
            lines.append(
                f"[{slot}] {title} (by {note.get('author', '?')}, "
                f"tick {note.get('tick', '?')}): {note.get('text', '')}"
            )
        model.mark_board_read(board_id)
        return "\n".join(lines) or f"{board_id} is empty"


def _register_memory_tools(tools: FunctionToolset[PlannerDeps]) -> None:
    @tools.tool
    async def remember(ctx: RunContext[PlannerDeps], text: str) -> str:
        """Append a line to today's notes in your journal; it lasts past this turn."""
        append_scratch_line(ctx.deps.memory_path, text, ctx.deps.bridge.model.entity_id)
        return "noted"

    @tools.tool
    async def recall(ctx: RunContext[PlannerDeps]) -> str:
        """Read back your journal."""
        return read_memory(ctx.deps.memory_path, ctx.deps.bridge.model.entity_id)


def read_memory(path: Path, entity_id: str = "") -> str:
    """The whole journal, seeded on first read (docs/12_sleep_journal.md)."""
    return read_journal(path, entity_id)


def _tool_args(part: ToolCallPart) -> dict[str, Any]:
    """The tool call's arguments as a dict, even when the model sent bad JSON."""
    try:
        return part.args_as_dict()
    except (ValueError, TypeError) as error:
        return {"_unparsed": part.args_as_json_str(), "_error": str(error)}


def _starts_turn(message: ModelMessage) -> bool:
    """True for the request that opens a planner turn (it carries the prompt)."""
    return isinstance(message, ModelRequest) and any(
        isinstance(part, UserPromptPart) for part in message.parts
    )


def trim_history(
    messages: Sequence[ModelMessage], limit: int = HISTORY_MESSAGE_LIMIT
) -> list[ModelMessage]:
    """Keep the first message plus as many whole recent turns as fit in `limit`.

    The cut always falls on a turn boundary: a tool result whose tool call was
    trimmed away is rejected by most providers. The newest turn is kept whole
    even when it alone is longer than `limit`.
    """
    if len(messages) <= limit:
        return list(messages)
    turn_starts = [
        index
        for index, message in enumerate(messages)
        if index > 0 and _starts_turn(message)
    ]
    if not turn_starts:
        return list(messages)
    fitting = [index for index in turn_starts if len(messages) - index < limit]
    cut = fitting[0] if fitting else turn_starts[-1]
    return list(messages[:1]) + list(messages[cut:])


class Planner:
    """Runs planner turns back to back for as long as the agent is alive."""

    def __init__(
        self,
        bridge: AgentBridge,
        entity_id: str,
        *,
        model_name: str = "",
        trace: AgentTrace,
        ledger: CostLedger = CostLedger(),
        settler_count: int = items.DEFAULT_SETTLER_COUNT,
    ) -> None:
        self.model_name = resolve_model_name(model_name)
        self.settler_count = settler_count
        # The agent always passes its own ledger; the default is a sink for
        # tests and scripts that build a planner on its own.
        self.ledger = ledger
        self.bridge = bridge
        self.entity_id = entity_id
        self.trace = trace
        self.memory_path = trace.memory_path
        self.agent = build_planner_agent(self.model_name, settler_count)
        self.day_log = DayLog()
        self.deps = PlannerDeps(
            bridge=bridge, memory_path=self.memory_path, day_log=self.day_log
        )
        self.history: list[ModelMessage] = []
        # "woke" or "respawned" when the body has been through one of those
        # since the current turn started; both drop the history at turn end.
        self._history_reset_reason = ""
        self.reports: list[StintReport] = []
        self.last_thought = ""
        # The journal as the last prompt saw it, traced with `turn_start`.
        self.journal_sections: dict[str, str] = {}
        self.turn = 0
        self._tool_calls_this_turn = 0
        self._turns_without_tools = 0

    def note_report(self, report: StintReport) -> None:
        """Remember a finished stint so the next turn's prompt can mention it."""
        self.reports.append(report)
        del self.reports[:-STINT_REPORTS_KEPT]

    async def build_prompt(self) -> str:
        """The user message for the next planner turn.

        It waits first for a journal rewrite that is still running, so a turn
        that follows a sleep reads the journal that sleep produced.
        """
        await self.bridge.await_journal()
        model = self.bridge.model
        parts = [f"Your name is {self.entity_id}."]
        # A wolf on top of the actor goes first: the look below is long.
        alert = threat_alert(model, alert_window_start(model.tick, 0))
        if alert:
            parts.append(alert)
        parts.append(describe_world(model))
        if self.reports:
            parts.append("Most recent stint:\n" + self.reports[-1].to_text())
        parts.extend(self.bridge.drain_notes(for_prompt=True))
        parts.append(self.bridge.reflex.prompt_line())
        # Read once: the prompt gets the rendered journal, the trace its
        # sections, and the file is not worth two reads.
        journal = Journal.load(self.memory_path, self.entity_id)
        self.journal_sections = journal.all_sections()
        parts.append("Your journal:\n" + journal.render().strip())
        parts.append(
            "Decide what to do next. Use start_stint for anything that takes "
            "more than one tick. Actions only happen through tool calls; text "
            "that merely describes a call does nothing. Finish with one short "
            "paragraph of reflection."
        )
        return "\n\n".join(parts)

    async def run(self) -> None:
        """Take planner turns forever; never let one bad turn stop the agent."""
        while True:
            try:
                await self.take_turn()
            except asyncio.CancelledError:
                raise
            except Exception as error:  # noqa: BLE001 - the planner must keep going
                logger.warning("planner_turn_failed", error=str(error))
                self._trace("turn_failed", error=str(error))
                await asyncio.sleep(TURN_RETRY_SECONDS)

    def _trace(self, event: str, **fields: object) -> None:
        """Write one line to `planner.jsonl.gz`, stamped with the current tick."""
        payload: dict[str, object] = {
            "event": event,
            "entity_id": self.entity_id,
            "tick": self.bridge.model.tick,
            "turn": self.turn,
        }
        payload.update(fields)
        self.trace.planner.write(payload)

    async def _log_events(self, events: AsyncIterable[AgentStreamEvent]) -> None:
        """Log every tool call and result so a live run can be followed."""
        async for event in events:
            if isinstance(event, FunctionToolCallEvent):
                self._tool_calls_this_turn += 1
                logger.info(
                    "planner_tool_call",
                    entity_id=self.entity_id,
                    tool=event.part.tool_name,
                    args=event.part.args_as_json_str()[:300],
                )
                self._trace(
                    "tool_call",
                    tool=event.part.tool_name,
                    args=_tool_args(event.part),
                )
            elif isinstance(event, FunctionToolResultEvent):
                tool_name = event.part.tool_name or ""
                logger.info(
                    "planner_tool_result",
                    entity_id=self.entity_id,
                    tool=tool_name,
                    result=str(event.part.content)[:200],
                )
                self._trace(
                    "tool_result", tool=tool_name, result=str(event.part.content)
                )

    async def take_turn(self) -> str:
        """Run one planner turn and publish its reflection."""
        # No turn while the body is asleep, collapsed or dead: the wait ends on
        # the tick it is awake and alive, and the wake or respawn it lived
        # through is what resets the history below.
        await self.bridge.await_active()
        logger.info("planner_turn_started", entity_id=self.entity_id)
        self.turn += 1
        # A wake or respawn that landed between turns is stale before this one
        # starts; one inside the turn is handled at its end.
        self._reset_history_after_a_new_life()
        self._tool_calls_this_turn = 0
        self.deps.budget.reset(MAX_TOOL_CALLS_PER_TURN, self.bridge.model.tick)
        prompt = await self.build_prompt()
        self._trace("turn_start", prompt=prompt, journal=self.journal_sections)
        started = time.monotonic()
        hard_limit = MAX_TOOL_CALLS_PER_TURN + HARD_LIMIT_MARGIN
        with capture_run_messages() as run_messages:
            try:
                result = await self.agent.run(
                    prompt,
                    deps=self.deps,
                    message_history=self.history,
                    usage_limits=UsageLimits(tool_calls_limit=hard_limit),
                    event_stream_handler=lambda _ctx, events: self._log_events(events),
                )
            except UsageLimitExceeded:
                text = self._end_turn_at_hard_limit(run_messages)
                self._reset_history_after_a_new_life()
                return text
        if self.deps.budget.left == 0:
            self._trace("tool_budget_spent", tool_calls=self._tool_calls_this_turn)
        self.history = trim_history(result.all_messages())
        self.last_thought = result.output.strip()
        if self.last_thought:
            self.bridge.set_thought(self.last_thought)
        logger.info("planner_thought", entity_id=self.entity_id, text=self.last_thought)
        usage = usage_from_messages(result.new_messages())
        self.ledger.add_planner(usage)
        self._trace(
            "turn_end",
            thought=self.last_thought,
            tool_calls=self._tool_calls_this_turn,
            duration_ms=int((time.monotonic() - started) * 1000),
            usage=usage,
        )
        if self.last_thought:
            self.day_log.add(self.bridge.model.tick, KIND_REFLECTION, self.last_thought)
        self._reset_history_after_a_new_life()
        await self._recover_from_text_only_turn()
        return self.last_thought

    def _end_turn_at_hard_limit(self, run_messages: Sequence[ModelMessage]) -> str:
        """The model kept calling tools after the budget refusals: stop the turn.

        What it did still happened in the world, so the messages are kept;
        pydantic-ai repairs the unanswered tool calls at the end of the history
        on the next run.
        """
        logger.info("planner_tool_budget_reached", entity_id=self.entity_id)
        # The turn is ending before `self.history` is replaced, so its current
        # length is still what went into the run: everything past it is what
        # this turn added, and what this turn's requests cost.
        added = run_messages[len(self.history) :]
        usage = usage_from_messages(added)
        self.ledger.add_planner(usage)
        self._trace(
            "tool_budget_reached",
            tool_calls=self._tool_calls_this_turn,
            usage=usage,
        )
        self.history = trim_history(run_messages)
        self.last_thought = "Ran out of tool calls this turn; continuing."
        self.bridge.set_thought(self.last_thought)
        return self.last_thought

    def note_life_event(self, reason: str) -> None:
        """Record that the body woke or respawned; the turn's history is stale.

        Args:
            reason: "woke" or "respawned".
        """
        self._history_reset_reason = reason

    def end_turn_now(self) -> None:
        """Spend the tool budget, so the model writes its reflection and stops."""
        self.deps.budget.spend()

    def _reset_history_after_a_new_life(self) -> None:
        """Drop the history when the turn just lived through a sleep or a death.

        The journal has been rewritten from this turn's day log, so keeping the
        messages would show the model both, and the older one at greater
        length (docs/12_sleep_journal.md).
        """
        reason = self._history_reset_reason
        if not reason:
            return
        self._history_reset_reason = ""
        self.history = []
        logger.info("planner_history_reset", entity_id=self.entity_id, reason=reason)
        self._trace("history_reset", reason=reason)

    async def _recover_from_text_only_turn(self) -> None:
        """Break the loop where the model narrates tool calls instead of making them.

        A turn with no tool calls does nothing in the world. Small models
        sometimes drift into writing `start_stint(...)` as prose; once that is
        in the history they repeat it forever. After two such turns the
        history is dropped so the next turn starts clean.
        """
        if self._tool_calls_this_turn > 0:
            self._turns_without_tools = 0
            return
        self._turns_without_tools += 1
        logger.warning(
            "planner_turn_without_tools",
            entity_id=self.entity_id,
            streak=self._turns_without_tools,
        )
        if self._turns_without_tools >= 2:
            logger.warning("planner_history_reset", entity_id=self.entity_id)
            self._trace("history_reset", reason="text_only")
            self.history = []
            self._turns_without_tools = 0
        await asyncio.sleep(TURN_RETRY_SECONDS)
