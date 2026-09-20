"""World model updates driven by synthetic Observation protos."""

from __future__ import annotations

import json

from agents import world_pb2 as pb
from agents.jev_agent import items
from agents.jev_agent.worldmodel import WorldModel

from helpers import (
    acted_event,
    make_entity,
    make_object,
    make_observation,
    make_tiles,
    utterance_event,
)


def test_first_observation_sets_position_settlement_and_stats() -> None:
    model = WorldModel("ada")
    model.update(
        make_observation(
            7, make_entity("ada", (5, 6), health=14, food=35, inventory={"wood": 3})
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


# --- building (docs/08_building.md) ----------------------------------------


def test_walkability_is_refreshed_when_a_tile_is_seen_again() -> None:
    """A wall can be built and torn down; memory must follow the last look."""
    model = WorldModel("ada")
    model.update(make_observation(1, make_entity("ada", (10, 10))))
    assert model.is_walkable((12, 10))

    model.update(
        make_observation(
            2,
            make_entity("ada", (10, 10)),
            tiles=make_tiles((10, 10), blocked=[(12, 10)]),
            objects=[make_object("w1", "wood_wall", (12, 10))],
        )
    )
    assert not model.is_walkable((12, 10))

    model.update(make_observation(3, make_entity("ada", (10, 10))))
    assert model.is_walkable((12, 10))
    assert not model.object_at((12, 10))


def test_a_wall_blocks_even_before_the_tile_is_seen_again() -> None:
    model = WorldModel("ada")
    model.update(make_observation(1, make_entity("ada", (10, 10))))
    model.update(
        make_observation(
            2,
            make_entity("ada", (10, 10)),
            objects=[make_object("w1", "stone_wall", (11, 10))],
        )
    )
    assert not model.is_walkable((11, 10))


def test_doors_stay_walkable_for_settlers() -> None:
    model = WorldModel("ada")
    model.update(
        make_observation(
            1,
            make_entity("ada", (10, 10)),
            objects=[make_object("d1", "door", (11, 10))],
        )
    )
    assert model.is_walkable((11, 10))


def test_object_layers_are_told_apart() -> None:
    model = WorldModel("ada")
    model.update(
        make_observation(
            1,
            make_entity("ada", (10, 10)),
            objects=[
                make_object("r1", "road", (11, 10)),
                make_object("b1", "bed", (11, 10)),
            ],
        )
    )
    assert [o.object_id for o in model.ground_objects_at((11, 10))] == ["r1"]
    assert [o.object_id for o in model.structure_objects_at((11, 10))] == ["b1"]


def test_a_workshop_table_is_found_only_when_it_is_within_reach() -> None:
    near = WorldModel("ada")
    near.update(
        make_observation(
            1,
            make_entity("ada", (10, 10)),
            objects=[make_object("ws1", "workshop_table", (11, 11))],
        )
    )
    assert near.station_near(items.WORKSHOP_TABLE) is not None

    far = WorldModel("ada")
    far.update(
        make_observation(
            1,
            make_entity("ada", (10, 10)),
            objects=[make_object("ws1", "workshop_table", (13, 10))],
        )
    )
    assert far.station_near(items.WORKSHOP_TABLE) is None


# -- message board unread tracking (docs/09 section 6) -----------------------


def _board(slot0_title: str = "Wood pile", tick: int = 4) -> pb.WorldObject:
    return make_object(
        "board_1",
        "message_board",
        (11, 11),
        {
            "notes": json.dumps(
                [
                    {
                        "title": slot0_title,
                        "text": "chest by the spring",
                        "author": "bob",
                        "tick": tick,
                    }
                ]
            )
        },
    )


def test_a_new_note_by_another_author_is_queued_once() -> None:
    model = WorldModel("ada")
    digest = model.update(
        make_observation(5, make_entity("ada", (10, 10)), objects=[_board()])
    )

    assert digest.board_notes == ["[board_1: new note by bob: 'Wood pile']"]

    # Staying in view on the next tick, with the note unchanged, queues nothing
    # more: the push is once per (board, slot, tick).
    digest = model.update(
        make_observation(6, make_entity("ada", (10, 10)), objects=[_board()])
    )
    assert digest.board_notes == []


def test_a_changed_note_is_queued_again() -> None:
    model = WorldModel("ada")
    model.update(make_observation(5, make_entity("ada", (10, 10)), objects=[_board()]))

    digest = model.update(
        make_observation(
            6,
            make_entity("ada", (10, 10)),
            objects=[_board(slot0_title="New plan", tick=6)],
        )
    )
    assert digest.board_notes == ["[board_1: new note by bob: 'New plan']"]


def test_the_actors_own_note_is_never_queued() -> None:
    model = WorldModel("ada")
    board = make_object(
        "board_1",
        "message_board",
        (11, 11),
        {
            "notes": json.dumps(
                [{"title": "Mine", "text": "hi", "author": "ada", "tick": 4}]
            )
        },
    )
    digest = model.update(
        make_observation(5, make_entity("ada", (10, 10)), objects=[board])
    )
    assert digest.board_notes == []


def test_unread_count_drops_to_zero_once_read_and_rises_on_a_new_note() -> None:
    model = WorldModel("ada")
    model.update(make_observation(5, make_entity("ada", (10, 10)), objects=[_board()]))

    assert model.unread_note_count("board_1") == 1
    model.mark_board_read("board_1")
    assert model.unread_note_count("board_1") == 0

    model.update(
        make_observation(
            6,
            make_entity("ada", (10, 10)),
            objects=[_board(slot0_title="New plan", tick=6)],
        )
    )
    assert model.unread_note_count("board_1") == 1


# -- the body clock the world sends (docs/10_metal_and_sleep.md, section 4) ---


def test_sleeping_on_and_collapsed_reach_the_entity_info() -> None:
    """The proto carries both, so the actor can tell a nap from a collapse."""
    model = WorldModel("ada")
    model.update(
        make_observation(
            5,
            make_entity("ada", (10, 10), asleep=True, sleeping_on="bed_1"),
            entities=[
                make_entity("bram", (11, 10), asleep=True, collapsed=True),
            ],
        )
    )

    assert model.self_info.asleep is True
    assert model.self_info.sleeping_on == "bed_1"
    assert model.self_info.collapsed is False

    bram = model.entities["bram"]
    assert (bram.sleeping_on, bram.collapsed) == ("", True)
