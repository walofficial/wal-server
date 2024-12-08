from fastapi import APIRouter, Header, Query
from typing import Annotated, List
from ment_api.common.custom_object_id import CustomObjectId
from ment_api.models.notification import NotificationResponse
from ment_api.persistence import mongo
from pymongo import UpdateOne

router = APIRouter(
    prefix="/notifications",
    tags=["notifications"],
    responses={404: {"description": "Not found"}},
)


@router.get("", response_model=List[NotificationResponse])
async def get_notifications(
    x_user_id: Annotated[CustomObjectId, Header(...)],
    page: int = Query(default=1, ge=1),
    limit: int = Query(default=20, ge=1, le=100),
):
    skip = (page - 1) * limit

    pipeline = [
        {"$match": {"to_user_id": x_user_id}},
        {"$sort": {"created_at": -1}},
        {"$skip": skip},
        {"$limit": limit},
        {
            "$lookup": {
                "from": "users",
                "localField": "from_user_id",
                "foreignField": "_id",
                "as": "from_user",
            }
        },
        {"$unwind": "$from_user"},
    ]

    notifications = await mongo.notifications.aggregate(pipeline)

    return [
        NotificationResponse(
            notification=notification, from_user=notification["from_user"]
        )
        for notification in notifications
    ]


@router.post("/mark-read")
async def mark_notifications_read(
    x_user_id: Annotated[CustomObjectId, Header(...)],
):
    result = await mongo.notifications.bulk_update(
        [UpdateOne({"to_user_id": x_user_id, "read": False}, {"$set": {"read": True}})]
    )

    return {"success": True, "modified_count": result.modified_count}


@router.get("/unread-count")
async def get_unread_count(
    x_user_id: Annotated[CustomObjectId, Header(...)],
):
    count = await mongo.notifications.count_documents(
        {"to_user_id": x_user_id, "read": False}
    )

    return {"count": count}
