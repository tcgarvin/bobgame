"""Pytest fixtures for the jev_agent tests."""

from __future__ import annotations

import pytest

from agents.jev_agent.worldmodel import WorldModel

from helpers import FakeJevClient, make_entity, make_observation


@pytest.fixture
def fake_jev() -> FakeJevClient:
    """A scriptable Jev stand-in."""
    return FakeJevClient()


@pytest.fixture
def model() -> WorldModel:
    """A world model for `ada`, primed with one observation at the origin."""
    world_model = WorldModel("ada")
    world_model.update(make_observation(1, make_entity("ada", (10, 10))))
    return world_model
