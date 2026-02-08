"""Pytest configuration and fixtures."""

import pytest
import asyncio
from unittest.mock import AsyncMock, MagicMock


@pytest.fixture
def event_loop():
    """Create an event loop for async tests."""
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest.fixture
def mock_pool():
    """Create a mock asyncpg pool."""
    pool = AsyncMock()
    pool.acquire = MagicMock()
    return pool


@pytest.fixture
def mock_connection():
    """Create a mock asyncpg connection."""
    conn = AsyncMock()
    return conn


@pytest.fixture
def sample_resource():
    """Sample resource data for testing."""
    return {
        "id": 1,
        "name": "test-resource",
        "resource_type_name": "GitHubWorkflow",
        "resource_type_version": "v1",
        "action_plugin": "github_actions",
        "spec": {"owner": "test", "repo": "test-repo", "workflow": "test.yml"},
        "plugin_config": {},
        "metadata": {},
        "outputs": {},
        "status": "pending",
        "status_message": None,
        "generation": 1,
        "observed_generation": 0,
        "spec_hash": "abc123",
        "retry_count": 0,
        "last_reconcile_time": None,
        "next_reconcile_time": None,
    }


@pytest.fixture
def sample_resource_type():
    """Sample resource type data for testing."""
    return {
        "id": 1,
        "name": "GitHubWorkflow",
        "version": "v1",
        "description": "GitHub Actions workflow trigger",
        "schema": {
            "type": "object",
            "required": ["owner", "repo", "workflow"],
            "properties": {
                "owner": {"type": "string"},
                "repo": {"type": "string"},
                "workflow": {"type": "string"},
            },
        },
        "status": "active",
        "metadata": {},
    }
