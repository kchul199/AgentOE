"""Shared pytest fixtures."""

from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from app.core.auth import create_access_token
from app.main import app


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture
def auth_headers():
    token = create_access_token("test-tenant", "test-client", ["operator"])
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def mock_session_repo():
    return AsyncMock()
