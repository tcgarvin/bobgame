"""Tests for the Inventory system."""

import pytest

from world.state import Inventory


class TestInventory:
    """Tests for Inventory operations."""

    def test_empty_inventory(self) -> None:
        """Empty inventory has zero count for any item."""
        inv = Inventory()
        assert inv.count("berry") == 0
        assert inv.count("wood") == 0
        assert not inv.has("berry")

    def test_add_items(self) -> None:
        """Adding items increases count."""
        inv = Inventory().add("berry", 3)
        assert inv.count("berry") == 3
        assert inv.has("berry", 3)
        assert not inv.has("berry", 4)

    def test_add_multiple_types(self) -> None:
        """Can add different item types."""
        inv = Inventory().add("berry", 2).add("wood", 5)
        assert inv.count("berry") == 2
        assert inv.count("wood") == 5

    def test_add_increments_existing(self) -> None:
        """Adding to existing item type increments count."""
        inv = Inventory().add("berry", 2).add("berry", 3)
        assert inv.count("berry") == 5

    def test_remove_items(self) -> None:
        """Removing items decreases count."""
        inv = Inventory().add("berry", 5).remove("berry", 2)
        assert inv.count("berry") == 3

    def test_remove_all_items(self) -> None:
        """Removing all items results in zero count."""
        inv = Inventory().add("berry", 3).remove("berry", 3)
        assert inv.count("berry") == 0
        assert not inv.has("berry")

    def test_remove_insufficient_raises(self) -> None:
        """Removing more than available raises ValueError."""
        inv = Inventory().add("berry", 2)
        with pytest.raises(ValueError, match="Cannot remove 3 berry"):
            inv.remove("berry", 3)

    def test_remove_nonexistent_raises(self) -> None:
        """Removing nonexistent item raises ValueError."""
        inv = Inventory()
        with pytest.raises(ValueError, match="Cannot remove 1 berry"):
            inv.remove("berry", 1)

    def test_immutability(self) -> None:
        """Add and remove return new instances, don't mutate original."""
        inv1 = Inventory()
        inv2 = inv1.add("berry", 1)
        inv3 = inv2.remove("berry", 1)

        assert inv1.count("berry") == 0
        assert inv2.count("berry") == 1
        assert inv3.count("berry") == 0

    def test_has_with_amount(self) -> None:
        """has() checks for specific amounts."""
        inv = Inventory().add("berry", 3)
        assert inv.has("berry", 1)
        assert inv.has("berry", 2)
        assert inv.has("berry", 3)
        assert not inv.has("berry", 4)

    def test_default_add_amount(self) -> None:
        """Default add amount is 1."""
        inv = Inventory().add("berry")
        assert inv.count("berry") == 1

    def test_default_remove_amount(self) -> None:
        """Default remove amount is 1."""
        inv = Inventory().add("berry", 3).remove("berry")
        assert inv.count("berry") == 2
