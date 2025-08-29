import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from ment_api.services.external_clients.langfuse_client import langfuse

from ment_api.configurations.config import settings
from ment_api.persistence.mongo import initialize_db
from ment_api.persistence.mongo_client import (
    close_mongo_client,
    initialize_mongo_client,
)
from ment_api.services.pub_sub_service import close_subscriber, initialize_subscriber
from ment_api.services.redis_service import get_redis_service
from ment_api.services.verification_service import video_transcode_callback
from ment_api.workers.check_fact_worker import process_check_fact_callback
from ment_api.workers.message_state_worker import (
    cleanup_message_state_task,
    init_message_state_task,
)
from ment_api.workers.news_worker import (
    process_news_callback,
)
from ment_api.workers.social_media_worker import process_social_media_callback
from ment_api.workers.translation_worker import process_translation_callback
from ment_api.workers.video_processor_worker import process_video_callback
from ment_api.workers.media_post_generator_worker import (
    process_media_post_generator_callback,
)

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(local_app: FastAPI):
    await initialize_mongo_client()
    await initialize_db()

    message_state_task = init_message_state_task()

    # Initialize subscribers and get their tasks
    (
        transcoder_task,
        news_task,
        check_fact_task,
        social_media_task,
        video_processor_task,
        translation_task,
        media_post_generator_task,
    ) = await asyncio.gather(
        initialize_subscriber(
            settings.gcp_project_id,
            settings.pub_sub_transcoder_topic_id,
            settings.pub_sub_transcoder_subscription_id,
            video_transcode_callback,
        ),
        initialize_subscriber(
            settings.gcp_project_id,
            settings.pub_sub_news_topic_id,
            settings.pub_sub_news_subscription_id,
            process_news_callback,
            True,
        ),
        initialize_subscriber(
            settings.gcp_project_id,
            settings.pub_sub_check_fact_topic_id,
            settings.pub_sub_check_fact_subscription_id,
            process_check_fact_callback,
            True,
        ),
        initialize_subscriber(
            settings.gcp_project_id,
            settings.pub_sub_social_media_scrape_topic_id,
            settings.pub_sub_social_media_scrape_subscription_id,
            process_social_media_callback,
            True,
        ),
        initialize_subscriber(
            settings.gcp_project_id,
            settings.pub_sub_video_processor_topic_id,
            settings.pub_sub_video_processor_subscription_id,
            process_video_callback,
            True,
        ),
        initialize_subscriber(
            settings.gcp_project_id,
            settings.pub_sub_translation_topic_id,
            settings.pub_sub_translation_subscription_id,
            process_translation_callback,
            True,
        ),
        initialize_subscriber(
            settings.gcp_project_id,
            settings.pub_sub_media_post_generator_topic_id,
            settings.pub_sub_media_post_generator_subscription_id,
            process_media_post_generator_callback,
            True,
        ),
    )

    yield

    # Shutdown code
    logger.info("Shutting down application")

    # Clean up pub/sub subscribers
    await asyncio.gather(
        close_subscriber(transcoder_task),
        close_subscriber(news_task),
        close_subscriber(check_fact_task),
        close_subscriber(social_media_task),
        close_subscriber(video_processor_task),
        close_subscriber(translation_task),
        close_subscriber(media_post_generator_task),
        return_exceptions=True,
    )

    langfuse.shutdown()

    # Clean up message state task
    cleanup_message_state_task(message_state_task)

    # Close Redis connections (both sync and async)
    try:
        redis_service = get_redis_service()
        # Close sync Redis client
        redis_service.close()
        # Close async Redis client
        await redis_service.aclose()
        logger.info("Redis connections (sync and async) closed successfully")
    except Exception as e:
        logger.error(f"Error closing Redis connections: {e}")

    # Close MongoDB connection
    await close_mongo_client()

    logger.info("Application shutdown complete")
