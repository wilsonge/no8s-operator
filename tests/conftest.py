"""Pytest configuration and fixtures."""

import pytest
import asyncio
from unittest.mock import AsyncMock, MagicMock

from auth import AuthManager


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
        "finalizers": ["github_actions"],
    }


@pytest.fixture
def mock_auth_manager():
    """An AuthManager instance pre-configured for tests."""
    return AuthManager(
        jwt_secret_key="test-jwt-secret-key-for-unit-tests-only", jwt_expiry_hours=1
    )


@pytest.fixture
def admin_user_payload():
    """JWT payload dict for an admin user."""
    return {
        "sub": "1",
        "id": 1,
        "username": "admin",
        "is_admin": True,
        "source": "manual",
    }


@pytest.fixture
def viewer_user_payload():
    """JWT payload dict for a non-admin user."""
    return {
        "sub": "2",
        "id": 2,
        "username": "viewer",
        "is_admin": False,
        "source": "manual",
    }


@pytest.fixture
def sample_user():
    """Sample user data dict for testing."""
    return {
        "id": 1,
        "username": "testuser",
        "email": "test@example.com",
        "display_name": "Test User",
        "source": "manual",
        "is_admin": False,
        "status": "active",
        "password_hash": None,
        "ldap_dn": None,
        "ldap_uid": None,
        "created_at": None,
        "updated_at": None,
        "last_login_at": None,
        "last_synced_at": None,
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
