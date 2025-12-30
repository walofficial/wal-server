"""
Location Assets API Routes.

Endpoints for uploading and managing location reference images
and prompts for AI character image generation.
"""

import logging
import uuid
from datetime import datetime, timezone
from typing import List, Optional

from bson import ObjectId
from fastapi import APIRouter, File, Form, HTTPException, UploadFile, Query

from ment_api.models.ai_character import (
    CreateLocationAssetRequest,
    LocationAssetAddImagesResponse,
    LocationAssetDeleteResponse,
    LocationAssetDetail,
    LocationAssetListResponse,
    LocationAssetUploadResponse,
)
from ment_api.persistence import mongo
from ment_api.services.external_clients.cloud_flare_client import upload_image

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/location-assets",
    tags=["location-assets"],
    responses={404: {"description": "Not found"}},
)


@router.post(
    "/upload",
    response_model=LocationAssetUploadResponse,
    operation_id="upload_location_assets",
)
async def upload_location_assets_endpoint(
    feed_id: str = Form(...),
    feed_name: str = Form(...),
    description: str = Form(...),
    images: List[UploadFile] = File(default=[]),
) -> LocationAssetUploadResponse:
    """
    Upload reference images and prompts for a location.

    These assets are used by AI characters when generating
    images of themselves at this location.
    """
    try:
        feed_object_id = ObjectId(feed_id)

        # Verify feed exists
        feed = await mongo.feeds.find_one({"_id": feed_object_id})
        if not feed:
            raise HTTPException(status_code=404, detail="Feed not found")

        # Upload images
        uploaded_images: List[dict] = []
        for idx, image_file in enumerate(images):
            content = await image_file.read()
            content_type = image_file.content_type or "image/jpeg"
            filename = f"location_assets/{feed_id}/{uuid.uuid4().hex[:8]}_{idx}.jpg"

            result = await upload_image(content, filename, content_type)
            uploaded_images.append(
                {
                    "url": result.url,
                    "width": result.width,
                    "height": result.height,
                }
            )

        now = datetime.now(timezone.utc)

        # Check if location asset already exists for this feed
        existing = await mongo.location_assets.find_one({"feed_id": feed_object_id})

        if existing:
            # Update existing
            update_data: dict = {
                "feed_name": feed_name,
                "description": description,
                "updated_at": now,
            }
            # Append new images to existing ones
            if uploaded_images:
                update_data["images"] = existing.get("images", []) + uploaded_images

            await mongo.location_assets.update_one(
                {"_id": existing["_id"]},
                {"$set": update_data},
            )

            logger.info(
                "Updated location assets",
                extra={
                    "json_fields": {
                        "operation": "update_location_assets",
                        "feed_id": feed_id,
                        "feed_name": feed_name,
                        "images_added": len(uploaded_images),
                    },
                    "labels": {"component": "location_assets_route"},
                },
            )

            return LocationAssetUploadResponse(
                status="updated",
                asset_id=str(existing["_id"]),
                images_count=len(existing.get("images", [])) + len(uploaded_images),
            )

        else:
            # Create new
            asset_doc = {
                "feed_id": feed_object_id,
                "feed_name": feed_name,
                "images": uploaded_images,
                "description": description,
                "created_at": now,
                "updated_at": now,
            }

            result = await mongo.location_assets.insert_one(asset_doc)

            logger.info(
                "Created location assets",
                extra={
                    "json_fields": {
                        "operation": "create_location_assets",
                        "asset_id": str(result.inserted_id),
                        "feed_id": feed_id,
                        "feed_name": feed_name,
                        "images_count": len(uploaded_images),
                    },
                    "labels": {"component": "location_assets_route"},
                },
            )

            return LocationAssetUploadResponse(
                status="created",
                asset_id=str(result.inserted_id),
                images_count=len(uploaded_images),
            )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to upload location assets: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get(
    "/{feed_id}",
    response_model=LocationAssetDetail,
    operation_id="get_location_assets",
)
async def get_location_assets_endpoint(feed_id: str) -> LocationAssetDetail:
    """Get location assets for a feed."""
    try:
        asset = await mongo.location_assets.find_one({"feed_id": ObjectId(feed_id)})
        if not asset:
            raise HTTPException(status_code=404, detail="Location assets not found")

        return LocationAssetDetail.from_mongo(asset)

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get location assets: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get(
    "/",
    response_model=LocationAssetListResponse,
    operation_id="list_location_assets",
)
async def list_location_assets_endpoint(
    limit: int = Query(default=50, le=100),
) -> LocationAssetListResponse:
    """List all location assets."""
    docs = await mongo.location_assets.find_all({})

    assets = [LocationAssetDetail.from_mongo(doc) for doc in docs[:limit]]

    return LocationAssetListResponse(assets=assets, total=len(docs))


@router.delete(
    "/{feed_id}",
    response_model=LocationAssetDeleteResponse,
    operation_id="delete_location_assets",
)
async def delete_location_assets_endpoint(feed_id: str) -> LocationAssetDeleteResponse:
    """Delete location assets for a feed."""
    result = await mongo.location_assets.delete_one({"feed_id": ObjectId(feed_id)})

    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Location assets not found")

    logger.info(
        "Deleted location assets",
        extra={
            "json_fields": {
                "operation": "delete_location_assets",
                "feed_id": feed_id,
            },
            "labels": {"component": "location_assets_route"},
        },
    )

    return LocationAssetDeleteResponse(status="deleted", feed_id=feed_id)


@router.post(
    "/{feed_id}/add-images",
    response_model=LocationAssetAddImagesResponse,
    operation_id="add_location_images",
)
async def add_location_images_endpoint(
    feed_id: str,
    images: List[UploadFile] = File(...),
) -> LocationAssetAddImagesResponse:
    """Add additional images to existing location assets."""
    try:
        asset = await mongo.location_assets.find_one({"feed_id": ObjectId(feed_id)})
        if not asset:
            raise HTTPException(status_code=404, detail="Location assets not found")

        # Upload new images
        new_images: List[dict] = []
        for idx, image_file in enumerate(images):
            content = await image_file.read()
            content_type = image_file.content_type or "image/jpeg"
            filename = f"location_assets/{feed_id}/{uuid.uuid4().hex[:8]}_{idx}.jpg"

            result = await upload_image(content, filename, content_type)
            new_images.append(
                {
                    "url": result.url,
                    "width": result.width,
                    "height": result.height,
                }
            )

        # Append to existing images
        all_images = asset.get("images", []) + new_images
        await mongo.location_assets.update_one(
            {"_id": asset["_id"]},
            {
                "$set": {
                    "images": all_images,
                    "updated_at": datetime.now(timezone.utc),
                }
            },
        )

        return LocationAssetAddImagesResponse(
            status="images_added",
            new_count=len(new_images),
            total_count=len(all_images),
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to add location images: {e}")
        raise HTTPException(status_code=500, detail=str(e))

