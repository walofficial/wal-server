import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from ment_api.config import settings
from ment_api.persistence.mongo_client import check_mongo_connection
from ment_api.services.pub_sub_service import initialize_subscriber, close_subscriber
from ment_api.services.verification_service import video_transcode_callback
from ment_api.workers.message_state_worker import init_message_state_worker
from ment_api.persistence.mongo import initialize_db
from ment_api.routes.chat import broadcast_feed_items
import asyncio

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(local_app: FastAPI):
    await check_mongo_connection()
    init_message_state_worker()

    await initialize_db()
    try:
        subscriber = await initialize_subscriber(
            settings.gcp_project_id,
            settings.pub_sub_transcoder_topic_id,
            settings.pub_sub_transcoder_subscription_id,
            video_transcode_callback,
        )
    except Exception:
        logger.error("Something went wrong during pub sub", exc_info=True)
        return
    # asyncio.create_task(broadcast_feed_items())

    yield
    logger.info("Application shutdown: closing Pub/Sub client")
    await close_subscriber(subscriber)
