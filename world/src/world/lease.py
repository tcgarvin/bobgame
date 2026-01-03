"""Lease management for entity control."""

import time
import uuid
from dataclasses import dataclass, field

import structlog

logger = structlog.get_logger()


# Default lease duration: 30 seconds
DEFAULT_LEASE_DURATION_MS = 30_000


@dataclass
class Lease:
    """An active lease for controlling an entity."""

    lease_id: str
    entity_id: str
    controller_id: str
    acquired_at_ms: int
    expires_at_ms: int

    def is_expired(self, current_time_ms: int | None = None) -> bool:
        """Check if this lease has expired."""
        if current_time_ms is None:
            current_time_ms = int(time.time() * 1000)
        return current_time_ms >= self.expires_at_ms


@dataclass
class LeaseManager:
    """Manages entity control leases with expiration."""

    lease_duration_ms: int = DEFAULT_LEASE_DURATION_MS

    # Active leases by lease_id
    _leases: dict[str, Lease] = field(default_factory=dict)
    # Entity to lease mapping for quick lookup
    _entity_leases: dict[str, str] = field(default_factory=dict)

    def acquire(self, entity_id: str, controller_id: str) -> Lease | str:
        """Acquire a lease for an entity.

        Returns the new Lease on success, or an error string on failure.
        """
        now_ms = int(time.time() * 1000)

        # Check for existing lease
        existing_lease_id = self._entity_leases.get(entity_id)
        if existing_lease_id:
            existing = self._leases.get(existing_lease_id)
            if existing and not existing.is_expired(now_ms):
                if existing.controller_id == controller_id:
                    # Same controller re-acquiring - just renew
                    return self._renew_lease(existing, now_ms)
                logger.debug(
                    "lease_acquire_rejected",
                    entity_id=entity_id,
                    existing_controller=existing.controller_id,
                )
                return f"entity already leased by {existing.controller_id}"
            # Expired lease, clean it up
            self._remove_lease(existing_lease_id)

        # Create new lease
        lease = Lease(
            lease_id=str(uuid.uuid4()),
            entity_id=entity_id,
            controller_id=controller_id,
            acquired_at_ms=now_ms,
            expires_at_ms=now_ms + self.lease_duration_ms,
        )

        self._leases[lease.lease_id] = lease
        self._entity_leases[entity_id] = lease.lease_id

        logger.info(
            "lease_acquired",
            lease_id=lease.lease_id,
            entity_id=entity_id,
            controller_id=controller_id,
            expires_at_ms=lease.expires_at_ms,
        )

        return lease

    def renew(self, lease_id: str) -> Lease | str:
        """Renew an existing lease.

        Returns the renewed Lease on success, or an error string on failure.
        """
        now_ms = int(time.time() * 1000)

        lease = self._leases.get(lease_id)
        if not lease:
            return "lease not found"

        if lease.is_expired(now_ms):
            self._remove_lease(lease_id)
            return "lease expired"

        return self._renew_lease(lease, now_ms)

    def release(self, lease_id: str) -> bool:
        """Release a lease.

        Returns True if the lease was released, False if not found.
        """
        if lease_id not in self._leases:
            return False

        self._remove_lease(lease_id)
        logger.info("lease_released", lease_id=lease_id)
        return True

    def get_lease(self, lease_id: str) -> Lease | None:
        """Get a lease by ID, or None if not found or expired."""
        now_ms = int(time.time() * 1000)
        lease = self._leases.get(lease_id)

        if lease and lease.is_expired(now_ms):
            self._remove_lease(lease_id)
            return None

        return lease

    def get_lease_for_entity(self, entity_id: str) -> Lease | None:
        """Get the active lease for an entity, or None."""
        lease_id = self._entity_leases.get(entity_id)
        if not lease_id:
            return None
        return self.get_lease(lease_id)

    def is_valid_lease(self, lease_id: str, entity_id: str) -> bool:
        """Check if a lease is valid for the given entity."""
        lease = self.get_lease(lease_id)
        return lease is not None and lease.entity_id == entity_id

    def cleanup_expired(self) -> int:
        """Remove all expired leases. Returns count of removed leases."""
        now_ms = int(time.time() * 1000)
        expired = [
            lease_id
            for lease_id, lease in self._leases.items()
            if lease.is_expired(now_ms)
        ]

        for lease_id in expired:
            self._remove_lease(lease_id)

        if expired:
            logger.debug("expired_leases_cleaned", count=len(expired))

        return len(expired)

    def _renew_lease(self, lease: Lease, now_ms: int) -> Lease:
        """Internal: renew a lease's expiry time."""
        new_expires = now_ms + self.lease_duration_ms

        # Create updated lease (Lease is a dataclass, not frozen)
        renewed = Lease(
            lease_id=lease.lease_id,
            entity_id=lease.entity_id,
            controller_id=lease.controller_id,
            acquired_at_ms=lease.acquired_at_ms,
            expires_at_ms=new_expires,
        )

        self._leases[lease.lease_id] = renewed

        logger.debug(
            "lease_renewed",
            lease_id=lease.lease_id,
            expires_at_ms=new_expires,
        )

        return renewed

    def _remove_lease(self, lease_id: str) -> None:
        """Internal: remove a lease from all indices."""
        lease = self._leases.pop(lease_id, None)
        if lease:
            self._entity_leases.pop(lease.entity_id, None)
