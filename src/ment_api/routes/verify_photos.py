from datetime import datetime, timezone
from typing import Annotated, Optional

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from pydantic import BaseModel

from ment_api.common.custom_object_id import CustomObjectId
from ment_api.common.utils import get_file_name_and_extension
from ment_api.models.location_feed_post import FeedPost
from ment_api.models.verification_state import VerificationState
from ment_api.persistence import mongo
from ment_api.services.external_clients.cloud_flare_client import upload_image
from ment_api.services.verification_service import (
    execute_file_verification,
    process_image,
)

router = APIRouter(
    prefix="/verify-photos",
    tags=["verify-photos"],
    responses={404: {"description": "Not found"}},
)

allowed_content_types = ["image/jpeg", "image/png", "image/webp"]


class UploadPhotoToLocationResponse(BaseModel):
    verification: FeedPost


@router.post(
    "/upload-to-location",
    response_model=UploadPhotoToLocationResponse,
    responses={500: {"description": "Generation error"}},
)
async def upload_photo_to_location(
    request: Request,
    photo_file: Annotated[
        UploadFile, File(media_type="image/jpeg", description="verification image")
    ],
    feed_id: Annotated[CustomObjectId, Form(...)],
    text_content: Annotated[Optional[str], Form()] = None,
):
    external_user_id = request.state.supabase_user_id
    if photo_file.content_type not in allowed_content_types:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid file type. Expected {allowed_content_types}.",
        )
    file_name, file_extension = get_file_name_and_extension(photo_file)

    file_bytes = photo_file.file.read()
    dest_file_extension = ".jpeg"
    dest_file_full_name = f"{file_name}{dest_file_extension}"

    image = await upload_image(
        file=file_bytes,
        destination_file_name=dest_file_full_name,
        content_type=photo_file.content_type,
    )

    insert_doc = {
        "feed_id": feed_id,
        "assignee_user_id": external_user_id,
        "file_content_type": "image",
        "file_name": file_name,
        "state": VerificationState.READY_FOR_USE,
        "last_modified_date": datetime.now(timezone.utc),
        "text_content": text_content,
        "image_gallery_with_dims": [image.model_dump()],
    }

    verification_doc = await mongo.verifications.insert_one(insert_doc)

    extra_data = {
        "image_url": image.url,
        "verification_id": verification_doc.inserted_id,
    }

    photo_file.file.seek(0)

    await execute_file_verification(
        photo_file.file,
        file_name,
        file_extension,
        photo_file.content_type,
        feed_id,
        external_user_id,
        process_image,
        extra_data,
    )

    insert_doc["_id"] = verification_doc.inserted_id

    return UploadPhotoToLocationResponse(verification=FeedPost(**insert_doc))
