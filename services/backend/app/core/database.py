"""MongoDB Motor async database connection management."""
from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase

from app.core.config import settings

_client: AsyncIOMotorClient | None = None


async def init_db() -> None:
    """Initialize MongoDB connection."""
    global _client
    _client = AsyncIOMotorClient(
        settings.MONGODB_URI,
        serverSelectionTimeoutMS=5000,
        connectTimeoutMS=5000,
    )
    # Verify connection
    await _client.admin.command("ping")


async def close_db() -> None:
    """Close MongoDB connection."""
    global _client
    if _client:
        _client.close()
        _client = None


def get_database() -> AsyncIOMotorDatabase:
    """Get the AgentOE database instance."""
    if _client is None:
        raise RuntimeError("Database not initialized. Call init_db() first.")
    return _client[settings.MONGODB_DB_NAME]
