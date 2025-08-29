from fastapi import APIRouter, Query, Request
from typing import List
from ment_api.models.notification import NotificationResponse
from ment_api.persistence import mongo
from ment_api.models.notification import NotificationType
from pydantic import BaseModel

router = APIRouter(
    prefix="/notifications",
    tags=["notifications"],
    responses={404: {"description": "Not found"}},
)


class MarkNotificationsReadResponse(BaseModel):
    success: bool
    modified_count: int


class UnreadCountResponse(BaseModel):
    count: int


@router.get(
    "", response_model=List[NotificationResponse], operation_id="get_notifications"
)
async def get_notifications(
    request: Request,
    page: int = Query(default=1, ge=1),
    limit: int = Query(default=20, ge=1, le=100),
):
    external_user_id = request.state.supabase_user_id
    skip = (page - 1) * limit

    pipeline = [
        {"$match": {"to_user_id": external_user_id}},
        {"$sort": {"created_at": -1}},
        {"$skip": skip},
        {"$limit": limit},
        {
            "$lookup": {
                "from": "users",
                "localField": "from_user_id",
                "foreignField": "external_user_id",
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


@router.post(
    "/mark-read",
    operation_id="mark_notifications_read",
    response_model=MarkNotificationsReadResponse,
)
async def mark_notifications_read(request: Request) -> MarkNotificationsReadResponse:
    external_user_id = request.state.supabase_user_id
    result = await mongo.notifications.update_many(
        {"to_user_id": external_user_id, "read": False}, {"$set": {"read": True}}
    )

    return {"success": True, "modified_count": result.modified_count}


@router.get(
    "/unread-count",
    operation_id="get_unread_count",
    response_model=UnreadCountResponse,
)
async def get_unread_count(request: Request) -> UnreadCountResponse:
    external_user_id = request.state.supabase_user_id
    count = await mongo.notifications.count_documents(
        {
            "to_user_id": external_user_id,
            "read": False,
            "type": {
                "$in": [NotificationType.VERIFICATION_LIKE, NotificationType.IMPRESSION]
            },
        }
    )

    return {"count": count}
