"""Tests for core types."""

import pytest
from pydantic import ValidationError

from world.types import (
    DIAGONAL_COMPONENTS,
    DIRECTION_DELTAS,
    Direction,
    MoveIntent,
    Position,
)


class TestPosition:
    """Tests for Position class."""

    def test_position_creation(self):
        """Position can be created with x and y."""
        pos = Position(x=5, y=10)
        assert pos.x == 5
        assert pos.y == 10

    def test_position_immutable(self):
        """Position is immutable (frozen)."""
        pos = Position(x=5, y=5)
        with pytest.raises(ValidationError):
            pos.x = 6  # type: ignore

    def test_position_equality(self):
        """Two positions with same coordinates are equal."""
        pos1 = Position(x=5, y=5)
        pos2 = Position(x=5, y=5)
        assert pos1 == pos2

    def test_position_inequality(self):
        """Two positions with different coordinates are not equal."""
        pos1 = Position(x=5, y=5)
        pos2 = Position(x=5, y=6)
        assert pos1 != pos2

    def test_position_hashable(self):
        """Position can be used as dict key or in set."""
        pos1 = Position(x=5, y=5)
        pos2 = Position(x=5, y=5)
        pos3 = Position(x=6, y=6)

        # Same coordinates should have same hash
        assert hash(pos1) == hash(pos2)

        # Can be used in set
        positions = {pos1, pos2, pos3}
        assert len(positions) == 2

        # Can be used as dict key
        mapping = {pos1: "first", pos3: "second"}
        assert mapping[pos2] == "first"

    def test_position_add(self):
        """Positions can be added."""
        pos1 = Position(x=3, y=4)
        pos2 = Position(x=2, y=1)
        result = pos1 + pos2
        assert result == Position(x=5, y=5)

    def test_position_offset_cardinal(self):
        """Position offset works for cardinal directions."""
        pos = Position(x=5, y=5)

        assert pos.offset(Direction.NORTH) == Position(x=5, y=4)
        assert pos.offset(Direction.SOUTH) == Position(x=5, y=6)
        assert pos.offset(Direction.EAST) == Position(x=6, y=5)
        assert pos.offset(Direction.WEST) == Position(x=4, y=5)

    def test_position_offset_diagonal(self):
        """Position offset works for diagonal directions."""
        pos = Position(x=5, y=5)

        assert pos.offset(Direction.NORTHEAST) == Position(x=6, y=4)
        assert pos.offset(Direction.SOUTHEAST) == Position(x=6, y=6)
        assert pos.offset(Direction.SOUTHWEST) == Position(x=4, y=6)
        assert pos.offset(Direction.NORTHWEST) == Position(x=4, y=4)

    def test_position_str(self):
        """Position has readable string representation."""
        pos = Position(x=3, y=7)
        assert str(pos) == "(3, 7)"

    def test_position_repr(self):
        """Position has useful repr."""
        pos = Position(x=3, y=7)
        assert repr(pos) == "Position(x=3, y=7)"


class TestDirection:
    """Tests for Direction enum."""

    def test_direction_values(self):
        """Direction enum has expected values matching proto."""
        assert Direction.NORTH == 1
        assert Direction.NORTHEAST == 2
        assert Direction.EAST == 3
        assert Direction.SOUTHEAST == 4
        assert Direction.SOUTH == 5
        assert Direction.SOUTHWEST == 6
        assert Direction.WEST == 7
        assert Direction.NORTHWEST == 8

    def test_direction_count(self):
        """There are exactly 8 directions."""
        assert len(Direction) == 8

    def test_all_directions_have_deltas(self):
        """All directions have corresponding deltas."""
        for direction in Direction:
            assert direction in DIRECTION_DELTAS

    def test_cardinal_deltas(self):
        """Cardinal direction deltas are correct."""
        assert DIRECTION_DELTAS[Direction.NORTH] == (0, -1)
        assert DIRECTION_DELTAS[Direction.SOUTH] == (0, 1)
        assert DIRECTION_DELTAS[Direction.EAST] == (1, 0)
        assert DIRECTION_DELTAS[Direction.WEST] == (-1, 0)

    def test_diagonal_deltas(self):
        """Diagonal direction deltas are correct."""
        assert DIRECTION_DELTAS[Direction.NORTHEAST] == (1, -1)
        assert DIRECTION_DELTAS[Direction.SOUTHEAST] == (1, 1)
        assert DIRECTION_DELTAS[Direction.SOUTHWEST] == (-1, 1)
        assert DIRECTION_DELTAS[Direction.NORTHWEST] == (-1, -1)


class TestDiagonalComponents:
    """Tests for diagonal component mapping."""

    def test_only_diagonals_have_components(self):
        """Only diagonal directions have components."""
        assert len(DIAGONAL_COMPONENTS) == 4
        for direction in DIAGONAL_COMPONENTS:
            assert direction in {
                Direction.NORTHEAST,
                Direction.SOUTHEAST,
                Direction.SOUTHWEST,
                Direction.NORTHWEST,
            }

    def test_northeast_components(self):
        """NORTHEAST decomposes to NORTH and EAST."""
        assert DIAGONAL_COMPONENTS[Direction.NORTHEAST] == (
            Direction.NORTH,
            Direction.EAST,
        )

    def test_southeast_components(self):
        """SOUTHEAST decomposes to SOUTH and EAST."""
        assert DIAGONAL_COMPONENTS[Direction.SOUTHEAST] == (
            Direction.SOUTH,
            Direction.EAST,
        )

    def test_southwest_components(self):
        """SOUTHWEST decomposes to SOUTH and WEST."""
        assert DIAGONAL_COMPONENTS[Direction.SOUTHWEST] == (
            Direction.SOUTH,
            Direction.WEST,
        )

    def test_northwest_components(self):
        """NORTHWEST decomposes to NORTH and WEST."""
        assert DIAGONAL_COMPONENTS[Direction.NORTHWEST] == (
            Direction.NORTH,
            Direction.WEST,
        )


class TestMoveIntent:
    """Tests for MoveIntent class."""

    def test_move_intent_creation(self):
        """MoveIntent can be created."""
        intent = MoveIntent(entity_id="player1", direction=Direction.NORTH)
        assert intent.entity_id == "player1"
        assert intent.direction == Direction.NORTH

    def test_move_intent_immutable(self):
        """MoveIntent is immutable."""
        intent = MoveIntent(entity_id="player1", direction=Direction.NORTH)
        with pytest.raises(ValidationError):
            intent.direction = Direction.SOUTH  # type: ignore
