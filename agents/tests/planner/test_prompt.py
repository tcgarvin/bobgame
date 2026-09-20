"""The planner's system prompt: what the narrative must state."""

from __future__ import annotations

from pathlib import Path
from pydantic_ai.models.test import TestModel
from agents.jev_agent import items
from agents.jev_agent.items import RECIPES
from agents.jev_agent.planner import (
    SETTLEMENT_NARRATIVE,
    settlement_narrative,
    Planner,
    PlannerDeps,
    body_alerts,
)
from agents.jev_agent.conversation import CONVERSER_NARRATIVE, converser_narrative
from agents.jev_agent.journal import JOURNAL_NARRATIVE, journal_narrative, Journal
from agents.jev_agent.reflex import NO_REFLEX_LINE, ReflexBrief
from agents.jev_agent.stint import Brief
from agents.jev_agent.tracelog import AgentTrace
from agents.jev_agent.worldmodel import WorldModel
from helpers import make_entity, make_object, make_observation

from conftest import RecordingBridge, bridge, deps, planner_lines, world_model


def test_the_prompt_gives_the_goal_the_physics_numbers_and_the_budget() -> None:
    assert "build a civilization" in SETTLEMENT_NARRATIVE
    assert "has 16 health" in SETTLEMENT_NARRATIVE
    assert "for 3 every tick" in SETTLEMENT_NARRATIVE
    assert "sword +3" in SETTLEMENT_NARRATIVE
    assert "`shout` reaches 60 tiles" in SETTLEMENT_NARRATIVE
    assert "You get 20 tool calls per turn" in SETTLEMENT_NARRATIVE
    assert "costs about\n  3 ticks all told" in SETTLEMENT_NARRATIVE


def test_the_prompt_states_physics_and_leaves_strategy_to_the_settlers() -> None:
    advice = (
        "side by side",
        "gang up",
        "Alone",
        "wolf-proof",
        "town plan",
        "never wall",
        "build one early",
        "A house is",
    )
    for phrase in advice:
        assert phrase not in SETTLEMENT_NARRATIVE, phrase


def test_the_prompt_shows_whole_briefs_including_shout_phrases() -> None:
    """Three stint briefs and two reflex briefs, each shown in full."""
    for field in ("instruction:", "success_condition:", "max_ticks:"):
        assert SETTLEMENT_NARRATIVE.count(field) == 5, field
    assert SETTLEMENT_NARRATIVE.count("shouts:") == 2
    assert SETTLEMENT_NARRATIVE.count("places:") == 1
    assert SETTLEMENT_NARRATIVE.count("trigger_distance:") == 2


def test_the_prompt_explains_how_jev_sees_the_world() -> None:
    """The planner is told what Jev can and cannot read (docs/05)."""
    assert "Jev does\n  not understand absolute coordinates" in SETTLEMENT_NARRATIVE
    assert 'the stint ends\n  with "lost"' in SETTLEMENT_NARRATIVE
    assert "bushes marked B" in SETTLEMENT_NARRATIVE
    assert "each stage leaves a\n  mark Jev can see" in SETTLEMENT_NARRATIVE


def test_the_example_shouts_are_not_about_wolves() -> None:
    """The first run copied the example phrase verbatim 144 times."""
    shout_lines = [
        line for line in SETTLEMENT_NARRATIVE.splitlines() if "shouts:" in line
    ]
    assert shout_lines
    for line in shout_lines:
        assert "wolf" not in line.lower(), line


async def test_the_prompt_carries_the_latest_look_and_stint_report(
    bridge: RecordingBridge, tmp_path: Path
) -> None:
    planner = Planner(
        bridge, "ada", model_name="test", trace=AgentTrace("ada", tmp_path)
    )
    report = await bridge.run_stint(
        Brief(instruction="Chop", success_condition="2 wood", max_ticks=5)
    )
    planner.note_report(report)
    prompt = await planner.build_prompt()
    assert prompt.startswith("Your name is ada.")
    assert "you are ada at (10, 10)" in prompt
    assert "STINT REPORT: Chop" in prompt
    assert "Your journal:" in prompt
    assert bridge.journal_waits == 1, "the prompt waits for the journal rewrite"


async def test_a_turn_writes_its_prompt_tools_and_result_to_the_trace(
    bridge: RecordingBridge, tmp_path: Path
) -> None:
    trace = AgentTrace("ada", tmp_path)
    planner = Planner(bridge, "ada", model_name="test", trace=trace)
    test_model = TestModel(call_tools=["remember"], custom_output_text="Noted.")
    with planner.agent.override(model=test_model):
        await planner.take_turn()

    lines = planner_lines(trace)
    events = [line["event"] for line in lines]
    assert events[0] == "turn_start"
    assert events[-1] == "turn_end"
    assert "tool_call" in events and "tool_result" in events
    assert all(line["entity_id"] == "ada" for line in lines)
    assert all(line["turn"] == 1 for line in lines)
    assert all(line["tick"] == bridge.model.tick for line in lines)

    start = lines[0]
    assert isinstance(start["prompt"], str)
    assert "you are ada at (10, 10)" in start["prompt"]

    call = next(line for line in lines if line["event"] == "tool_call")
    assert call["tool"] == "remember"
    assert isinstance(call["args"], dict)

    result = next(line for line in lines if line["event"] == "tool_result")
    assert result["tool"] == "remember"
    assert str(result["result"]).startswith(
        "noted\n[tick 5 \u00b7 day 0 5/300 day; this turn has cost 0 ticks"
    )

    end = lines[-1]
    assert end["thought"] == "Noted."
    assert end["tool_calls"] == 1
    assert isinstance(end["duration_ms"], int)
    usage = end["usage"]
    assert isinstance(usage, dict)
    assert set(usage) == {
        "input_tokens",
        "output_tokens",
        "cached_tokens",
        "requests",
        "cost_usd",
        # TestModel responses carry no provider cost, so the turn is flagged.
        "cost_missing",
    }
    assert usage["requests"] >= 1
    assert usage["cost_missing"] is True


def test_the_prompt_explains_the_real_time_clock() -> None:
    assert "ticks every two\n  seconds whether or not you have answered" in (
        SETTLEMENT_NARRATIVE
    )
    assert "only happens\n  if Jev is doing it" in SETTLEMENT_NARRATIVE


def test_the_prompt_teaches_every_recipe() -> None:
    for name, recipe in RECIPES.items():
        assert name in SETTLEMENT_NARRATIVE, name
        assert recipe.cost_text() in SETTLEMENT_NARRATIVE, name


def test_the_prompt_marks_each_recipes_station_and_work() -> None:
    for name, recipe in RECIPES.items():
        line = next(
            line
            for line in SETTLEMENT_NARRATIVE.splitlines()
            if line.startswith(f"  {name} =")
        )
        assert f"[{recipe.station or 'hand'}, {recipe.work} action" in line


def test_the_prompt_says_how_placed_objects_behave() -> None:
    for phrase in (
        "Walls block everyone",
        "A door blocks wolves",
        "message board",
        "can be dismantled",
    ):
        assert phrase in SETTLEMENT_NARRATIVE


async def test_the_prompt_shows_the_reflex_brief_and_any_reflex_report(
    bridge: RecordingBridge, tmp_path: Path
) -> None:
    planner = Planner(bridge, "ada", model_name="test", trace=AgentTrace.disabled())
    assert NO_REFLEX_LINE in await planner.build_prompt()

    bridge.reflex = ReflexBrief(
        instruction="Walk to the beds.",
        success_condition="you are next to a bed",
        max_ticks=10,
        trigger_distance=5,
    )
    bridge.reflex_notes = [
        "[reflex ran ticks 3-9: ended because threat_gone; " "health 20 -> 17]"
    ]
    prompt = await planner.build_prompt()
    assert "Walk to the beds." in prompt
    assert "[reflex ran ticks 3-9" in prompt


def test_the_prompt_gives_the_fatigue_the_day_and_the_tier_numbers() -> None:
    for phrase in (
        "Fatigue runs from 0 to 100",
        "every 4 ticks by day",
        "From 60 you are tired",
        "At 100 you collapse",
        "A day is 300 ticks",
        "1 fatigue per tick at\n  night",
        "copper_vein needs a wielded pickaxe",
        "3 work makes one unit",
        "workshop_table, furnace or anvil",
        "iron_sword +5",
    ):
        assert phrase in SETTLEMENT_NARRATIVE, phrase


def test_the_recipe_table_in_the_prompt_comes_from_items() -> None:
    for name, recipe in RECIPES.items():
        line = f"  {name} = {recipe.cost_text()}"
        assert line in SETTLEMENT_NARRATIVE, name
    assert "[furnace, 2 actions]" in SETTLEMENT_NARRATIVE
    assert "[hand, 1 action]" in SETTLEMENT_NARRATIVE


def test_the_prompt_no_longer_offers_invitations() -> None:
    condensed = " ".join(SETTLEMENT_NARRATIVE.split())
    assert "open_to_talk" not in SETTLEMENT_NARRATIVE
    assert "invitation" not in condensed
    assert "interrupted: conversation conv_N started" in SETTLEMENT_NARRATIVE
    assert "hails: []" in SETTLEMENT_NARRATIVE
    assert "hail a settler you named with the line you wrote" in condensed


def test_the_prompt_lists_the_four_channels_with_their_purpose() -> None:
    condensed = " ".join(SETTLEMENT_NARRATIVE.split())
    assert "Reaching the others, and what each way is good for:" in condensed
    assert "`shout` reaches 60 tiles" in condensed
    assert "The opening line is heard by everyone within 10 tiles" in condensed
    assert "A message board: twenty notes" in condensed
    assert "Sign: holds one line" in condensed
    assert "crafts one from 2 wood if you need it" in condensed


def test_the_prompt_gives_the_settler_a_place_of_its_own_to_find() -> None:
    assert (
        "Each of you also has to find your place in it: what you do, whom you work "
        "with,\nand what you are known for." in SETTLEMENT_NARRATIVE
    )


def test_the_goal_names_a_shelter_of_your_own_and_a_dependable_tomorrow() -> None:
    assert (
        "Together, build a civilization: a settlement that\nlasts, where every one "
        "of you has a shelter of your own to sleep in, and where\nfood, safety and "
        "rest are things you can count on tomorrow and not only today."
        in SETTLEMENT_NARRATIVE
    )


def test_the_settler_count_comes_from_the_scenario() -> None:
    assert SETTLEMENT_NARRATIVE.startswith("You are one of twelve people")
    assert settlement_narrative(6).startswith("You are one of six people")
    assert settlement_narrative(2).startswith("You are one of two people")
    # Outside the spelled range the digits are used rather than a wrong word.
    assert settlement_narrative(20).startswith("You are one of 20 people")
    # Nothing but the count changes.
    assert settlement_narrative(6).replace("six", "twelve", 1) == (SETTLEMENT_NARRATIVE)


def test_the_converser_and_journal_prompts_take_the_same_count() -> None:
    assert converser_narrative(6).startswith("You are one of six people")
    assert journal_narrative(6).startswith("You are one of six people")
    assert CONVERSER_NARRATIVE.startswith("You are one of twelve people")
    assert JOURNAL_NARRATIVE.startswith("You are one of twelve people")


def test_the_converser_prompt_states_the_goal_once_and_briefly() -> None:
    assert (
        "Together, build a\ncivilization that lasts: a shelter of your own each, and "
        "food, safety and rest\nyou can count on tomorrow." in CONVERSER_NARRATIVE
    )


async def test_the_prompt_waits_for_an_in_flight_rewrite_before_reading_the_journal(
    bridge: RecordingBridge, tmp_path: Path
) -> None:
    planner = Planner(
        bridge, "ada", model_name="test", trace=AgentTrace("ada", tmp_path)
    )
    # The rewrite lands while the turn is parked in `await_journal`.
    bridge.on_journal_wait = lambda: Journal(
        story_so_far="I slept by the lake.", tomorrow="Chop six wood."
    ).save(planner.memory_path)

    prompt = await planner.build_prompt()

    assert bridge.journal_waits == 1
    assert "I slept by the lake." in prompt
    assert "Chop six wood." in prompt


def test_the_prompt_states_the_hail_physics() -> None:
    condensed = " ".join(SETTLEMENT_NARRATIVE.split())
    assert "walks you to them and says your opening line out loud" in condensed
    assert "out of a conversation for at least 60 ticks" in condensed
    assert "walks up and hails you" in condensed


def test_the_converser_prompt_says_a_hailed_seat_can_happen() -> None:
    condensed = " ".join(CONVERSER_NARRATIVE.split())
    assert "someone walked up and addressed you" in condensed


async def test_the_turn_prompt_carries_the_body_alerts(
    deps: PlannerDeps, bridge: RecordingBridge, tmp_path: Path
) -> None:
    bridge.model.update(make_observation(6, make_entity("ada", (10, 10), food=4)))
    planner = Planner(
        bridge, "ada", model_name="test", trace=AgentTrace("ada", tmp_path)
    )
    prompt = await planner.build_prompt()
    assert "!! FOOD LOW: food 4/100" in prompt


def test_the_turn_prompt_states_being_enclosed(world_model: WorldModel) -> None:
    """esme sat in a 1-tile cell for 64 ticks and her planner never knew."""
    ring = [
        (10 + dx, 10 + dy)
        for dx in (-1, 0, 1)
        for dy in (-1, 0, 1)
        if (dx, dy) != (0, 0)
    ]
    world_model.update(
        make_observation(
            8,
            make_entity("ada", (10, 10)),
            objects=[
                make_object(f"wood_wall_{i}", "wood_wall", tile, {"owner": "ada"})
                for i, tile in enumerate(ring)
            ],
        )
    )
    alerts = body_alerts(world_model)
    assert any(line.startswith("!! ENCLOSED:") for line in alerts)
    assert any("dismantle removes a placed piece" in line for line in alerts)


def test_the_prompt_states_the_one_hunger_number_for_sleep() -> None:
    assert (
        f"Falling asleep needs food above {items.HUNGRY_WAKE_FOOD}"
        in SETTLEMENT_NARRATIVE
    )
    assert (
        f"when your\n  food falls to {items.HUNGRY_WAKE_FOOD}" in SETTLEMENT_NARRATIVE
    )


def test_the_journal_writer_opens_with_the_planner_goal() -> None:
    from agents.jev_agent.journal import journal_narrative

    opening = items.island_opening(6)
    assert settlement_narrative(6).startswith(opening)
    assert journal_narrative(6).startswith(opening)


def test_the_narrative_says_where_each_raw_source_grows() -> None:
    """Nothing told a settler where a resource it had never seen grows."""
    text = settlement_narrative(6)
    assert "Where things are found" in text
    assert "reeds grow on the banks and in the shallows of fresh water" in text
    assert "clay deposits lie in tight patches 2 to 7 tiles back" in text
    assert "berry bushes grow in thickets along the edges of woodland" in text
    assert "Copper and iron veins sit in rock outcrops on high ground" in text
