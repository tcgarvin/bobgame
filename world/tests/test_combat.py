"""Tests for attacks, damage, death and respawn."""

from world.combat import kill_entity, process_attack_phase
from world.containers import read_contents
from world.events import TickEvents
from world.items import attack_damage
from world.state import Entity, Inventory, World
from world.stats import (
    RESPAWN_DELAY_TICKS,
    RESPAWN_HUNGER,
    RESPAWN_SAFE_DISTANCE,
    process_respawns,
)
from world.types import AttackIntent, Position
from world.wolves import WOLF_MAX_HEALTH


def _world_with_pair() -> World:
    world = World(width=20, height=20)
    world.add_entity(Entity(entity_id="alice", position=Position(x=5, y=5)))
    world.add_entity(Entity(entity_id="bob", position=Position(x=6, y=5)))
    return world


class TestAttackDamage:
    def test_base_damage_for_player_and_wolf(self) -> None:
        assert attack_damage("player", "") == 2
        assert attack_damage("wolf", "") == 3

    def test_wielded_bonus(self) -> None:
        assert attack_damage("player", "sword") == 5
        assert attack_damage("player", "axe") == 4
        assert attack_damage("player", "pickaxe") == 3
        assert attack_damage("player", "berry") == 2

    def test_adjacent_attack_reduces_health(self) -> None:
        world = _world_with_pair()
        events = TickEvents()

        process_attack_phase(
            world,
            {"alice": AttackIntent(entity_id="alice", target_entity_id="bob")},
            events,
        )

        assert world.get_entity("bob").health == 18
        assert len(events.damage_events) == 1
        assert events.damage_events[0].attacker_id == "alice"
        assert events.action_results[0].success

    def test_attack_requires_adjacency(self) -> None:
        world = _world_with_pair()
        world.update_entity_position("bob", Position(x=9, y=9))
        events = TickEvents()

        process_attack_phase(
            world,
            {"alice": AttackIntent(entity_id="alice", target_entity_id="bob")},
            events,
        )

        assert world.get_entity("bob").health == 20
        assert not events.action_results[0].success
        assert "not adjacent" in events.action_results[0].details

    def test_cannot_attack_self(self) -> None:
        world = _world_with_pair()
        events = TickEvents()

        process_attack_phase(
            world,
            {"alice": AttackIntent(entity_id="alice", target_entity_id="alice")},
            events,
        )

        assert not events.action_results[0].success

    def test_simultaneous_kills(self) -> None:
        world = _world_with_pair()
        for entity_id in ("alice", "bob"):
            entity = world.get_entity(entity_id)
            world.set_entity(entity.with_health(2))
        events = TickEvents()

        process_attack_phase(
            world,
            {
                "alice": AttackIntent(entity_id="alice", target_entity_id="bob"),
                "bob": AttackIntent(entity_id="bob", target_entity_id="alice"),
            },
            events,
        )

        assert not world.get_entity("alice").alive
        assert not world.get_entity("bob").alive
        assert {d.entity_id for d in events.deaths} == {"alice", "bob"}


class TestDeath:
    def test_death_drops_inventory_as_pile(self) -> None:
        world = _world_with_pair()
        alice = world.get_entity("alice")
        world.set_entity(alice.with_inventory(Inventory().add("wood", 3)))
        events = TickEvents()

        kill_entity(world, "alice", "bob", events)

        piles = [
            o for o in world.all_objects().values() if o.object_type == "item_pile"
        ]
        assert len(piles) == 1
        assert read_contents(piles[0]) == {"wood": 3}
        assert piles[0].position == Position(x=5, y=5)

    def test_dead_entity_leaves_position_index_but_stays_listed(self) -> None:
        world = _world_with_pair()
        events = TickEvents()

        kill_entity(world, "alice", "", events)

        assert "alice" in world.all_entities()
        assert not world.is_position_occupied(Position(x=5, y=5))
        dead = world.get_entity("alice")
        assert not dead.alive
        assert dead.health == 0
        assert dead.status_bits & 1

    def test_wolf_is_removed_on_death(self) -> None:
        world = World(width=20, height=20)
        world.add_entity(
            Entity(
                entity_id="wolf_1",
                position=Position(x=3, y=3),
                entity_type="wolf",
                health=10,
                max_health=10,
            )
        )
        events = TickEvents()

        kill_entity(world, "wolf_1", "alice", events)

        assert "wolf_1" not in world.all_entities()
        assert [e.entity_id for e in events.entities_despawned] == ["wolf_1"]


class TestRespawn:
    def test_respawn_after_delay_at_settlement(self) -> None:
        world = _world_with_pair()
        world.settlement = Position(x=1, y=1)
        events = TickEvents()
        kill_entity(world, "alice", "bob", events)

        world.tick = RESPAWN_DELAY_TICKS - 1
        process_respawns(world, events)
        assert not world.get_entity("alice").alive

        world.tick = RESPAWN_DELAY_TICKS
        process_respawns(world, events)

        alice = world.get_entity("alice")
        assert alice.alive
        assert alice.position == Position(x=1, y=1)
        assert alice.health == alice.max_health
        assert alice.hunger == RESPAWN_HUNGER
        assert alice.inventory.items == ()
        assert world.is_position_occupied(Position(x=1, y=1))
        assert [r.entity_id for r in events.respawns] == ["alice"]

    def test_respawn_without_settlement_uses_death_position(self) -> None:
        world = _world_with_pair()
        events = TickEvents()
        kill_entity(world, "alice", "bob", events)

        world.tick = RESPAWN_DELAY_TICKS
        process_respawns(world, events)

        assert world.get_entity("alice").position == Position(x=5, y=5)

    def test_respawn_picks_free_tile_when_settlement_occupied(self) -> None:
        world = _world_with_pair()
        world.settlement = Position(x=6, y=5)  # bob stands here
        events = TickEvents()
        kill_entity(world, "alice", "bob", events)

        world.tick = RESPAWN_DELAY_TICKS
        process_respawns(world, events)

        alice = world.get_entity("alice")
        assert alice.position != Position(x=6, y=5)
        assert max(abs(alice.position.x - 6), abs(alice.position.y - 5)) == 1

    def test_respawn_avoids_a_wolf_camping_the_settlement(self) -> None:
        world = World(width=60, height=60)
        world.settlement = Position(x=30, y=30)
        world.add_entity(Entity(entity_id="alice", position=Position(x=5, y=5)))
        world.add_entity(_wolf("wolf_1", Position(x=31, y=30)))
        events = TickEvents()
        kill_entity(world, "alice", "wolf_1", events)

        world.tick = RESPAWN_DELAY_TICKS
        process_respawns(world, events)

        alice = world.get_entity("alice")
        assert alice.alive
        distance = max(abs(alice.position.x - 31), abs(alice.position.y - 30))
        assert distance >= RESPAWN_SAFE_DISTANCE
        assert max(abs(alice.position.x - 30), abs(alice.position.y - 30)) <= 12

    def test_respawn_uses_the_settlement_when_the_wolf_is_far_away(self) -> None:
        world = World(width=60, height=60)
        world.settlement = Position(x=30, y=30)
        world.add_entity(Entity(entity_id="alice", position=Position(x=5, y=5)))
        world.add_entity(_wolf("wolf_1", Position(x=30 + RESPAWN_SAFE_DISTANCE, y=30)))
        events = TickEvents()
        kill_entity(world, "alice", "wolf_1", events)

        world.tick = RESPAWN_DELAY_TICKS
        process_respawns(world, events)

        assert world.get_entity("alice").position == Position(x=30, y=30)

    def test_respawn_falls_back_to_the_settlement_when_wolves_are_everywhere(
        self,
    ) -> None:
        world = World(width=20, height=20)
        world.settlement = Position(x=10, y=10)
        world.add_entity(Entity(entity_id="alice", position=Position(x=5, y=5)))
        for index, (x, y) in enumerate([(3, 3), (16, 3), (3, 16), (16, 16), (10, 9)]):
            world.add_entity(_wolf(f"wolf_{index}", Position(x=x, y=y)))
        events = TickEvents()
        kill_entity(world, "alice", "wolf_0", events)

        world.tick = RESPAWN_DELAY_TICKS
        process_respawns(world, events)

        assert world.get_entity("alice").position == Position(x=10, y=10)


def _wolf(entity_id: str, position: Position) -> Entity:
    return Entity(
        entity_id=entity_id,
        position=position,
        entity_type="wolf",
        health=16,
        max_health=16,
    )


def _damage_taken_killing_a_wolf(attacker_damages: list[int]) -> int:
    """Total damage a group takes killing one wolf, everyone striking each tick.

    Attacks are simultaneous, so the wolf still bites on the tick it dies.
    """
    per_tick = sum(attacker_damages)
    ticks = -(-WOLF_MAX_HEALTH // per_tick)
    return ticks * attack_damage("wolf", "")


class TestWolfBalanceRewardsCooperation:
    """The balance contract: alone is costly or fatal, together is cheap."""

    PLAYER_HEALTH = Entity(entity_id="probe", position=Position(x=0, y=0)).max_health

    def test_a_lone_unarmed_settler_dies(self) -> None:
        unarmed = attack_damage("player", "")
        assert _damage_taken_killing_a_wolf([unarmed]) >= self.PLAYER_HEALTH

    def test_a_lone_swordsman_wins_but_loses_at_least_half_their_health(self) -> None:
        taken = _damage_taken_killing_a_wolf([attack_damage("player", "sword")])
        assert self.PLAYER_HEALTH // 2 <= taken < self.PLAYER_HEALTH

    def test_two_armed_settlers_take_a_third_of_one_health_bar_at_most(self) -> None:
        sword = attack_damage("player", "sword")
        axe = attack_damage("player", "axe")
        assert _damage_taken_killing_a_wolf([sword, axe]) <= self.PLAYER_HEALTH // 3

    def test_three_unarmed_settlers_beat_a_wolf_comfortably(self) -> None:
        unarmed = attack_damage("player", "")
        taken = _damage_taken_killing_a_wolf([unarmed] * 3)
        assert taken < self.PLAYER_HEALTH // 2
