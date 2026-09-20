"""The two written channels: signs, and message-board notes."""

from __future__ import annotations

from pydantic_ai import ModelRetry, RunContext
from pydantic_ai.toolsets import FunctionToolset

from .... import world_pb2 as pb
from ... import items
from ...actions import place_failure_lines, write_note_attempt, write_sign_attempt
from ...outcomes import ActionOutcome
from ...recipes import craft_once
from ..toolset import PlannerDeps
from ..validation import direction_value
from .common import run_attempt, attempt_text


async def submit_sign_text(
    ctx: RunContext[PlannerDeps], sign_id: str, text: str
) -> ActionOutcome:
    """Write `text` on `sign_id` with the world's one-slot note intent."""
    return await run_attempt(
        ctx, write_sign_attempt(ctx.deps.bridge.model, sign_id, text)
    )


def register_signs(tools: FunctionToolset[PlannerDeps]) -> None:
    """place_sign and write_sign: the one pushed line tied to a place."""

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
            needed = dict(recipe.inputs).get(items.WOOD, 0)
            have = bridge.model.self_info.inventory.get(items.WOOD, 0)
            if have < needed:
                return (
                    f"a sign takes {needed} wood; you carry {have}, and you "
                    f"have no sign to place"
                )
            lines.append(await craft_once(ctx.deps.bridge, items.SIGN, recipe))
            if bridge.model.self_info.inventory.get(items.SIGN, 0) <= 0:
                return "\n".join(lines)
        value = direction_value(direction)
        placed = await bridge.direct_action(
            pb.Intent(place=pb.PlaceIntent(kind=items.SIGN, direction=value)),
            f"place sign {direction}",
        )
        lines.append(placed.text())
        if not placed.ok:
            lines.append(place_failure_lines(bridge.model, items.SIGN, value))
            return "\n".join(lines)
        sign_id = placed.placed_object_id
        if not sign_id:
            lines.append(
                "the sign is standing but the world did not name it; "
                "use look to find its id and write_sign to write on it"
            )
            return "\n".join(lines)
        wrote = await submit_sign_text(ctx, sign_id, text)
        lines.append(wrote.text())
        if not wrote.ok:
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
        if len(text) > items.SIGN_TEXT_MAX:
            raise ModelRetry(
                f"a sign holds at most {items.SIGN_TEXT_MAX} characters; "
                f"that line is {len(text)}"
            )
        attempt = write_sign_attempt(ctx.deps.bridge.model, sign_id, text)
        if not attempt.allowed:
            return attempt.refusal
        return (await run_attempt(ctx, attempt)).text()


def register_boards(tools: FunctionToolset[PlannerDeps]) -> None:
    """write_note and read_board: the twenty standing note slots."""

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
        return await attempt_text(
            ctx,
            write_note_attempt(ctx.deps.bridge.model, board_id, slot, title, text),
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
