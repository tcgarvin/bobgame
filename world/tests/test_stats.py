"""Tests for hunger, starvation and health regeneration."""

from world.events import TickEvents
from world.foraging import process_eat_phase
from world.state import Entity, Inventory, World
from world.stats import (
    find_free_tile,
    process_health_regen,
    process_hunger_phase,
)
from world.types import EatIntent, Position


def _world_with_entity(**entity_kwargs: object) -> World:
    world = World(width=10, height=10)
    world.add_entity(
        Entity(entity_id="bob", position=Position(x=5, y=5), **entity_kwargs)  # type: ignore[arg-type]
    )
    return world


class TestHunger:
    def test_hunger_drops_every_fourth_tick(self) -> None:
        world = _world_with_entity(hunger=80)
        world.tick = 1
        process_hunger_phase(world, TickEvents())
        assert world.get_entity("bob").hunger == 80
        world.tick = 4
        process_hunger_phase(world, TickEvents())
        assert world.get_entity("bob").hunger == 79

    def test_wolves_do_not_starve(self) -> None:
        world = World(width=10, height=10)
        world.add_entity(
            Entity(
                entity_id="wolf_1",
                position=Position(x=1, y=1),
                entity_type="wolf",
                health=10,
                max_health=10,
                hunger=100,
            )
        )
        world.tick = 2
        process_hunger_phase(world, TickEvents())
        wolf = world.get_entity("wolf_1")
        assert wolf.hunger == 100
        assert wolf.health == 10

    def test_starvation_damages_every_fourth_tick(self) -> None:
        world = _world_with_entity(hunger=0)
        events = TickEvents()

        world.tick = 3  # off interval: no starvation damage
        process_hunger_phase(world, events)
        assert world.get_entity("bob").health == 20

        world.tick = 4
        process_hunger_phase(world, events)
        assert world.get_entity("bob").health == 19
        assert events.damage_events[-1].attacker_id == ""

    def test_starvation_can_kill(self) -> None:
        world = _world_with_entity(hunger=0, health=1)
        events = TickEvents()
        world.tick = 4

        process_hunger_phase(world, events)

        assert not world.get_entity("bob").alive
        assert events.deaths[0].entity_id == "bob"


class TestRegeneration:
    def test_regen_when_well_fed(self) -> None:
        world = _world_with_entity(health=10, hunger=80)
        world.tick = 5
        process_health_regen(world)
        assert world.get_entity("bob").health == 11

    def test_no_regen_when_hungry(self) -> None:
        world = _world_with_entity(health=10, hunger=50)
        world.tick = 5
        process_health_regen(world)
        assert world.get_entity("bob").health == 10

    def test_no_regen_off_interval(self) -> None:
        world = _world_with_entity(health=10, hunger=90)
        world.tick = 6
        process_health_regen(world)
        assert world.get_entity("bob").health == 10

    def test_regen_capped_at_max(self) -> None:
        world = _world_with_entity(health=20, hunger=90)
        world.tick = 5
        process_health_regen(world)
        assert world.get_entity("bob").health == 20


class TestEating:
    def test_berry_restores_hunger(self) -> None:
        world = _world_with_entity(hunger=40, inventory=Inventory().add("berry", 2))

        results = process_eat_phase(
            world, {"bob": EatIntent(entity_id="bob", item_type="berry", amount=1)}
        )

        assert results[0].success
        assert world.get_entity("bob").hunger == 60

    def test_hunger_capped_at_max(self) -> None:
        world = _world_with_entity(hunger=95, inventory=Inventory().add("berry", 1))

        process_eat_phase(
            world, {"bob": EatIntent(entity_id="bob", item_type="berry", amount=1)}
        )

        assert world.get_entity("bob").hunger == 100

    def test_eating_wood_fails(self) -> None:
        world = _world_with_entity(inventory=Inventory().add("wood", 1))

        results = process_eat_phase(
            world, {"bob": EatIntent(entity_id="bob", item_type="wood", amount=1)}
        )

        assert not results[0].success
        assert results[0].failure_reason == "not_edible"
        assert world.get_entity("bob").inventory.count("wood") == 1


class TestFindFreeTile:
    def test_returns_center_when_free(self) -> None:
        world = World(width=10, height=10)
        assert find_free_tile(world, Position(x=4, y=4)) == Position(x=4, y=4)

    def test_skips_occupied_and_unwalkable(self) -> None:
        from world.state import Tile

        world = World(width=3, height=3)
        world.add_entity(Entity(entity_id="bob", position=Position(x=1, y=1)))
        for position in (
            Position(x=0, y=0),
            Position(x=1, y=0),
            Position(x=2, y=0),
            Position(x=0, y=1),
            Position(x=2, y=1),
            Position(x=0, y=2),
            Position(x=1, y=2),
        ):
            world.set_tile(Tile(position=position, walkable=False))

        assert find_free_tile(world, Position(x=1, y=1)) == Position(x=2, y=2)
