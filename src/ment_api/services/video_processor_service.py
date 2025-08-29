import logging
from bson import ObjectId

from ment_api.configurations.config import settings
from ment_api.events.video_processor_event import VideoProcessorEvent
from ment_api.services.pub_sub_service import publish_message

logger = logging.getLogger(__name__)


async def publish_video_processor_request(
    verification_id: ObjectId,
    youtube_url: str,
    external_user_id: str,
    video_title: str,
) -> None:
    """
    Publish a request to process a YouTube video for a verification using Gemini API.

    Args:
        verification_id: ID of the verification document
        youtube_url: URL of the YouTube video
        external_user_id: ID of the user who created the verification
        video_title: Title of the YouTube video
    """
    try:
        data = (
            VideoProcessorEvent(
                verification_id=str(verification_id),
                youtube_url=youtube_url,
                external_user_id=external_user_id,
                video_title=video_title,
            )
            .model_dump_json()
            .encode()
        )

        await publish_message(
            settings.gcp_project_id,
            settings.pub_sub_video_processor_topic_id,
            data,
            retry_timeout=60.0,
        )

        logger.info(
            f"Published video processor request for verification {verification_id}"
        )
    except Exception as e:
        logger.error(f"Error publishing video processor request: {e}", exc_info=True)
