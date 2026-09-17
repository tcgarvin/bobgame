"""The slow half of the actor: a pydantic-ai agent that decides what to do next.

The planner never touches the tick loop. It looks at the world model, writes to
its memory file, and mostly hands control to Jev through `start_stint`, which
only returns once the stint has ended. Single-tick tools exist for the fiddly
moments (craft this, place that) where a whole stint would be overkill.
"""

from __future__ import annotations

import asyncio
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, AsyncIterable, Protocol, Sequence

import structlog
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
from pydantic_ai.models.openrouter import OpenRouterModelSettings
from pydantic_ai.usage import UsageLimits
from pydantic_ai.exceptions import UsageLimitExceeded
from pydantic_ai.toolsets import FunctionToolset, ToolsetTool, WrapperToolset

from .. import world_pb2 as pb
from . import items
from .build import (
    BuildExecutor,
    BuildPlanError,
    SHAPES,
    build_brief_text,
    make_plan,
)
from .geometry import NAME_TO_DIRECTION, NO_DIRECTION, Coord, chebyshev
from .options import (
    CRAFT_RECIPES,
    MAX_BRIEF_SHOUTS,
    MAX_SHOUT_LENGTH,
    WOLF_ALERT_RADIUS,
    TravelState,
)
from .stint import Brief, StintDriver, StintReport
from .tracelog import AgentTrace
from .worldmodel import HeardUtterance, WorldModel

logger = structlog.get_logger(__name__)

DEFAULT_PLANNER_MODEL = "qwen/qwen3.7-flash"
# The per-turn tool budget is soft: every tool result tells the model how many
# calls are left, and a call past the budget is refused with a message instead
# of being run. The turn then ends normally, with its reflection and history
# intact. (The old hard limit of 12 ended 70% of turns by exception, and every
# one of those turns was forgotten.)
MAX_TOOL_CALLS_PER_TURN = 30
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


def planner_model_settings(model_name: str) -> OpenRouterModelSettings:
    """Per-model settings: reasoning off where allowed, low effort otherwise.

    Reasoning traces multiply planner latency; the planner gets its thinking
    from tool calls and the reflection paragraph instead. Some endpoints
    (GLM 5.x, MiniMax) refuse to disable reasoning, so they get low effort.
    """
    mandatory_reasoning = ("glm-5", "minimax")
    if any(marker in model_name for marker in mandatory_reasoning):
        reasoning: dict[str, object] = {"effort": "low"}
    else:
        reasoning = {"enabled": False}
    return OpenRouterModelSettings(
        openrouter_reasoning=reasoning,  # type: ignore[typeddict-item]
        temperature=0.7,
        timeout=90.0,
    )


SETTLEMENT_NARRATIVE = f"""\
You are one of twelve people who woke up together on a large, wild island with
nothing but your hands. The others are real agents like you; they hear what you
say and read what you write. Together, build a civilization.

What follows is how this world works and how you act in it. What to do with it
is up to you and the others.

Bodies:
- The world advances in ticks. Hunger drops 1 every 4 ticks; at hunger 0 you
  lose health. Eating a berry restores 20 hunger. Health regenerates 1 per 5
  ticks while hunger is above 50. You have {items.PLAYER_MAX_HEALTH} health.
- Dying drops your whole inventory where you fell and costs you 10 ticks.
- Resting on a bed heals {items.REST_HEAL} health per rest.

Wolves and fighting:
- Wolves roam the island and keep coming for the whole game, a few at a time.
  A wolf hunts whoever is nearest, has {items.WOLF_HEALTH} health, moves as fast as you do and
  bites an adjacent settler for {items.WOLF_DAMAGE} every tick.
- You hit for {items.UNARMED_DAMAGE} unarmed. A wielded sword adds +{items.WIELD_DAMAGE_BONUS[items.SWORD]}, an axe +{items.WIELD_DAMAGE_BONUS[items.AXE]}, a pickaxe +{items.WIELD_DAMAGE_BONUS[items.PICKAXE]}.
  A weapon only counts while it is equipped.
- All damage in a tick lands at once: everyone attacking the same wolf hits it
  on the same tick, and it bites back on that tick too.

Voices and writing:
- `say` reaches settlers within {items.SAY_RADIUS} tiles. `shout` reaches {items.SHOUT_RADIUS} tiles, and hearers
  are told where it came from.
- A message board holds twenty notes that anyone standing near it can read and
  overwrite.
- `look` lists every settler you have met by name and where you last saw them.

Materials:
- wood comes from a tree (4 units), faster with an axe.
- stone comes from rocks and boulders (1 to 6 units), faster with a pickaxe.
- fiber comes from reeds (3 units) at the water's edge; no tool helps.
- clay comes from a clay deposit (6 units) on river banks and lake shores,
  faster with a pickaxe.

Recipes ([workshop table] ones only work while you stand on or next to a
placed workshop table):
{items.recipe_table_text()}

Placed objects:
- Every crafted building item is placed as an object. Roads and floors are
  ground pieces: you place them on the tile you are standing on. Walls, doors,
  beds, chairs, tables, chests, boards and workshop tables are structures: you
  stand next to the tile and place toward it.
- Walls block everyone. A door blocks wolves but lets settlers through.
- Anything built can be dismantled: {items.DISMANTLE_WORK} extract actions
  return one item to the dismantler.

How you act:
- You are the slow, thinking half of one settler. The world ticks every two
  seconds whether or not you have answered. Each model reply and each
  single-tick tool call costs you one to several ticks, during which your body
  just stands there. Every tool result tells you the current tick and how many
  ticks this turn has cost, and flags a wolf that is near or biting with "!!".
- Jev is the fast half: a cheap reflex layer that moves your own body every
  tick while you are not thinking. It is not a settler and not your name; your
  name is on the first line of every turn. Jev is extremely literal. It picks
  one action per tick from a closed list that code builds for it: move, walk
  to something it can see or to where a shout came from, attack, extract,
  collect, eat, craft, equip, place, rest, use a chest, say a canned phrase,
  shout a phrase you gave it, wait. It does not plan, it does not remember,
  and it does exactly what your brief says even when that is silly.
- Anything that has to happen at world speed, a fight included, only happens
  if Jev is doing it. `start_stint` hands your body to Jev and blocks until
  the stint ends. A brief is a concrete instruction, a success condition Jev
  can recognise from what it sees, a tick budget, optional notes, and the
  exact phrases Jev may shout (it cannot invent its own). Two briefs of very
  different shape, to show the form only; the content is yours:
    instruction: "Mine the rocks north-east of you and pick up the stone. Each
      time you are carrying 6 stone, shout the stone phrase once."
    success_condition: "you are carrying 10 stone"
    max_ticks: 120
    notes: "eat a berry when hunger is below 40"
    shouts: ["Stone to spare at the rocks, come and take some."]
  and
    instruction: "Withdraw every plank from the chest next to you, then craft
      wood_wall until you have no planks left."
    success_condition: "you carry no planks and at least 1 wood_wall"
    max_ticks: 25
    shouts: []
  A success condition names one thing Jev can see in its own state. A stint
  that runs out of ticks hands your body back with the job half done.
- `travel_to` walks you to a map position and `build` places a whole line or
  rectangle of pieces; each is one call however many ticks it runs. The
  single-tick tools are for one-off precision actions.
- You get {MAX_TOOL_CALLS_PER_TURN} tool calls per turn, and every tool result ends with how many
  are left. A call made after the budget is spent is refused, not run; write
  your reflection then, and the next turn starts with a full budget.
- `remember` writes to your notes, the only thing of yours that survives
  across turns.
- End every turn with one short paragraph saying what you just did and what you
  intend next. That paragraph is shown to the humans watching.
"""


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
    """How much world time the current planner turn has already cost."""
    elapsed = max(0, model.tick - turn_start_tick)
    return f"[tick {model.tick}; this turn has cost {elapsed} ticks so far]"


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


@dataclass
class BudgetedToolset(WrapperToolset[PlannerDeps]):
    """Counts tool calls into `deps.budget` and tells the model what is left.

    A call past the budget is refused with a message rather than an exception,
    so the turn ends with its reflection and its history instead of vanishing.
    """

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
        result = await self.wrapped.call_tool(name, tool_args, ctx, tool)
        model = ctx.deps.bridge.model
        lines = [str(result), turn_clock_line(model, budget.turn_start_tick)]
        since_tick = alert_window_start(model.tick, budget.turn_start_tick)
        alert = threat_alert(model, since_tick)
        if alert:
            lines.append(alert)
        lines.append(budget.footer())
        return "\n".join(lines)


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
    items.BED,
    items.DOOR,
    items.REEDS,
    items.CLAY_DEPOSIT,
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
        state = "" if entity.alive else ", dead"
        lines.append(
            f"  {entity.entity_id} at {entity.position} "
            f"(d{chebyshev(entity.position, model.position)}, {when}{state})"
        )
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


def describe_world(model: WorldModel) -> str:
    """The `look()` summary: everything the actor knows, in a readable block."""
    info = model.self_info
    dx, dy = model.settlement_offset()
    inventory = (
        ", ".join(f"{kind} x{count}" for kind, count in sorted(info.inventory.items()))
        or "empty"
    )
    lines = [
        f"tick {model.tick}, you are {model.entity_id} at {info.position}",
        f"health {info.health}/{info.max_health}, hunger {info.hunger}/{info.max_hunger}"
        f", wielded: {info.wielded or 'nothing'}, alive: {info.alive}",
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

    for board in model.objects_by_type(["message_board"])[:2]:
        lines.append(f"message board {board.object_id} at {board.position}:")
        notes = board.notes()
        if not notes:
            lines.append("  (no notes yet)")
        for index, note in enumerate(notes):
            title = str(note.get("title", ""))
            author = str(note.get("author", "?"))
            if title:
                lines.append(f"  [{index}] {title} - {author}")

    visible = model.entities_near(8)
    if visible:
        lines.append("entities in view:")
        for entity in visible:
            wielding = f", wielding {entity.wielded}" if entity.wielded else ""
            lines.append(
                f"  {entity.entity_id} ({entity.entity_type}) at {entity.position}, "
                f"hp {entity.health}/{entity.max_health}{wielding}"
            )
    else:
        lines.append("entities in view: none")

    lines.extend(_roster_lines(model))

    heard = model.recent_utterances(5)
    if heard:
        lines.append("recently heard:")
        lines.extend(f"  {_heard_line(u, model.tick)}" for u in heard)

    recent = model.recent_history(8)
    if recent:
        lines.append("your recent ticks:")
        lines.extend(f"  {line}" for line in recent)

    return "\n".join(lines)


def build_planner_agent(model_name: str) -> Agent[PlannerDeps, str]:
    """Create the pydantic-ai agent with every planner tool registered."""
    tools: FunctionToolset[PlannerDeps] = FunctionToolset()
    agent: Agent[PlannerDeps, str] = Agent(
        model_name,
        deps_type=PlannerDeps,
        output_type=str,
        system_prompt=SETTLEMENT_NARRATIVE,
        retries=2,
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
    ) -> str:
        """Hand control to Jev until the brief is done, then read the report.

        Args:
            instruction: what Jev should do, concretely, in one or two sentences.
            success_condition: what Jev should be able to see when it is done.
            max_ticks: hard tick budget; the stint ends when it runs out.
            notes: extra hints, for example "eat a berry when hunger is below 40".
            check_every: ask Jev every Nth tick and repeat the last action between.
            shouts: the exact phrases Jev may shout during this stint, for
                example ["Stone to spare at the rocks.", "Come to the workshop."].
                Say in the instruction when to use each. Jev cannot shout anything else;
                leave it empty and Jev stays quiet.
        """
        brief = Brief(
            instruction=instruction,
            success_condition=success_condition,
            max_ticks=max(1, max_ticks),
            notes=notes,
            check_every=max(1, check_every),
            shouts=_validated_shouts(shouts),
        )
        report = await ctx.deps.bridge.run_stint(brief)
        return report.to_text()

    @tools.tool
    async def travel_to(
        ctx: RunContext[PlannerDeps], x: int, y: int, max_ticks: int
    ) -> str:
        """Walk to a map position, reacting to danger on the way."""
        brief = Brief(
            instruction=f"Walk to ({x}, {y}).",
            success_condition=f"you are standing on or next to ({x}, {y})",
            max_ticks=max(1, max_ticks),
            notes="Follow the path; react to danger; eject on arrival.",
            travel=TravelState(target=(x, y), label=f"({x}, {y})"),
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
            build_danger, build_would_seal_you_in or ticks_exhausted.
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
    _register_memory_tools(tools)
    return agent


def _register_single_tick_tools(tools: FunctionToolset[PlannerDeps]) -> None:
    @tools.tool
    async def move(ctx: RunContext[PlannerDeps], direction: str) -> str:
        """Step one tile. Direction is N, NE, E, SE, S, SW, W, or NW."""
        value = _direction_value(direction)
        return await ctx.deps.bridge.direct_action(
            pb.Intent(move=pb.MoveIntent(direction=value)), f"move {direction}"
        )

    @tools.tool
    async def attack(ctx: RunContext[PlannerDeps], entity_id: str) -> str:
        """Attack an adjacent entity."""
        return await ctx.deps.bridge.direct_action(
            pb.Intent(attack=pb.AttackIntent(target_entity_id=entity_id)),
            f"attack {entity_id}",
        )

    @tools.tool
    async def extract(ctx: RunContext[PlannerDeps], object_id: str) -> str:
        """Chop a tree or mine a rock on your tile or next to it."""
        return await ctx.deps.bridge.direct_action(
            pb.Intent(extract=pb.ExtractIntent(object_id=object_id)),
            f"extract {object_id}",
        )

    @tools.tool
    async def collect(ctx: RunContext[PlannerDeps], object_id: str) -> str:
        """Pick the berry off a bush you are standing on."""
        return await ctx.deps.bridge.direct_action(
            pb.Intent(
                collect=pb.CollectIntent(
                    object_id=object_id, item_type="berry", amount=1
                )
            ),
            f"collect {object_id}",
        )

    @tools.tool
    async def eat(ctx: RunContext[PlannerDeps], kind: str = "berry") -> str:
        """Eat something from your pack to restore hunger."""
        return await ctx.deps.bridge.direct_action(
            pb.Intent(eat=pb.EatIntent(item_type=kind, amount=1)), f"eat {kind}"
        )

    @tools.tool
    async def pickup(ctx: RunContext[PlannerDeps], kind: str, amount: int = 1) -> str:
        """Take items from the pile on your tile."""
        return await ctx.deps.bridge.direct_action(
            pb.Intent(pickup=pb.PickupIntent(kind=kind, amount=amount)),
            f"pickup {amount} {kind}",
        )

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
        """Craft one recipe once. Workshop recipes need a workshop table nearby.

        Args:
            recipe: one of the names in the recipe table in your instructions.
        """
        if recipe not in CRAFT_RECIPES:
            return f"no such recipe {recipe!r}; known: {sorted(CRAFT_RECIPES)}"
        return await ctx.deps.bridge.direct_action(
            pb.Intent(craft=pb.CraftIntent(recipe=recipe)), f"craft {recipe}"
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
    async def say(ctx: RunContext[PlannerDeps], text: str) -> str:
        """Speak out loud; every settler within ten tiles hears you."""
        return await ctx.deps.bridge.direct_action(
            pb.Intent(say=pb.SayIntent(text=text[:200], channel="local")),
            f"say {text[:60]!r}",
        )

    @tools.tool
    async def shout(ctx: RunContext[PlannerDeps], text: str) -> str:
        """Shout; every settler within sixty tiles hears it and where it came from.

        For calling help to a wolf fight or gathering people. Say where and why:
        "Wolf at (1540, 970), two of us fighting, come!".
        """
        return await ctx.deps.bridge.direct_action(
            pb.Intent(say=pb.SayIntent(text=text[:200], channel=items.SHOUT_CHANNEL)),
            f"shout {text[:60]!r}",
        )

    @tools.tool
    async def write_note(
        ctx: RunContext[PlannerDeps],
        board_id: str,
        slot: int,
        title: str,
        text: str,
    ) -> str:
        """Write one of a message board's twenty note slots (0-19)."""
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
        """Read every note on a message board you have seen."""
        board = ctx.deps.bridge.model.objects.get(board_id)
        if board is None:
            return f"you have not seen a board called {board_id!r}"
        notes = board.notes()
        if not notes:
            return f"{board_id} is empty"
        lines = []
        for index, note in enumerate(notes):
            title = str(note.get("title", ""))
            if not title:
                continue
            lines.append(
                f"[{index}] {title} (by {note.get('author', '?')}, "
                f"tick {note.get('tick', '?')}): {note.get('text', '')}"
            )
        return "\n".join(lines) or f"{board_id} is empty"


def _register_memory_tools(tools: FunctionToolset[PlannerDeps]) -> None:
    @tools.tool
    async def remember(ctx: RunContext[PlannerDeps], text: str) -> str:
        """Append a line to your persistent notes; it survives across turns."""
        path = ctx.deps.memory_path
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(f"- {text.strip()}\n")
        return "noted"

    @tools.tool
    async def recall(ctx: RunContext[PlannerDeps]) -> str:
        """Read back your persistent notes."""
        return read_memory(ctx.deps.memory_path)


def read_memory(path: Path) -> str:
    """The contents of the persistent notes file, or a placeholder."""
    if not path.exists():
        return "(no notes yet)"
    return path.read_text(encoding="utf-8").strip() or "(no notes yet)"


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
    ) -> None:
        resolved = model_name or os.environ.get("PLANNER_MODEL", DEFAULT_PLANNER_MODEL)
        # Bare OpenRouter ids look like "vendor/model"; anything else (such as
        # "test" or an explicit "provider:model") is passed through untouched.
        if ":" not in resolved and "/" in resolved:
            resolved = f"openrouter:{resolved}"
        self.model_name = resolved
        self.bridge = bridge
        self.entity_id = entity_id
        self.trace = trace
        self.memory_path = trace.memory_path
        self.agent = build_planner_agent(self.model_name)
        self.deps = PlannerDeps(bridge=bridge, memory_path=self.memory_path)
        self.history: list[ModelMessage] = []
        self.reports: list[StintReport] = []
        self.last_thought = ""
        self.turn = 0
        self._tool_calls_this_turn = 0
        self._turns_without_tools = 0

    def note_report(self, report: StintReport) -> None:
        """Remember a finished stint so the next turn's prompt can mention it."""
        self.reports.append(report)
        del self.reports[:-STINT_REPORTS_KEPT]

    def build_prompt(self) -> str:
        """The user message for the next planner turn."""
        model = self.bridge.model
        parts = [f"Your name is {self.entity_id}."]
        # A wolf on top of the actor goes first: the look below is long.
        alert = threat_alert(model, alert_window_start(model.tick, 0))
        if alert:
            parts.append(alert)
        parts.append(describe_world(model))
        if self.reports:
            parts.append("Most recent stint:\n" + self.reports[-1].to_text())
        parts.append("Your notes:\n" + read_memory(self.memory_path))
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
        logger.info("planner_turn_started", entity_id=self.entity_id)
        self.turn += 1
        self._tool_calls_this_turn = 0
        self.deps.budget.reset(MAX_TOOL_CALLS_PER_TURN, self.bridge.model.tick)
        prompt = self.build_prompt()
        self._trace("turn_start", prompt=prompt)
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
                return self._end_turn_at_hard_limit(run_messages)
        if self.deps.budget.left == 0:
            self._trace("tool_budget_spent", tool_calls=self._tool_calls_this_turn)
        self.history = trim_history(result.all_messages())
        self.last_thought = result.output.strip()
        if self.last_thought:
            self.bridge.set_thought(self.last_thought)
        logger.info("planner_thought", entity_id=self.entity_id, text=self.last_thought)
        # `usage` is a property on pydantic-ai 2.4x, not a method.
        usage = result.usage
        self._trace(
            "turn_end",
            thought=self.last_thought,
            tool_calls=self._tool_calls_this_turn,
            duration_ms=int((time.monotonic() - started) * 1000),
            usage={
                "input_tokens": usage.input_tokens,
                "output_tokens": usage.output_tokens,
            },
        )
        await self._recover_from_text_only_turn()
        return self.last_thought

    def _end_turn_at_hard_limit(self, run_messages: Sequence[ModelMessage]) -> str:
        """The model kept calling tools after the budget refusals: stop the turn.

        What it did still happened in the world, so the messages are kept;
        pydantic-ai repairs the unanswered tool calls at the end of the history
        on the next run.
        """
        logger.info("planner_tool_budget_reached", entity_id=self.entity_id)
        self._trace("tool_budget_reached", tool_calls=self._tool_calls_this_turn)
        self.history = trim_history(run_messages)
        self.last_thought = "Ran out of tool calls this turn; continuing."
        self.bridge.set_thought(self.last_thought)
        return self.last_thought

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
            self._trace("history_reset")
            self.history = []
            self._turns_without_tools = 0
        await asyncio.sleep(TURN_RETRY_SECONDS)
