"""Shared test fixtures for world tests."""

import pytest

from world.state import Entity, Tile, World
from world.types import Position


@pytest.fixture
def empty_world() -> World:
    """10x10 world with all tiles walkable."""
    return World(width=10, height=10)


@pytest.fixture
def world_with_walls() -> World:
    """10x10 world with a wall across the middle at y=5."""
    world = World(width=10, height=10)
    for x in range(10):
        world.set_tile(
            Tile(position=Position(x=x, y=5), walkable=False, opaque=True)
        )
    return world


@pytest.fixture
def world_with_l_wall() -> World:
    """10x10 world with an L-shaped wall blocking diagonal movement.

    Wall tiles at (5, 4) and (6, 5):
        . . . . . W .
        . . . . . . W
        . . . . . . .

    Entity at (5, 5) cannot move NE because (5, 4) blocks N.
    """
    world = World(width=10, height=10)
    world.set_tile(Tile(position=Position(x=5, y=4), walkable=False, opaque=True))
    world.set_tile(Tile(position=Position(x=6, y=5), walkable=False, opaque=True))
    return world


@pytest.fixture
def two_entities(empty_world: World) -> World:
    """World with two entities at (2,2) and (7,7)."""
    empty_world.add_entity(
        Entity(entity_id="entity_a", position=Position(x=2, y=2))
    )
    empty_world.add_entity(
        Entity(entity_id="entity_b", position=Position(x=7, y=7))
    )
    return empty_world


@pytest.fixture
def adjacent_entities(empty_world: World) -> World:
    """World with two adjacent entities at (3,3) and (4,3)."""
    empty_world.add_entity(
        Entity(entity_id="entity_a", position=Position(x=3, y=3))
    )
    empty_world.add_entity(
        Entity(entity_id="entity_b", position=Position(x=4, y=3))
    )
    return empty_world


@pytest.fixture
def three_entities_triangle(empty_world: World) -> World:
    """World with three entities in a triangle for cycle testing.

    Positions:
        a at (3, 3)
        b at (4, 3)
        c at (4, 4)
    """
    empty_world.add_entity(
        Entity(entity_id="a", position=Position(x=3, y=3))
    )
    empty_world.add_entity(
        Entity(entity_id="b", position=Position(x=4, y=3))
    )
    empty_world.add_entity(
        Entity(entity_id="c", position=Position(x=4, y=4))
    )
    return empty_world
