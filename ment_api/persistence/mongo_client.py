import logging
from motor.motor_asyncio import AsyncIOMotorClient
from ment_api.config import settings

logger = logging.getLogger(__name__)

client = AsyncIOMotorClient(settings.mongodb_uri, tz_aware=True)
db = client[settings.mongodb_db_name]


async def check_mongo_connection() -> None:
    await client.admin.command("ismaster")


def check_mongo_connection_sync() -> None:
    client.admin.command("ismaster")


async def create_chat_room_index() -> None:
    await db.chat_rooms.create_index(
        [("participants", 1)],
        unique=True,
        partialFilterExpression={"participants": {"$exists": True}},
    )
