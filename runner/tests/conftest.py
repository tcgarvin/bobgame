"""Pytest configuration and fixtures for runner tests."""

import tempfile
from pathlib import Path

import pytest


@pytest.fixture
def temp_dir():
    """Create a temporary directory for test files."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


@pytest.fixture
def sample_config_toml():
    """Sample runner config as TOML string."""
    return """
[runner]
server = "localhost:50051"
connection_timeout_ms = 5000
auto_discover = true
log_dir = "test_logs"
max_restart_attempts = 3
initial_backoff_ms = 100
max_backoff_ms = 1000
backoff_multiplier = 2.0

[agents.default]
module = "agents.random_agent"
args = ["--eat-probability", "0.1"]

[agents.alice]
module = "agents.random_agent"
args = ["--eat-probability", "0.5"]
"""


@pytest.fixture
def config_file(temp_dir, sample_config_toml):
    """Create a temporary config file."""
    config_path = temp_dir / "test_config.toml"
    config_path.write_text(sample_config_toml)
    return config_path
