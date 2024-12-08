from datetime import datetime, timezone
import json
import logging
from typing import Callable, Awaitable, BinaryIO, Optional

from google.pubsub_v1 import ReceivedMessage

from ment_api.common.custom_object_id import CustomObjectId
from ment_api.config import settings
from ment_api.models.media_processing_result import MediaProcessingResult
from ment_api.models.verification_example_media import VerificationExampleMedia
from ment_api.models.verification_result import VerificationResult
from ment_api.models.verification_state import VerificationState
from ment_api.persistence import mongo
from ment_api.services.google_storage_service import download_verification_example_media
from ment_api.services.notification_service import (
    notify_companion_user,
    send_notification,
)
from ment_api.services.google_storage_service import build_public_video_mp4_path
from ment_api.services.external_clients.cloud_flare_client import upload_image

from ment_api.services.google_storage_service import (
    build_raw_video_path,
    build_transcoded_video_path,
    build_public_transcoded_video_path,
    build_public_video_mp4_path,
)
from ment_api.services.pub_sub_service import get_topic_path
from ment_api.services.transcoder_service import (
    create_transcode_job,
)
import vertexai

from vertexai.generative_models import GenerativeModel, Content, Part

logger = logging.getLogger(__name__)

vertexai.init(project=settings.gcp_project_id, location="us-central1")

# The Gemini 1.5 models are versatile and work with both text-only and multimodal prompts
model = GenerativeModel(
    model_name="gemini-1.5-pro-002",
    system_instruction="You are the best content detection expert which detects "
    "if the photo video satisfies the verification criteria (image or video)",
    generation_config={"response_mime_type": "application/json"},
)


async def execute_file_verification(
    file: BinaryIO,
    file_name: str,
    file_extension: str,
    mime_type: str,
    task_id: CustomObjectId,
    assignee_user_id: CustomObjectId,
    process_function: Callable[[dict], Awaitable[MediaProcessingResult]],
    extra_data: Optional[dict] = None,
) -> VerificationResult:
    # Fetch the task from the cache or database

    reqs = """0 - Make sure user content contains human speech like human saying something. Most likely all videos will have speech. Speech can be in any language.
              1 - Make sure inappropriate content is rejected like nudity, violence, etc.
              """

    content_type = "video" if mime_type.startswith("video/") else "image"

    prompt = f"""
    You are given a {content_type} and a task with specific verification criteria.
    Your job is to verify that the {content_type} satisfies all the criteria necessary for completing the task.

    Let's think step by step:
    1. Check if the {content_type} contains inappropriate content like nudity, violence, etc.
    2. Contect video can by anything it doesn't matter what user will capture

    Task Details:
    - **Verification Criteria**:

{reqs}

    The last file is the actual content that needs to be verified.

    Based on the provided {content_type}, check if the photo video is okay
    
    Return a JSON object adhering to this schema:

    {{
      "is_verified": bool, 
      "violated_verification_criteria_index": int,
      "your_reasoning": string,
      "explain_in_detail_what_you_see_on_it": string
    }}

    - "is_verified": A boolean indicating whether the content satisfies the verification criteria
    - "violated_verification_criteria_index": The index of the verification criterion that was violated with the highest assurance, or null if all criteria are met.

    Please analyze the provided {content_type} thoroughly and ensure the response is accurate.
"""

    verification_examples = []

    raw_data_bytes = file.read()

    # parts = [
    #     Part.from_text(prompt),
    #     Part.from_data(raw_data_bytes, mime_type),
    # ]

    # response = model.generate_content(
    #     contents=[Content(parts=parts, role="user")],
    # )
    # result = json.loads(response.text)

    # Wait until verification is necessary
    if True:
        await process_function(
            {
                "file_bytes": raw_data_bytes,
                "file_name": file_name,
                "file_extension": file_extension,
                "task_id": task_id,
                "assignee_user_id": assignee_user_id,
                "extra_data": extra_data,
            }
        )

        # _ = asyncio.gather(notify_companion_user(match, assignee_user_id))

        return VerificationResult(is_verification_success=True)
    else:
        pass


async def video_transcode_callback(message: ReceivedMessage) -> None:
    message_str = message.message.data.decode("utf-8")
    job_data = json.loads(message_str)
    verification_new_sate = (
        VerificationState.READY_FOR_USE
        if job_data["job"]["state"] == "SUCCEEDED"
        else VerificationState.PROCESSING_FAILED
    )

    await mongo.verifications.update_one(
        {"transcode_job_name": job_data["job"]["name"]},
        {"$set": {"state": verification_new_sate}},
    )


def accumulate_verification_requirements(task_verifications):
    requirements_list = []
    for ind, verification in enumerate(task_verifications):
        requirement_desc = verification.get("name", "")
        requirements_list.append(f"{ind + 1}) - {requirement_desc}\n")
    return "".join(requirements_list)


async def process_video(additional_data: dict) -> MediaProcessingResult:
    raw_file_full_name = (
        f'{additional_data["file_name"]}{additional_data["file_extension"]}'
    )

    public_hls_url = ""
    public_dash_url = ""
    transcode_job_name = None
    thumbnail_path = ""
    if additional_data["extra_data"]["should_transcode"]:
        job = create_transcode_job(
            build_raw_video_path(raw_file_full_name),
            build_transcoded_video_path(additional_data["file_name"]),
            get_topic_path(
                settings.gcp_project_id, settings.pub_sub_transcoder_topic_id
            ),
        )
        transcode_job_name = job.name

        public_transcoded_path = build_public_transcoded_video_path(
            additional_data["file_name"]
        )

        public_hls_url = f"{public_transcoded_path}manifest.m3u8"
        public_dash_url = f"{public_transcoded_path}manifest.mpd"
        thumbnail_path = f"{public_transcoded_path}thumbnail0000000000.jpeg"

    public_video_mp4_path = build_public_video_mp4_path(raw_file_full_name)
    await mongo.verifications.update_one(
        {
            "_id": additional_data["extra_data"]["verification_id"],
        },
        {
            "$set": {
                "state": VerificationState.PROCESSING_MEDIA,
                "verified_media_playback": {
                    "hls": public_hls_url,
                    "dash": public_dash_url,
                    "mp4": public_video_mp4_path,
                    "thumbnail": thumbnail_path,
                },
                "transcode_job_name": transcode_job_name,
                "last_modified_date": datetime.now(timezone.utc),
            }
        },
    )

    logger.info(f"transcode has been scheduled for the file '{raw_file_full_name}'")

    return MediaProcessingResult(
        file_urls=[public_hls_url, public_dash_url, public_video_mp4_path],
        verification_state=VerificationState.PROCESSING_MEDIA,
        transcode_job_name=transcode_job_name,
    )


async def process_image(additional_data: dict) -> MediaProcessingResult:
    await mongo.verifications.update_one(
        {
            "_id": additional_data["extra_data"]["verification_id"],
        },
        {
            "$set": {
                "is_public": True,
                "last_modified_date": datetime.now(timezone.utc),
                "state": VerificationState.READY_FOR_USE,
                "verified_image": additional_data["extra_data"]["image_url"],
                "file_name": additional_data["file_name"],
                "file_extension": additional_data["file_extension"],
                "file_content_type": "image",
            }
        },
    )

    return MediaProcessingResult(
        file_urls=[additional_data["extra_data"]["image_url"]],
        verification_state=VerificationState.READY_FOR_USE,
    )
