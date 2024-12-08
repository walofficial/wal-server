from fastapi import APIRouter, HTTPException, Body, Header
from bson import ObjectId
from ment_api.persistence.mongo import pinned_verifications, verifications, users
from ment_api.common.custom_object_id import CustomObjectId
from ment_api.models.locaiton_feed_post import LocationFeedPost
from pydantic import BaseModel
from typing import Annotated
from ment_api.services.notification_service import send_pinned_post_notifications
import logging

router = APIRouter()

logger = logging.getLogger(__name__)


class PinnedVerification(BaseModel):
    verification_id: CustomObjectId
    task_id: CustomObjectId


@router.delete("/pin_verification")
async def delete_pin(task_id: CustomObjectId):
    result = await pinned_verifications.delete_one({"task_id": task_id})

    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Pinned verification not found")

    return {"message": "Pin deleted successfully"}


@router.post("/pin_verification")
async def pin_verification(verification: PinnedVerification):
    document = {
        "verification_id": verification.verification_id,
        "task_id": verification.task_id,
    }

    existing = await pinned_verifications.update_one(
        {"task_id": document["task_id"]}, {"$set": document}, upsert=True
    )
    await send_pinned_post_notifications(
        verification.verification_id, verification.task_id
    )


@router.get("/get_pinned_verification")
async def get_pinned_verification(task_id: CustomObjectId):
    document = await pinned_verifications.find_one({"task_id": task_id})
    if not document:
        raise HTTPException(status_code=404, detail="Pinned verification not found")

    pipeline = [
        {"$match": {"_id": document["verification_id"]}},
        {
            "$lookup": {
                "from": "users",
                "localField": "assignee_user_id",
                "foreignField": "_id",
                "as": "assignee_user",
            }
        },
        {"$unwind": "$assignee_user"},
        {
            "$project": {
                "_id": 1,
                "assignee_user_id": 1,
                "task_id": 1,
                "state": 1,
                "transcode_job_name": 1,
                "verified_media_playback": 1,
                "verified_image": 1,
                "assignee_user": 1,
                "last_modified_date": 1,
                "text_content": 1,
            }
        },
    ]

    results = await verifications.aggregate(pipeline)
    verifications_list = [LocationFeedPost(**verification) for verification in results]

    if not verifications_list:
        raise HTTPException(status_code=404, detail="Verification not found")

    return verifications_list[0]
