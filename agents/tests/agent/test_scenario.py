"""The scenario's settler count, the prices and the running cost."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
import pytest
from agents.jev_agent.agent import JevAgent
from agents.jev_agent.conversation import ModelConverser
from agents.jev_agent.items import DEFAULT_SETTLER_COUNT
from agents.jev_agent.pricing import JEV_USD_PER_MILLION_INPUT_TOKENS
from agents.jev_agent.journal import ModelJournalWriter
from agents.jev_agent.stint import Brief
from helpers import FakeJevClient

from conftest import FakeWorldClient, build_agent, observations


def test_starting_an_agent_records_the_prices_it_is_billed_at(
    fake_jev: FakeJevClient, tmp_path: Path
) -> None:
    agent = build_agent(FakeWorldClient([]), fake_jev, tmp_path)

    payload = json.loads(agent.trace.pricing_path.read_text(encoding="utf-8"))

    assert payload == {
        "jev_usd_per_million_input_tokens": JEV_USD_PER_MILLION_INPUT_TOKENS,
        "planner_model": "test",
    }


async def test_the_status_report_carries_the_running_cost(tmp_path: Path) -> None:
    jev = FakeJevClient(default_action="wait")
    world = FakeWorldClient(observations(4))
    agent = build_agent(world, jev, tmp_path)

    async def plan() -> None:
        await agent.run_stint(
            Brief(instruction="Stand guard", success_condition="never", max_ticks=2)
        )
        await asyncio.sleep(3600)

    agent.planner.run = plan  # type: ignore[method-assign]
    await agent.run()

    costs = [json.loads(status[4]) for status in world.statuses]
    assert costs, "every status report carries a cost block"
    assert costs[-1]["jev_calls"] == len(jev.calls) == 2
    assert costs[-1]["jev_usd"] > costs[0]["jev_usd"], "Jev ticks cost money"
    assert costs[0]["jev_calls"] == 1, "the first report follows the first Jev tick"
    assert costs[-1]["total_usd"] == costs[-1]["jev_usd"]


def test_the_settlers_flag_defaults_to_twelve() -> None:
    from agents.jev_agent.__main__ import parse_args

    assert parse_args(["--entity", "ada"]).settlers == DEFAULT_SETTLER_COUNT


def test_the_settlers_flag_reaches_every_prompt(tmp_path: Path) -> None:
    from agents.jev_agent.__main__ import parse_args

    args = parse_args(["--entity", "ada", "--settlers", "6"])
    assert args.settlers == 6

    agent = JevAgent(
        FakeWorldClient([]),  # type: ignore[arg-type]
        FakeJevClient([]),
        "ada",
        log_root=tmp_path,
        planner_model="test",
        journal_model="test",
        settler_count=args.settlers,
    )
    converser = agent.converser
    writer = agent.journal_writer
    assert isinstance(converser, ModelConverser)
    assert isinstance(writer, ModelJournalWriter)
    assert agent.settler_count == 6
    assert agent.planner.settler_count == 6
    assert converser.settler_count == 6
    assert writer.settler_count == 6


def test_a_settler_count_below_one_is_refused() -> None:
    from agents.jev_agent.__main__ import parse_args

    with pytest.raises(SystemExit):
        parse_args(["--entity", "ada", "--settlers", "0"])
