"""The slow half of the actor: a pydantic-ai agent that decides what to do next.

The planner never touches the tick loop. It looks at the world model, writes to
its memory file, and mostly hands control to Jev through `start_stint`, which
only returns once the stint has ended. Single-tick tools exist for the fiddly
moments (craft this, place that) where a whole stint would be overkill.
"""

from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, Sequence

import structlog
from pydantic_ai import Agent, ModelRetry, RunContext
from pydantic_ai.messages import ModelMessage
from pydantic_ai.usage import UsageLimits
from pydantic_ai.exceptions import UsageLimitExceeded

from .. import world_pb2 as pb
from .geometry import NAME_TO_DIRECTION, chebyshev
from .options import CRAFT_RECIPES, TravelState
from .stint import Brief, StintReport
from .worldmodel import WorldModel

logger = structlog.get_logger(__name__)

DEFAULT_PLANNER_MODEL = "z-ai/glm-5.3-flash"
MAX_TOOL_CALLS_PER_TURN = 12
HISTORY_MESSAGE_LIMIT = 20
STINT_REPORTS_KEPT = 10
TURN_RETRY_SECONDS = 5.0

SETTLEMENT_NARRATIVE = """\
You are one of twelve settlers who woke up together on a large island. Your
shared goal is to build a settlement at the spawn site: gather wood and stone,
craft tools, craft and place a chest and a message board, keep everyone fed, and
defend each other against the wolves that roam the island. Use the message board
to coordinate with the other settlers - they are real agents like you, and they
read what you write.

How the world works:
- One tick per turn of the world. Hunger drops 1 per tick; at hunger 0 you lose
  health. Eating a berry restores 20 hunger. Health regenerates slowly while
  hunger is above 50.
- Chop trees for wood and mine rocks for stone; wielding an axe or pickaxe makes
  that three times faster. Recipes: axe = 2 wood + 1 stone, pickaxe = 2 wood +
  2 stone, sword = 1 wood + 3 stone, chest = 6 wood, message board = 4 wood +
  1 stone.
- Wolves hit for 3. A sword adds +3 to your own damage. Below 8 health, run.
- Dying drops your whole inventory where you fell and costs you 10 ticks.

How you work:
- You are slow and expensive. Every turn you take costs real money and real
  seconds, and the world keeps ticking while you think.
- Jev is fast, cheap, and extremely literal. It picks one action per tick from a
  closed list that code builds for it. It does not plan, it does not remember,
  and it will do exactly what your brief says even when that is silly.
- So: do the thinking here, then hand Jev a brief with a concrete instruction, a
  success condition it can recognise from what it can see, and a tick budget.
  `start_stint` is your main tool and it blocks until the stint ends.
- Use the single-tick tools only for one-off precision actions.
- Write what you learn to memory with `remember` - it is the only thing that
  survives across turns.
- End every turn with one short paragraph saying what you just did and what you
  intend next. That paragraph is shown to the humans watching.
"""


class AgentBridge(Protocol):
    """What the planner needs from the tick loop."""

    @property
    def model(self) -> WorldModel:
        """The shared world model, updated every tick."""

    async def run_stint(self, brief: Brief) -> StintReport:
        """Hand control to Jev and return only once the stint has ended."""

    async def direct_action(self, intent: pb.Intent, description: str) -> str:
        """Submit one intent on the next tick and report what happened."""

    async def wait_ticks(self, ticks: int) -> str:
        """Do nothing for `ticks` ticks."""

    def set_thought(self, thought: str) -> None:
        """Publish the planner's latest reflection to the viewer."""


@dataclass
class PlannerDeps:
    """Dependencies handed to every tool call."""

    bridge: AgentBridge
    memory_path: Path


def _direction_value(name: str) -> pb.Direction:
    key = name.strip().upper()
    if key not in NAME_TO_DIRECTION:
        raise ModelRetry(
            f"unknown direction {name!r}; use one of {sorted(NAME_TO_DIRECTION)}"
        )
    return NAME_TO_DIRECTION[key]


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
            nearest = model.objects_by_type([object_type])[:3]
            examples = ", ".join(
                f"{o.object_id} at {o.position} "
                f"(d{chebyshev(o.position, info.position)})"
                for o in nearest
            )
            lines.append(
                f"  {object_type}: {len(grouped[object_type])} known; nearest {examples}"
            )
    else:
        lines.append("known objects: none yet")

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
            lines.append(
                f"  {entity.entity_id} ({entity.entity_type}) at {entity.position}, "
                f"hp {entity.health}/{entity.max_health}"
            )
    else:
        lines.append("entities in view: none")

    heard = model.recent_utterances(5)
    if heard:
        lines.append("recently heard:")
        lines.extend(f'  {u.speaker_id}: "{u.text}"' for u in heard)

    recent = model.recent_history(8)
    if recent:
        lines.append("your recent ticks:")
        lines.extend(f"  {line}" for line in recent)

    return "\n".join(lines)


def build_planner_agent(model_name: str) -> Agent[PlannerDeps, str]:
    """Create the pydantic-ai agent with every planner tool registered."""
    agent: Agent[PlannerDeps, str] = Agent(
        model_name,
        deps_type=PlannerDeps,
        output_type=str,
        system_prompt=SETTLEMENT_NARRATIVE,
        retries=2,
    )

    @agent.tool
    async def look(ctx: RunContext[PlannerDeps]) -> str:
        """Look around: your stats, inventory, known objects, and recent events."""
        return describe_world(ctx.deps.bridge.model)

    @agent.tool
    async def start_stint(
        ctx: RunContext[PlannerDeps],
        instruction: str,
        success_condition: str,
        max_ticks: int,
        notes: str = "",
        check_every: int = 1,
    ) -> str:
        """Hand control to Jev until the brief is done, then read the report.

        Args:
            instruction: what Jev should do, concretely, in one or two sentences.
            success_condition: what Jev should be able to see when it is done.
            max_ticks: hard tick budget; the stint ends when it runs out.
            notes: extra hints, for example "wolves are dangerous below 8 health".
            check_every: ask Jev every Nth tick and repeat the last action between.
        """
        brief = Brief(
            instruction=instruction,
            success_condition=success_condition,
            max_ticks=max(1, max_ticks),
            notes=notes,
            check_every=max(1, check_every),
        )
        report = await ctx.deps.bridge.run_stint(brief)
        return report.to_text()

    @agent.tool
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

    _register_single_tick_tools(agent)
    _register_memory_tools(agent)
    return agent


def _register_single_tick_tools(agent: Agent[PlannerDeps, str]) -> None:
    @agent.tool
    async def move(ctx: RunContext[PlannerDeps], direction: str) -> str:
        """Step one tile. Direction is N, NE, E, SE, S, SW, W, or NW."""
        value = _direction_value(direction)
        return await ctx.deps.bridge.direct_action(
            pb.Intent(move=pb.MoveIntent(direction=value)), f"move {direction}"
        )

    @agent.tool
    async def attack(ctx: RunContext[PlannerDeps], entity_id: str) -> str:
        """Attack an adjacent entity."""
        return await ctx.deps.bridge.direct_action(
            pb.Intent(attack=pb.AttackIntent(target_entity_id=entity_id)),
            f"attack {entity_id}",
        )

    @agent.tool
    async def extract(ctx: RunContext[PlannerDeps], object_id: str) -> str:
        """Chop a tree or mine a rock on your tile or next to it."""
        return await ctx.deps.bridge.direct_action(
            pb.Intent(extract=pb.ExtractIntent(object_id=object_id)),
            f"extract {object_id}",
        )

    @agent.tool
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

    @agent.tool
    async def eat(ctx: RunContext[PlannerDeps], kind: str = "berry") -> str:
        """Eat something from your pack to restore hunger."""
        return await ctx.deps.bridge.direct_action(
            pb.Intent(eat=pb.EatIntent(item_type=kind, amount=1)), f"eat {kind}"
        )

    @agent.tool
    async def pickup(ctx: RunContext[PlannerDeps], kind: str, amount: int = 1) -> str:
        """Take items from the pile on your tile."""
        return await ctx.deps.bridge.direct_action(
            pb.Intent(pickup=pb.PickupIntent(kind=kind, amount=amount)),
            f"pickup {amount} {kind}",
        )

    @agent.tool
    async def drop(ctx: RunContext[PlannerDeps], kind: str, amount: int = 1) -> str:
        """Drop items onto your tile as a pile."""
        return await ctx.deps.bridge.direct_action(
            pb.Intent(drop=pb.DropIntent(kind=kind, amount=amount)),
            f"drop {amount} {kind}",
        )

    @agent.tool
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

    @agent.tool
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

    @agent.tool
    async def craft(ctx: RunContext[PlannerDeps], recipe: str) -> str:
        """Craft one of: axe, pickaxe, sword, chest, message_board."""
        if recipe not in CRAFT_RECIPES:
            return f"no such recipe {recipe!r}; known: {sorted(CRAFT_RECIPES)}"
        return await ctx.deps.bridge.direct_action(
            pb.Intent(craft=pb.CraftIntent(recipe=recipe)), f"craft {recipe}"
        )

    @agent.tool
    async def equip(ctx: RunContext[PlannerDeps], kind: str = "") -> str:
        """Wield an item from your pack, or pass an empty string to unequip."""
        return await ctx.deps.bridge.direct_action(
            pb.Intent(equip=pb.EquipIntent(kind=kind)), f"equip {kind or '(nothing)'}"
        )

    @agent.tool
    async def place(ctx: RunContext[PlannerDeps], kind: str, direction: str) -> str:
        """Put a chest or message board down on the adjacent tile in a direction."""
        value = _direction_value(direction)
        return await ctx.deps.bridge.direct_action(
            pb.Intent(place=pb.PlaceIntent(kind=kind, direction=value)),
            f"place {kind} {direction}",
        )

    @agent.tool
    async def wait(ctx: RunContext[PlannerDeps], ticks: int = 1) -> str:
        """Do nothing for a few ticks."""
        return await ctx.deps.bridge.wait_ticks(max(1, ticks))

    @agent.tool
    async def say(ctx: RunContext[PlannerDeps], text: str) -> str:
        """Speak out loud; every settler within ten tiles hears you."""
        return await ctx.deps.bridge.direct_action(
            pb.Intent(say=pb.SayIntent(text=text[:200], channel="local")),
            f"say {text[:60]!r}",
        )

    @agent.tool
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

    @agent.tool
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


def _register_memory_tools(agent: Agent[PlannerDeps, str]) -> None:
    @agent.tool
    async def remember(ctx: RunContext[PlannerDeps], text: str) -> str:
        """Append a line to your persistent notes; it survives across turns."""
        path = ctx.deps.memory_path
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(f"- {text.strip()}\n")
        return "noted"

    @agent.tool
    async def recall(ctx: RunContext[PlannerDeps]) -> str:
        """Read back your persistent notes."""
        return read_memory(ctx.deps.memory_path)


def read_memory(path: Path) -> str:
    """The contents of the persistent notes file, or a placeholder."""
    if not path.exists():
        return "(no notes yet)"
    return path.read_text(encoding="utf-8").strip() or "(no notes yet)"


def trim_history(
    messages: Sequence[ModelMessage], limit: int = HISTORY_MESSAGE_LIMIT
) -> list[ModelMessage]:
    """Keep the system message plus the most recent `limit` messages."""
    if len(messages) <= limit:
        return list(messages)
    return list(messages[:1]) + list(messages[-(limit - 1) :])


class Planner:
    """Runs planner turns back to back for as long as the agent is alive."""

    def __init__(
        self,
        bridge: AgentBridge,
        entity_id: str,
        *,
        model_name: str = "",
        log_root: Path | None = None,
    ) -> None:
        resolved = model_name or os.environ.get("PLANNER_MODEL", DEFAULT_PLANNER_MODEL)
        # Bare OpenRouter ids look like "vendor/model"; anything else (such as
        # "test" or an explicit "provider:model") is passed through untouched.
        if ":" not in resolved and "/" in resolved:
            resolved = f"openrouter:{resolved}"
        self.model_name = resolved
        self.bridge = bridge
        self.entity_id = entity_id
        root = Path("logs") if log_root is None else log_root
        self.memory_path = root / f"agent-{entity_id}" / "memory.md"
        self.agent = build_planner_agent(self.model_name)
        self.deps = PlannerDeps(bridge=bridge, memory_path=self.memory_path)
        self.history: list[ModelMessage] = []
        self.reports: list[StintReport] = []
        self.last_thought = ""

    def note_report(self, report: StintReport) -> None:
        """Remember a finished stint so the next turn's prompt can mention it."""
        self.reports.append(report)
        del self.reports[:-STINT_REPORTS_KEPT]

    def build_prompt(self) -> str:
        """The user message for the next planner turn."""
        parts = [describe_world(self.bridge.model)]
        if self.reports:
            parts.append("Most recent stint:\n" + self.reports[-1].to_text())
        parts.append("Your notes:\n" + read_memory(self.memory_path))
        parts.append(
            "Decide what to do next. Use start_stint for anything that takes "
            "more than one tick. Finish with one short paragraph of reflection."
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
                await asyncio.sleep(TURN_RETRY_SECONDS)

    async def take_turn(self) -> str:
        """Run one planner turn and publish its reflection."""
        try:
            result = await self.agent.run(
                self.build_prompt(),
                deps=self.deps,
                message_history=self.history,
                usage_limits=UsageLimits(tool_calls_limit=MAX_TOOL_CALLS_PER_TURN),
            )
        except UsageLimitExceeded:
            # Hitting the per-turn tool budget is normal, not an error: the turn
            # simply ends here and the next one starts with a fresh look().
            logger.info("planner_tool_budget_reached", entity_id=self.entity_id)
            self.last_thought = "Ran out of tool calls this turn; continuing."
            self.bridge.set_thought(self.last_thought)
            return self.last_thought
        self.history = trim_history(result.all_messages())
        self.last_thought = result.output.strip()
        if self.last_thought:
            self.bridge.set_thought(self.last_thought)
        logger.info("planner_thought", entity_id=self.entity_id, text=self.last_thought)
        return self.last_thought
