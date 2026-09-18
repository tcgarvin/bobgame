"""Tests for AgentStatusService."""

from typing import Any

import pytest

from world import world_pb2 as pb
from world.lease import Lease, LeaseManager
from world.services.status_service import AgentStatusServiceServicer


@pytest.fixture
def lease_manager() -> LeaseManager:
    return LeaseManager()


@pytest.fixture
def lease(lease_manager: LeaseManager) -> Lease:
    acquired = lease_manager.acquire("ada", "runner-1")
    assert isinstance(acquired, Lease)
    return acquired


@pytest.fixture
def broadcasts() -> list[dict[str, Any]]:
    return []


@pytest.fixture
def service(
    lease_manager: LeaseManager, broadcasts: list[dict[str, Any]]
) -> AgentStatusServiceServicer:
    return AgentStatusServiceServicer(lease_manager, broadcasts.append)


def test_accepts_and_broadcasts_a_report(
    service: AgentStatusServiceServicer,
    lease: Lease,
    broadcasts: list[dict[str, Any]],
) -> None:
    ack = service.ReportStatus(
        pb.AgentStatusReport(
            lease_id=lease.lease_id,
            entity_id="ada",
            mode="stint",
            brief="gather wood",
            planner_thought="we need an axe",
            stint_json='{"tick": 4, "action": "extract:tree_9"}',
            cost_json='{"total_usd": 0.0074, "planner_usd": 0.0049}',
        ),
        None,
    )

    assert ack.accepted is True
    assert broadcasts == [
        {
            "type": "agent_status",
            "entity_id": "ada",
            "mode": "stint",
            "brief": "gather wood",
            "planner_thought": "we need an axe",
            "stint": {"tick": 4, "action": "extract:tree_9"},
            "cost": {"total_usd": 0.0074, "planner_usd": 0.0049},
        }
    ]


def test_empty_stint_json_becomes_null(
    service: AgentStatusServiceServicer,
    lease: Lease,
    broadcasts: list[dict[str, Any]],
) -> None:
    ack = service.ReportStatus(
        pb.AgentStatusReport(lease_id=lease.lease_id, entity_id="ada", mode="planning"),
        None,
    )

    assert ack.accepted is True
    assert broadcasts[0]["stint"] is None
    assert broadcasts[0]["cost"] is None


def test_rejects_invalid_lease(
    service: AgentStatusServiceServicer, broadcasts: list[dict[str, Any]]
) -> None:
    ack = service.ReportStatus(
        pb.AgentStatusReport(lease_id="nope", entity_id="ada", mode="idle"), None
    )

    assert ack.accepted is False
    assert broadcasts == []


def test_rejects_lease_for_another_entity(
    service: AgentStatusServiceServicer,
    lease: Lease,
    broadcasts: list[dict[str, Any]],
) -> None:
    ack = service.ReportStatus(
        pb.AgentStatusReport(lease_id=lease.lease_id, entity_id="bram", mode="idle"),
        None,
    )

    assert ack.accepted is False
    assert broadcasts == []


def test_rejects_invalid_stint_json(
    service: AgentStatusServiceServicer,
    lease: Lease,
    broadcasts: list[dict[str, Any]],
) -> None:
    ack = service.ReportStatus(
        pb.AgentStatusReport(
            lease_id=lease.lease_id,
            entity_id="ada",
            mode="stint",
            stint_json="{not json",
        ),
        None,
    )

    assert ack.accepted is False
    assert broadcasts == []


def test_invalid_cost_json_is_dropped_but_report_goes_out(
    service: AgentStatusServiceServicer,
    lease: Lease,
    broadcasts: list[dict[str, Any]],
) -> None:
    ack = service.ReportStatus(
        pb.AgentStatusReport(
            lease_id=lease.lease_id,
            entity_id="ada",
            mode="stint",
            brief="gather wood",
            cost_json="{not json",
        ),
        None,
    )

    assert ack.accepted is True
    assert len(broadcasts) == 1
    assert broadcasts[0]["cost"] is None
    assert broadcasts[0]["brief"] == "gather wood"
