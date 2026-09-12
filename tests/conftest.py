import os
from unittest.mock import AsyncMock, patch

import pytest

from financial_ai.config import get_settings

VALID_SECRETS = {
    "POSTGRES_PASSWORD": os.getenv("POSTGRES_PASSWORD", "finai-dev-password-change-in-prod"),
    "REDIS_PASSWORD": os.getenv("REDIS_PASSWORD", "redis-dev-password-change-in-prod"),
    "APP_ENV": "testing"
}

@pytest.fixture(autouse=True)
def clear_settings_cache():
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()
    
@pytest.fixture(autouse=True)
def mock_tiktoken():
    """Prevent tiktoken from trying to download bpe files during unit tests."""
    with patch("tiktoken.get_encoding") as mock_get:
        mock_encoding = mock_get.return_value
        mock_encoding.encode.return_value = [1, 2, 3]
        mock_encoding.decode.return_value = "mock decoded text"
        yield mock_get
    
@pytest.fixture
def test_env():
    with patch.dict(os.environ, VALID_SECRETS, clear=False):
        yield
        
@pytest.fixture
def mock_db_session():
    session = AsyncMock()
    session.execute = AsyncMock()
    session.commit = AsyncMock()
    session.rollback = AsyncMock()
    return session

@pytest.fixture
def mock_cache_client():
    client = AsyncMock()
    client.get = AsyncMock(return_value=None)
    client.set = AsyncMock(return_value=True)
    client.delete = AsyncMock(return_value=True)
    client.health_check = AsyncMock(return_value={"status": "healthy"})
    return client

@pytest.fixture
def sample_embedding():
    return [0,0] * 384

@pytest.fixture
def sample_chunk_text():
    return (
        "Apple Inc. reported total net sales of $391.0 billion for fiscal year 2024, "
        "representing a 2% increase compared to fiscal year 2023. The Company's "
        "Services segment achieved record revenue of $96.2 billion."
    )

