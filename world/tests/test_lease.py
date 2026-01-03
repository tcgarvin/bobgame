"""Tests for lease management."""

import time
from unittest.mock import patch

import pytest

from world.lease import Lease, LeaseManager


class TestLease:
    """Tests for the Lease dataclass."""

    def test_lease_creation(self) -> None:
        lease = Lease(
            lease_id="test-lease",
            entity_id="entity-1",
            controller_id="controller-1",
            acquired_at_ms=1000,
            expires_at_ms=2000,
        )
        assert lease.lease_id == "test-lease"
        assert lease.entity_id == "entity-1"
        assert lease.controller_id == "controller-1"

    def test_lease_not_expired_before_deadline(self) -> None:
        lease = Lease(
            lease_id="test-lease",
            entity_id="entity-1",
            controller_id="controller-1",
            acquired_at_ms=1000,
            expires_at_ms=2000,
        )
        assert not lease.is_expired(current_time_ms=1500)

    def test_lease_expired_after_deadline(self) -> None:
        lease = Lease(
            lease_id="test-lease",
            entity_id="entity-1",
            controller_id="controller-1",
            acquired_at_ms=1000,
            expires_at_ms=2000,
        )
        assert lease.is_expired(current_time_ms=2500)

    def test_lease_expired_at_exact_deadline(self) -> None:
        lease = Lease(
            lease_id="test-lease",
            entity_id="entity-1",
            controller_id="controller-1",
            acquired_at_ms=1000,
            expires_at_ms=2000,
        )
        assert lease.is_expired(current_time_ms=2000)


class TestLeaseManager:
    """Tests for the LeaseManager."""

    def test_acquire_lease(self) -> None:
        manager = LeaseManager()
        result = manager.acquire("entity-1", "controller-1")

        assert isinstance(result, Lease)
        assert result.entity_id == "entity-1"
        assert result.controller_id == "controller-1"
        assert result.lease_id is not None

    def test_acquire_lease_already_leased_different_controller(self) -> None:
        manager = LeaseManager()
        manager.acquire("entity-1", "controller-1")

        result = manager.acquire("entity-1", "controller-2")

        assert isinstance(result, str)
        assert "already leased" in result

    def test_acquire_lease_same_controller_renews(self) -> None:
        manager = LeaseManager()
        first = manager.acquire("entity-1", "controller-1")
        assert isinstance(first, Lease)

        second = manager.acquire("entity-1", "controller-1")
        assert isinstance(second, Lease)
        assert second.lease_id == first.lease_id

    def test_renew_lease(self) -> None:
        manager = LeaseManager()
        lease = manager.acquire("entity-1", "controller-1")
        assert isinstance(lease, Lease)

        original_expires = lease.expires_at_ms

        # Wait a tiny bit and renew
        time.sleep(0.01)
        renewed = manager.renew(lease.lease_id)

        assert isinstance(renewed, Lease)
        assert renewed.expires_at_ms > original_expires

    def test_renew_nonexistent_lease(self) -> None:
        manager = LeaseManager()
        result = manager.renew("nonexistent-lease")

        assert isinstance(result, str)
        assert "not found" in result

    def test_release_lease(self) -> None:
        manager = LeaseManager()
        lease = manager.acquire("entity-1", "controller-1")
        assert isinstance(lease, Lease)

        success = manager.release(lease.lease_id)
        assert success

        # Should be able to acquire again
        new_lease = manager.acquire("entity-1", "controller-2")
        assert isinstance(new_lease, Lease)

    def test_release_nonexistent_lease(self) -> None:
        manager = LeaseManager()
        success = manager.release("nonexistent-lease")
        assert not success

    def test_get_lease(self) -> None:
        manager = LeaseManager()
        lease = manager.acquire("entity-1", "controller-1")
        assert isinstance(lease, Lease)

        retrieved = manager.get_lease(lease.lease_id)
        assert retrieved is not None
        assert retrieved.lease_id == lease.lease_id

    def test_get_lease_nonexistent(self) -> None:
        manager = LeaseManager()
        retrieved = manager.get_lease("nonexistent-lease")
        assert retrieved is None

    def test_get_lease_for_entity(self) -> None:
        manager = LeaseManager()
        lease = manager.acquire("entity-1", "controller-1")
        assert isinstance(lease, Lease)

        retrieved = manager.get_lease_for_entity("entity-1")
        assert retrieved is not None
        assert retrieved.entity_id == "entity-1"

    def test_get_lease_for_entity_none(self) -> None:
        manager = LeaseManager()
        retrieved = manager.get_lease_for_entity("entity-1")
        assert retrieved is None

    def test_is_valid_lease(self) -> None:
        manager = LeaseManager()
        lease = manager.acquire("entity-1", "controller-1")
        assert isinstance(lease, Lease)

        assert manager.is_valid_lease(lease.lease_id, "entity-1")
        assert not manager.is_valid_lease(lease.lease_id, "entity-2")
        assert not manager.is_valid_lease("wrong-lease", "entity-1")

    def test_cleanup_expired(self) -> None:
        manager = LeaseManager(lease_duration_ms=10)  # Very short lease
        lease = manager.acquire("entity-1", "controller-1")
        assert isinstance(lease, Lease)

        # Wait for expiry
        time.sleep(0.02)

        cleaned = manager.cleanup_expired()
        assert cleaned == 1

        # Lease should be gone
        assert manager.get_lease(lease.lease_id) is None

    def test_expired_lease_allows_new_acquisition(self) -> None:
        manager = LeaseManager(lease_duration_ms=10)  # Very short lease
        lease = manager.acquire("entity-1", "controller-1")
        assert isinstance(lease, Lease)

        # Wait for expiry
        time.sleep(0.02)

        # New controller can acquire
        new_lease = manager.acquire("entity-1", "controller-2")
        assert isinstance(new_lease, Lease)
        assert new_lease.controller_id == "controller-2"
