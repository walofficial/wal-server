import logging
from typing import Optional

from pymongo import AsyncMongoClient
from pymongo.asynchronous.database import AsyncDatabase

from ment_api.configurations.config import settings

logger = logging.getLogger(__name__)

# Don't create the client at import time - this is the fork-safety issue
_client: Optional[AsyncMongoClient] = None
db: Optional[AsyncDatabase] = None


async def initialize_mongo_client() -> None:
    global _client
    global db
    if _client is None:
        _client = AsyncMongoClient(settings.mongodb_uri, tz_aware=True)
    if db is None:
        db = _client[settings.mongodb_db_name]

    await check_mongo_connection()


async def check_mongo_connection() -> None:
    """
    Check MongoDB connection by pinging the server.

    Raises:
        ConnectionError: If the ping command fails or response is invalid
        pymongo.errors.*: Original MongoDB exceptions for specific error handling
    """
    response = await _client.admin.command("ping")
    is_ok = response.get("ok") in [1, True, 1.0]

    if not is_ok:
        logger.error(f"MongoDB ping returned invalid response: {response}")
        raise ConnectionError(f"MongoDB ping failed: invalid response {response}")

    logger.info(f"MongoDB connection verified: {response}")


async def close_mongo_client() -> None:
    global _client
    global db
    if _client is not None:
        await _client.close()
        _client = None
        db = None
