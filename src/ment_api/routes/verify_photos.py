import asyncio
import logging
from datetime import datetime, timezone
from typing import Annotated, Optional

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from ment_api.models.feed import Feed
from pydantic import BaseModel

from ment_api.common.custom_object_id import CustomObjectId
from ment_api.common.utils import get_file_name_and_extension
from ment_api.models.location_feed_post import FeedPost
from ment_api.models.verification_state import VerificationState
from ment_api.persistence import mongo
from ment_api.services.external_clients.cloud_flare_client import upload_image
from ment_api.services.external_clients.gemini_client import GeminiClient
from ment_api.services.verification_service import (
    execute_file_verification,
    process_image,
)

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/verify-photos",
    tags=["verify-photos"],
    responses={404: {"description": "Not found"}},
)

allowed_content_types = ["image/jpeg", "image/png", "image/webp"]


async def add_photo_to_location_assets(
    feed_id: CustomObjectId,
    image_url: str,
    image_width: int,
    image_height: int,
) -> None:
    """
    Background task to add uploaded photo to location assets.
    
    This runs asynchronously without blocking the main request.
    """
    try:
        # Fetch feed to get feed name
        feed = await mongo.feeds.find_one({"_id": feed_id})
        feed = Feed(**feed)
        if not feed:
            logger.warning(
                "Feed not found for location asset",
                extra={
                    "json_fields": {
                        "operation": "add_photo_to_location_assets",
                        "feed_id": str(feed_id),
                    },
                    "labels": {"component": "verify_photos"},
                },
            )
            return

        feed_name = feed.display_name
        now = datetime.now(timezone.utc)

        # Generate image description using Gemini
        gemini_client = GeminiClient()
        image_description = None
        try:
            image_description = await gemini_client.generate_image_description(
                image_url=image_url,
                location_name=feed_name,
            )
            logger.info(
                "Generated image description with Gemini",
                extra={
                    "json_fields": {
                        "operation": "add_photo_to_location_assets",
                        "feed_id": str(feed_id),
                        "has_description": image_description is not None,
                        "description_length": len(image_description) if image_description else 0,
                    },
                    "labels": {"component": "verify_photos"},
                },
            )
        except Exception as e:
            logger.warning(
                "Failed to generate image description with Gemini",
                extra={
                    "json_fields": {
                        "operation": "add_photo_to_location_assets",
                        "feed_id": str(feed_id),
                        "error": str(e),
                    },
                    "labels": {"component": "verify_photos"},
                },
            )
            # Continue without description if generation fails

        # Prepare image data
        new_image = {
            "url": image_url,
            "width": image_width,
            "height": image_height,
        }
        
        # Add description to image data if available
        if image_description:
            new_image["description"] = image_description

        # Check if location asset already exists
        existing = await mongo.location_assets.find_one({"feed_id": feed_id})

        if existing:
            # Append to existing images
            existing_images = existing.get("images", [])
            updated_images = existing_images + [new_image]

            await mongo.location_assets.update_one(
                {"_id": existing["_id"]},
                {
                    "$set": {
                        "images": updated_images,
                        "updated_at": now,
                    }
                },
            )

            logger.info(
                "Added photo to existing location assets",
                extra={
                    "json_fields": {
                        "operation": "add_photo_to_location_assets",
                        "feed_id": str(feed_id),
                        "asset_id": str(existing["_id"]),
                        "total_images": len(updated_images),
                    },
                    "labels": {"component": "verify_photos"},
                },
            )
        else:
            # Create new location asset with default values
            asset_doc = {
                "feed_id": feed_id,
                "feed_name": feed_name,
                "images": [new_image],
                "description": f"User-uploaded photos from {feed_name}",
                "created_at": now,
                "updated_at": now,
            }

            result = await mongo.location_assets.insert_one(asset_doc)

            logger.info(
                "Created new location assets from verification photo",
                extra={
                    "json_fields": {
                        "operation": "add_photo_to_location_assets",
                        "feed_id": str(feed_id),
                        "asset_id": str(result.inserted_id),
                        "feed_name": feed_name,
                    },
                    "labels": {"component": "verify_photos"},
                },
            )

    except Exception as e:
        logger.error(
            "Failed to add photo to location assets",
            extra={
                "json_fields": {
                    "operation": "add_photo_to_location_assets",
                    "feed_id": str(feed_id),
                    "error": str(e),
                },
                "labels": {"component": "verify_photos"},
            },
        )


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
        limit_aspect_ratio=True,
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

    # Add photo to location assets in background (non-blocking)
    asyncio.create_task(
        add_photo_to_location_assets(
            feed_id=feed_id,
            image_url=image.url,
            image_width=image.width,
            image_height=image.height,
        )
    )

    insert_doc["_id"] = verification_doc.inserted_id

    return UploadPhotoToLocationResponse(verification=FeedPost(**insert_doc))
