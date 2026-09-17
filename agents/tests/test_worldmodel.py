"""World model updates driven by synthetic Observation protos."""

from __future__ import annotations

from agents import world_pb2 as pb
from agents.jev_agent.worldmodel import WorldModel

from helpers import (
    acted_event,
    make_entity,
    make_object,
    make_observation,
    make_tiles,
)


def test_first_observation_sets_position_settlement_and_stats() -> None:
    model = WorldModel("ada")
    model.update(
        make_observation(
            7, make_entity("ada", (5, 6), health=14, hunger=35, inventory={"wood": 3})
        )
    )
    assert model.position == (5, 6)
    assert model.settlement == (5, 6)
    assert model.self_info.health == 14
    assert model.self_info.inventory == {"wood": 3}
    assert model.tick == 7


def test_tiles_and_objects_are_remembered_after_leaving_view() -> None:
    model = WorldModel("ada")
    model.update(
        make_observation(
            1,
            make_entity("ada", (0, 0)),
            objects=[make_object("tree_1", "tree", (3, 0), {"remaining": "4"})],
        )
    )
    # Walk far away; the tree is no longer in view but must still be known.
    model.update(make_observation(2, make_entity("ada", (40, 40))))
    assert "tree_1" in model.objects
    assert model.objects["tree_1"].remaining == 4
    assert model.is_known((3, 0))


def test_object_inside_view_that_stops_being_reported_is_forgotten() -> None:
    model = WorldModel("ada")
    tree = make_object("tree_1", "tree", (3, 0))
    model.update(make_observation(1, make_entity("ada", (0, 0)), objects=[tree]))
    model.update(make_observation(2, make_entity("ada", (0, 0)), objects=[]))
    assert "tree_1" not in model.objects


def test_blocking_objects_make_their_tile_unwalkable() -> None:
    model = WorldModel("ada")
    model.update(
        make_observation(
            1,
            make_entity("ada", (0, 0)),
            objects=[
                make_object("tree_1", "tree", (1, 0)),
                make_object("bush_1", "bush", (0, 1), {"berry_count": "1"}),
            ],
        )
    )
    assert not model.is_walkable((1, 0))
    assert model.is_walkable((0, 1))


def test_unwalkable_tiles_are_recorded() -> None:
    model = WorldModel("ada")
    model.update(
        make_observation(
            1,
            make_entity("ada", (0, 0)),
            tiles=make_tiles((0, 0), radius=2, blocked=[(1, 1)]),
        )
    )
    assert not model.is_walkable((1, 1))
    assert model.is_walkable((2, 2))
    assert not model.is_known((9, 9))


def test_own_actions_become_history_lines() -> None:
    model = WorldModel("ada")
    digest = model.update(
        make_observation(
            12,
            make_entity("ada", (0, 0)),
            events=[
                acted_event("ada", "extract", True, "chopped tree_1 (+1 wood)"),
                acted_event("bob", "extract", True, "not mine"),
            ],
        )
    )
    assert len(digest.own_actions) == 1
    assert model.recent_history() == ["t12 extract ok (chopped tree_1 (+1 wood))"]


def test_damage_death_and_utterances_land_in_the_digest() -> None:
    model = WorldModel("ada")
    model.update(make_observation(1, make_entity("ada", (0, 0))))
    digest = model.update(
        make_observation(
            2,
            make_entity("ada", (0, 0), health=11),
            events=[
                pb.ObservationEvent(
                    entity_damaged=pb.EntityDamaged(
                        entity_id="ada",
                        attacker_id="wolf_1",
                        amount=3,
                        remaining_health=11,
                    )
                ),
                pb.ObservationEvent(
                    utterance=pb.Utterance(
                        speaker_id="bob", channel="local", text="wolf!"
                    )
                ),
                pb.ObservationEvent(
                    entity_died=pb.EntityDied(entity_id="carl", killer_id="wolf_1")
                ),
            ],
        )
    )
    assert digest.damage_taken == 3
    assert digest.attackers == ["wolf_1"]
    assert digest.deaths == ["carl"]
    assert digest.utterances[0].text == "wolf!"


def test_respawn_moves_the_remembered_settlement() -> None:
    model = WorldModel("ada")
    model.update(make_observation(1, make_entity("ada", (5, 5))))
    digest = model.update(
        make_observation(
            2,
            make_entity("ada", (30, 30)),
            events=[
                pb.ObservationEvent(
                    entity_respawned=pb.EntityRespawned(
                        entity_id="ada", position=pb.Position(x=30, y=30)
                    )
                )
            ],
        )
    )
    assert digest.self_respawned
    assert model.settlement == (30, 30)


def test_chest_contents_and_board_notes_are_parsed() -> None:
    model = WorldModel("ada")
    model.update(
        make_observation(
            1,
            make_entity("ada", (0, 0)),
            objects=[
                make_object(
                    "chest_1", "chest", (1, 0), {"contents": '{"wood": 4, "stone": 0}'}
                ),
                make_object(
                    "board_1",
                    "message_board",
                    (0, 1),
                    {
                        "notes": '[{"title": "plan", "text": "chop", "author": "bob"}, null]'
                    },
                ),
                make_object("pile_1", "item_pile", (2, 0), {"contents": "not json"}),
            ],
        )
    )
    assert model.objects["chest_1"].contents() == {"wood": 4}
    notes = model.objects["board_1"].notes()
    assert len(notes) == 1 and notes[0]["title"] == "plan"
    assert model.objects["pile_1"].contents() == {}


def test_objects_by_type_is_sorted_by_distance() -> None:
    model = WorldModel("ada")
    model.update(
        make_observation(
            1,
            make_entity("ada", (0, 0)),
            objects=[
                make_object("tree_far", "tree", (6, 0)),
                make_object("tree_near", "tree", (2, 0)),
                make_object("rock_1", "rock_small", (1, 1)),
            ],
        )
    )
    trees = model.objects_by_type(["tree"])
    assert [t.object_id for t in trees] == ["tree_near", "tree_far"]
    assert model.objects_by_type(["tree", "rock_small"])[0].object_id == "rock_1"


def test_nearest_wolf_ignores_players_and_the_dead() -> None:
    model = WorldModel("ada")
    model.update(
        make_observation(
            1,
            make_entity("ada", (0, 0)),
            entities=[
                make_entity("bob", (1, 0)),
                make_entity("wolf_1", (4, 0), entity_type="wolf"),
                make_entity("wolf_2", (2, 0), entity_type="wolf", alive=False),
            ],
        )
    )
    wolf = model.nearest_wolf()
    assert wolf is not None and wolf.entity_id == "wolf_1"


def test_default_remaining_is_used_when_the_object_has_not_been_touched() -> None:
    model = WorldModel("ada")
    model.update(
        make_observation(
            1,
            make_entity("ada", (0, 0)),
            objects=[
                make_object("tree_1", "tree", (1, 0)),
                make_object("boulder_1", "boulder", (2, 0)),
            ],
        )
    )
    assert model.objects["tree_1"].remaining == 4
    assert model.objects["boulder_1"].remaining == 6
