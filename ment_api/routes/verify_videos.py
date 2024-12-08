import logging
import os
import uuid
from typing import Annotated, Tuple

from cachetools import TTLCache
from fastapi import APIRouter, UploadFile, File, HTTPException, Form, Header

from ment_api.common.custom_object_id import CustomObjectId
from ment_api.config import settings
from ment_api.models.execute_video_verification_request import (
    ExecuteVideoVerificationRequest,
)
from ment_api.models.execute_location_post_upload_request import (
    ExecuteLocationPostUploadRequest,
)
from ment_api.models.verification_state import VerificationState
from ment_api.persistence import mongo
from ment_api.services.google_storage_service import (
    upload_video_verification,
    download_video_verification,
    build_raw_video_path,
    build_raw_video_mp4_path,
)

from ment_api.services.google_tasks_service import create_http_task
from ment_api.services.transcoder_service import (
    create_to_mp4_transcode_job,
    wait_for_job_completion,
)
from typing import Optional
from ment_api.services.verification_service import execute_file_verification
from ment_api.services.verification_service import process_video
from datetime import datetime, timezone
from ment_api.models.locaiton_feed_post import LocationFeedPost

router = APIRouter(
    prefix="/verify-videos",
    tags=["verification"],
    responses={404: {"description": "Not found"}},
)

supported_mime_types = ["video/webm;codecs=vp8,opus", "video/webm", "video/mp4"]

video_verification_cache = TTLCache(
    maxsize=settings.video_verification_cache_size,
    ttl=settings.video_verification_cache_ttl,
)

logger = logging.getLogger(__name__)


@router.post("")
async def submit_user_video_verification(
    video_file: Annotated[
        UploadFile,
        File(description="A video for task verification"),
    ],
    x_user_id: Annotated[CustomObjectId, Header()],
    match_id: Annotated[CustomObjectId, Form()],
):
    if video_file.content_type not in supported_mime_types:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid file type. Supported types are: {', '.join(supported_mime_types)}.",
        )

    dest_file_name = str(uuid.uuid4())
    dest_file_extension = ".webm" if "webm" in video_file.content_type else ".mp4"
    dest_file_full_name = f"{dest_file_name}{dest_file_extension}"

    try:
        uploaded_url = upload_video_verification(
            video_file.file, dest_file_full_name, video_file.content_type
        )
    except Exception as e:
        logging.error("Something went wrong during the upload.", exc_info=True)
        raise HTTPException(
            status_code=400,
            detail={"description": "Something went wrong during the upload."},
        )

    gtask_payload = {
        "match_id": str(match_id),
        "assignee_user_id": str(x_user_id),
        "content_type": video_file.content_type,
        "file_name": dest_file_name,
        "file_extension": dest_file_extension,
    }
    create_http_task(f"{settings.api_url}/verify-videos/execute", gtask_payload)

    await mongo.verifications.update_one(
        {
            "match_id": match_id,
            "assignee_user_id": x_user_id,
        },
        {
            "$set": {
                "state": VerificationState.VERIFICATION_IN_PROGRESS,
                "file_name": dest_file_name,
                "file_extension": dest_file_extension,
                "file_content_type": video_file.content_type,
            }
        },
    )

    return {"uploaded_file_url": uploaded_url, "gtask_payload": gtask_payload}


@router.post("/execute")
async def execute_user_video_verification(
    verification_request: ExecuteVideoVerificationRequest,
):
    try:
        file_name, extension = (
            verification_request.file_name,
            verification_request.file_extension,
        )

        if verification_request.file_extension != ".mp4":
            file_name, extension = await prepare_raw_file_in_mp4(
                verification_request.file_name, verification_request.file_extension
            )

        file_full_name = f"{file_name}{extension}"
        file = download_video_verification(file_full_name)

        result = await execute_file_verification(
            file,
            file_name,
            extension,
            verification_request.content_type,
            verification_request.match_id,
            verification_request.assignee_user_id,
            process_video,
        )
        logging.info(f"video processing result - {result}")
    except Exception as e:
        logger.exception("something went wrong during execute")
        raise HTTPException(status_code=500, detail=str(e))

    return result


@router.post("/process-video")
async def trigger_transcode(
    file_full_name: str, match_id: CustomObjectId, assignee_user_id: CustomObjectId
) -> None:
    name, extension = os.path.splitext(file_full_name)

    await process_video(
        {
            "match_id": match_id,
            "file_name": name,
            "file_extension": extension,
            "assignee_user_id": assignee_user_id,
        }
    )


async def prepare_raw_file_in_mp4(
    raw_file_name: str, raw_file_extension: str
) -> Tuple[str, str]:
    to_mp4_job = create_to_mp4_transcode_job(
        build_raw_video_path(f"{raw_file_name}{raw_file_extension}"),
        build_raw_video_mp4_path(),
        f"{raw_file_name}.mp4",
    )
    await wait_for_job_completion(to_mp4_job.name)
    return raw_file_name, ".mp4"


logger = logging.getLogger(__name__)


@router.post("/upload-to-location")
async def submit_user_video_verification_location_upload(
    video_file: Annotated[
        UploadFile,
        File(description="A video for task verification"),
    ],
    x_user_id: Annotated[CustomObjectId, Header()],
    task_id: Annotated[CustomObjectId, Form()],
    recording_time: Annotated[int, Form()],
    text_content: Annotated[Optional[str], Form()] = None,
):
    # Only transcoding videos longer than 5 seconds due to transcoder limitation on small videos
    should_transcode_video = recording_time > 5
    print(f"should_transcode_video - {should_transcode_video}")
    if video_file.content_type not in supported_mime_types:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid file type. Supported types are: {', '.join(supported_mime_types)}.",
        )

    dest_file_name = str(uuid.uuid4())
    dest_file_extension = ".webm" if "webm" in video_file.content_type else ".mp4"
    dest_file_full_name = f"{dest_file_name}{dest_file_extension}"

    try:
        uploaded_url = upload_video_verification(
            video_file.file, dest_file_full_name, video_file.content_type
        )
    except Exception as e:
        logging.error("Something went wrong during the upload.", exc_info=True)
        raise HTTPException(
            status_code=400,
            detail={"description": "Something went wrong during the upload."},
        )

    insert_doc = {
        "task_id": task_id,
        "assignee_user_id": x_user_id,
        "state": VerificationState.READY_FOR_USE,
        "file_name": dest_file_name,
        "file_extension": dest_file_extension,
        "file_content_type": video_file.content_type,
        "last_modified_date": datetime.now(timezone.utc),
        "is_public": True,
        "text_content": text_content,
        "verified_media_playback": {
            "hls": "",
            "dash": "",
            "mp4": uploaded_url,
        },
    }

    verification_doc = await mongo.verifications.insert_one(insert_doc)
    insert_doc["_id"] = verification_doc.inserted_id
    gtask_payload = {
        "task_id": str(task_id),
        "assignee_user_id": str(x_user_id),
        "content_type": video_file.content_type,
        "file_name": dest_file_name,
        "file_extension": dest_file_extension,
        "verification_id": str(verification_doc.inserted_id),
        "should_transcode": should_transcode_video,
    }
    create_http_task(
        f"{settings.api_url}/verify-videos/execute/location-upload", gtask_payload
    )

    return {
        "uploaded_file_url": uploaded_url,
        "gtask_payload": gtask_payload,
        "verification": LocationFeedPost(**insert_doc),
    }


@router.post("/execute/location-upload")
async def execute_user_video_verification_location_upload(
    verification_request: ExecuteLocationPostUploadRequest,
):
    try:
        file_name, extension = (
            verification_request.file_name,
            verification_request.file_extension,
        )

        should_transcode_video = verification_request.should_transcode
        if verification_request.file_extension != ".mp4":
            file_name, extension = await prepare_raw_file_in_mp4(
                verification_request.file_name, verification_request.file_extension
            )

        file_full_name = f"{file_name}{extension}"
        file = download_video_verification(file_full_name)

        result = await execute_file_verification(
            file,
            file_name,
            extension,
            verification_request.content_type,
            verification_request.task_id,
            verification_request.assignee_user_id,
            process_video,
            {
                "verification_id": verification_request.verification_id,
                "should_transcode": should_transcode_video,
            },
        )
        logging.info(f"video processing result - {result}")
    except Exception as e:
        logger.exception("something went wrong during execute")
        raise HTTPException(status_code=500, detail=str(e))

    return result
