import logging
import time

from google.pubsub_v1 import ReceivedMessage

from ment_api.persistence import mongo
from ment_api.services.news_service import generate_news

logger = logging.getLogger(__name__)


async def process_news_callback(message: ReceivedMessage):
    logger.info(f"Processing news callback for {message.message.message_id}")

    mnt_media_user = await mongo.users.find_one({"username": "walmedia"})
    country_feed = await mongo.feeds.find_one({"feed_title": "რახდება"})

    if mnt_media_user is None:
        logger.error("Failed to find mnt_media user")
        return

    if country_feed is None:
        logger.error("Failed to find country_feed feed")
        return

    start_time = time.time()

    await generate_news(mnt_media_user["external_user_id"], country_feed["_id"])
    end_time = time.time()
    logger.info(f"News generation completed in {end_time - start_time} seconds")
