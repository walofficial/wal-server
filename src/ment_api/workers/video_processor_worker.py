import asyncio
import logging
import os
import tempfile
import time

import yt_dlp
from bson import ObjectId
from google.pubsub_v1 import ReceivedMessage

from langfuse import observe
from ment_api.common.custom_object_id import CustomObjectId
from ment_api.events.video_processor_event import VideoProcessorEvent
from ment_api.persistence import mongo
from ment_api.services.gcs_service import build_audio_blob_path, upload_file_to_gcs
from ment_api.services.google_storage_service import build_gcs_uri, check_blob_exists
from ment_api.services.notification_service import send_notification
from ment_api.services.verification_service import find_processed_youtube_id
from ment_api.services.video_processing_service import (
    generate_audio_transcript,
    generate_summary_from_transcript,
)
from ment_api.services.youtube_utils import (
    get_youtube_info_async,
)
from ment_api.services.news_service import publish_check_fact

logger = logging.getLogger(__name__)

# --- GCS Configuration (Should ideally come from settings) ---
GCS_BUCKET_NAME = "ment-verification"
# -----------------------------------------------------------

# Maximum duration in seconds for video processing
MAX_VIDEO_DURATION = 4000  # 15 minutes in seconds

# Maximum timeout for the entire video processing callback (10 minutes)
CALLBACK_TIMEOUT = 600  # 10 minutes in seconds


# --- yt-dlp Logging and Hooks ---
class YtdlpLogger:
    def debug(self, msg):
        if msg.startswith("[debug] "):
            pass
        else:
            self.info(msg)

    def info(self, msg):
        logger.info(msg)

    def warning(self, msg):
        logger.warning(msg)

    def error(self, msg):
        logger.error(msg)


def progress_hook(d):
    if d["status"] == "downloading":
        pass


# -----------------------------------------------------------


async def download_youtube_content(
    url: str, output_dir: str, start_time: int, end_time: int
) -> dict:
    cookies_path = "cookies.txt"
    """Downloads audio from YouTube URL and saves in the specified directory."""
    output_id = str(ObjectId())  # Unique ID
    audio_output_template = os.path.join(
        output_dir, f"youtube_audio_{output_id}.%(ext)s"
    )
    audio_format = "webm"

    # Download audio only
    audio_ydl_opts = {
        "format": "bestaudio/best",
        "outtmpl": audio_output_template,
        "logger": YtdlpLogger(),
        "progress_hooks": [progress_hook],
        "keepvideo": False,
    }

    if start_time and end_time:
        download_range = yt_dlp.utils.download_range_func(
            [], [[float(start_time), float(end_time)]]
        )
        audio_ydl_opts["download_ranges"] = download_range

    # Add cookies if the file exists
    if cookies_path and os.path.exists(cookies_path):
        audio_ydl_opts["cookiefile"] = cookies_path
        logger.info(f"Using cookies from: {cookies_path}")
    elif cookies_path:
        logger.warning(f"Cookie file specified but not found: {cookies_path}")

    result = {"audio_path": None}

    try:
        logger.info(f"Starting audio download for: {url}")

        # Download audio
        def download_audio_sync():
            with yt_dlp.YoutubeDL(audio_ydl_opts) as ydl:
                return ydl.download([url])

        error_code = await asyncio.to_thread(download_audio_sync)

        if error_code != 0:
            logger.error(f"Audio download failed with error code: {error_code}")
        else:
            audio_output_path = os.path.join(
                output_dir, f"youtube_audio_{output_id}.{audio_format}"
            )
            if os.path.exists(audio_output_path):
                logger.info(f"Successfully downloaded audio to: {audio_output_path}")
                result["audio_path"] = audio_output_path
            else:
                # Search just in case
                found_files = [
                    f
                    for f in os.listdir(output_dir)
                    if f.startswith(f"youtube_audio_{output_id}")
                    and os.path.isfile(os.path.join(output_dir, f))
                ]
                if found_files:
                    found_path = os.path.join(output_dir, found_files[0])
                    logger.warning(f"Found potential audio file: {found_path}")
                    result["audio_path"] = found_path

        return result

    except yt_dlp.utils.DownloadError as e:
        logger.error(f"yt-dlp Download Error: {e}")
        return result
    except Exception as e:
        logger.error(f"An unexpected error occurred during download: {e}")
        return result


@observe()
async def process_video(
    youtube_url: str,
    youtube_id: str,
    verification_id: CustomObjectId,
    external_user_id: str,
    video_title: str = None,
):
    """Process a video and generate transcript and summary."""
    logger.info(f"Processing video: {youtube_url}")

    # Build audio blob path to check if it already exists
    audio_blob_path = build_audio_blob_path(youtube_id)
    audio_gcs_uri = build_gcs_uri(audio_blob_path, GCS_BUCKET_NAME)

    # Check if audio blob already exists in GCS
    if check_blob_exists(audio_blob_path, GCS_BUCKET_NAME):
        logger.info(
            f"Audio blob already exists at {audio_gcs_uri}, skipping download and upload"
        )
    else:
        logger.info("Audio blob does not exist, downloading and uploading")

        with tempfile.TemporaryDirectory() as tmpdir:
            start_time = 0
            end_time = 3600
            # Download the video
            download_result = await download_youtube_content(
                youtube_url, tmpdir, start_time, end_time
            )

            local_audio_path = download_result["audio_path"]
            if not local_audio_path:
                logger.error("Failed to download audio")
                await mongo.verifications.update_one(
                    {"_id": verification_id},
                    {"$set": {"ai_video_summary_status": "FAILED"}},
                )
                return False

            # Upload to GCS
            uploaded_audio_gcs_uri = await upload_file_to_gcs(
                local_audio_path, audio_blob_path, GCS_BUCKET_NAME
            )

            if not uploaded_audio_gcs_uri:
                logger.error("Failed to upload audio to GCS")
                await mongo.verifications.update_one(
                    {"_id": verification_id},
                    {"$set": {"ai_video_summary_status": "FAILED"}},
                )
                return False

    # Generate transcript using the audio GCS URI
    transcript_result = await generate_audio_transcript(audio_gcs_uri)

    if not transcript_result:
        logger.error("Failed to generate transcript")
        await mongo.verifications.update_one(
            {"_id": verification_id},
            {"$set": {"ai_video_summary_status": "FAILED"}},
        )
        return False

    transcript = transcript_result["transcript"]

    # Generate the AI summary using the transcript
    ai_summary = await generate_summary_from_transcript(
        transcript=transcript,
        video_title=video_title,
    )

    # Update the verification document with the AI summary
    update_result = await mongo.verifications.update_one(
        {"_id": verification_id},
        {
            "$set": {
                "ai_video_summary": (ai_summary.model_dump() if ai_summary else None),
                "youtube_id": youtube_id,
            }
        },
    )

    if update_result.modified_count == 0:
        logger.error(f"Failed to update verification with ID {verification_id}")
        return False

    # Send notification to the user
    await send_notification(
        external_user_id,
        "ვიდეოს ანალიზი დასრულდა",
        "ნახეთ",
        data={
            "type": "video_summary_completed",
            "verificationId": str(verification_id),
        },
    )

    # Publish to check fact service
    logger.info(f"Publishing check fact for {verification_id}")
    await publish_check_fact([verification_id])

    return True


@observe()
async def process_video_callback(message: ReceivedMessage) -> None:
    """
    Process a video processing request from pubsub subscription.
    This handles YouTube audio processing after download.
    Has a 10-minute timeout to prevent indefinite processing.

    Args:
        message: The pubsub message containing the VideoProcessorEvent
    """
    start_time = time.time()

    logger.info("Processing video callback")
    verification_id = None

    try:
        # Wrap the entire processing logic with a timeout
        await asyncio.wait_for(
            _process_video_callback_internal(message), timeout=CALLBACK_TIMEOUT
        )

        end_time = time.time()
        duration_ms = (end_time - start_time) * 1000
        logger.info(f"Video processing completed successfully in {duration_ms}ms")

    except asyncio.TimeoutError:
        end_time = time.time()
        duration_ms = (end_time - start_time) * 1000

        logger.warning(
            f"Video processing timed out after {CALLBACK_TIMEOUT} seconds (duration: {duration_ms}ms)"
        )

        # Try to extract verification info for cleanup if possible
        try:
            video_event = VideoProcessorEvent.model_validate_json(
                message.message.data.decode()
            )
            verification_id = ObjectId(video_event.verification_id)

            # Update verification status to failed due to timeout
            await mongo.verifications.update_one(
                {"_id": verification_id},
                {"$set": {"ai_video_summary_status": "FAILED"}},
            )

        except Exception as cleanup_error:
            logger.error(f"Error during timeout cleanup: {cleanup_error}")

        # Return success even on timeout as requested
        return

    except Exception as e:
        end_time = time.time()
        duration_ms = (end_time - start_time) * 1000

        logger.error(f"Error in video processing callback: {e}", exc_info=True)
        raise


async def _process_video_callback_internal(message: ReceivedMessage) -> None:
    """
    Internal function containing the actual video processing logic.
    Separated to allow timeout wrapper in the main callback function.
    """
    verification_id = None
    youtube_url = None
    external_user_id = None

    # Parse the message
    video_event = VideoProcessorEvent.model_validate_json(message.message.data.decode())
    verification_id = ObjectId(video_event.verification_id)
    youtube_url = video_event.youtube_url
    external_user_id = video_event.external_user_id
    video_title = video_event.video_title

    logger.info(f"Processing video: {youtube_url} for verification: {verification_id}")

    # Check if this video has already been processed
    youtube_info = await get_youtube_info_async(youtube_url)
    youtube_id = youtube_info["id"]

    existing_verification = await find_processed_youtube_id(youtube_id)
    if existing_verification:
        logger.info(f"Video {youtube_id} already processed, skipping")
        await mongo.verifications.update_one(
            {"_id": verification_id},
            {
                "$set": {
                    "ai_video_summary_status": "COMPLETED",
                    "metadata_status": "COMPLETED",
                    "youtube_id": youtube_id,
                    "ai_video_summary": existing_verification["ai_video_summary"],
                }
            },
        )
        await publish_check_fact([verification_id])
        return
    verification = await mongo.verifications.find_one(
        {"_id": verification_id},
        {"_id": 1},
    )
    if verification.get("ai_video_summary_status") == "PENDING":
        logger.info(f"Video {youtube_id} already being processed, skipping")
        return

    await mongo.verifications.update_one(
        {"_id": verification_id},
        {
            "$set": {
                "metadata_status": "COMPLETED",
                "ai_video_summary_status": "PENDING",
            }
        },
    )
    # Get video duration and check if it's within limits
    duration_seconds = youtube_info["duration_seconds"]
    user = await mongo.users.find_one(
        {"external_user_id": external_user_id},
        {"_id": 1},
    )
    can_summarify = user.get("can_summarify", False)

    # Set video duration limit based on user permissions
    video_duration_limit = (
        7200 if can_summarify else MAX_VIDEO_DURATION
    )  # 1 hour vs 15 minutes

    if duration_seconds > video_duration_limit:
        if not user:
            logger.error(f"User {external_user_id} not found")
            return

        logger.error(
            f"Video duration {duration_seconds}s exceeds maximum allowed duration of {video_duration_limit}s"
        )
        await mongo.verifications.update_one(
            {"_id": verification_id},
            {"$set": {"ai_video_summary_status": "FAILED"}},
        )
        await send_notification(
            external_user_id,
            "ვიდეო ძალიან გრძელია",
            "მხოლოდ 15 წუთამდე ვიდეოები შეიძლება დაამუშავოთ",
            data={
                "type": "video_too_long",
                "verificationId": str(verification_id),
            },
        )
        return

    logger.info(
        f"Video duration {duration_seconds}s is within limits, processing video"
    )

    # Process the video
    success = await process_video(
        youtube_url,
        youtube_id,
        verification_id,
        external_user_id,
        video_title,
    )

    logger.info(f"Video processing completed with success: {success}")
