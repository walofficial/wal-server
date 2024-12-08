import random
from fastapi import APIRouter, Body, Header, HTTPException, Path
from typing import Annotated, Awaitable, Dict, Optional, Tuple, List
from ment_api.common.custom_object_id import CustomObjectId
from pydantic import BaseModel

from fastapi import Query
from ment_api.models.task import Task
from ment_api.models.task_category import TaskCategory

from ment_api.persistence import mongo
from ment_api.services.location_service import is_on_task_location
from ment_api.services.notification_service import send_notification
import asyncio
from datetime import datetime, timezone, timedelta, timezone, timezone
from ment_api.models.user import User
from ment_api.models.task_response import DailyTasksResponse, TaskWithLocation
from ment_api.models.task_location_mapping import (
    Location,
)
import uuid
from bson import ObjectId
from pymongo.errors import DuplicateKeyError
from ment_api.models.locaiton_feed_post import LocationFeedPost
from ment_api.persistence.mongo_client import client, db
from ment_api.services.external_clients.google_client import task_expire

# Add this import at the top of the file
from ment_api.persistence import mongo

from ment_api.models.anon_list_response import AnonListEntry
from ment_api.services.google_tasks_service import create_http_task

router = APIRouter(
    prefix="/tasks",
    tags=["tasks"],
    responses={404: {"description": "Not found"}},
)


@router.get(
    "/locations",
    response_model=DailyTasksResponse,
    responses={500: {"description": "Generation error"}},
)
async def get_daily_tasks(
    category_id: Annotated[CustomObjectId, Query()],
    x_user_location_latitude: Annotated[float, Header(...)],
    x_user_location_longitude: Annotated[float, Header(...)],
):
    user_location = (x_user_location_latitude, x_user_location_longitude)
    pipeline = [
        {"$match": {"task_category_id": category_id, "hidden": {"$ne": True}}},
        {
            "$lookup": {
                "from": "live_users",
                "let": {"current_task_id": "$_id"},
                "pipeline": [
                    {
                        "$match": {
                            "$expr": {"$eq": ["$task_id", "$$current_task_id"]},
                            "expiration_date": {"$gt": datetime.now(timezone.utc)},
                        }
                    },
                    {"$count": "count"},
                ],
                "as": "live_user_count",
            }
        },
        {"$unwind": {"path": "$live_user_count", "preserveNullAndEmptyArrays": True}},
        {
            "$lookup": {
                "from": "task_verifications",
                "let": {"current_task_id": "$_id"},
                "pipeline": [
                    {"$match": {"$expr": {"$eq": ["$task_id", "$$current_task_id"]}}},
                    {"$count": "count"},
                ],
                "as": "verification_count",
            }
        },
        {
            "$unwind": {
                "path": "$verification_count",
                "preserveNullAndEmptyArrays": True,
            }
        },
        {
            "$project": {
                "_id": 1,
                "task_title": 1,
                "task_category_id": 1,
                "task_verification_media_type": 1,
                "display_name": 1,
                "task_verification_requirements": 1,
                "task_verification_example_sources": 1,
                "task_location": 1,
                "task_locations": 1,
                "task_description": 1,
                "hidden": 1,
                "live_user_count": {"$ifNull": ["$live_user_count.count", 0]},
                "verification_count": {"$ifNull": ["$verification_count.count", 0]},
            }
        },
    ]

    tasks = await mongo.daily_picks.aggregate(pipeline)
    tasks = list(tasks)

    tasks_at_location = []
    nearest_tasks = []

    for task in tasks:
        task_obj = Task(**task)
        is_at_location, nearest_location = await is_on_task_location(
            task_obj.id, user_location
        )

        if is_at_location:
            tasks_at_location.append(task_obj)
        else:
            nearest_tasks.append(
                TaskWithLocation(task=task_obj, nearest_location=nearest_location)
            )

    return DailyTasksResponse(
        tasks_at_location=tasks_at_location, nearest_tasks=nearest_tasks
    )


@router.get(
    "/daily",
    responses={500: {"description": "Generation error"}},
)
async def get_daily_tasks(
    category_id: Annotated[CustomObjectId, Query()],
):
    data = await mongo.daily_picks.find_all({"task_category_id": category_id})

    tasks = [task for task in data if not task.get("hidden", False)]

    category = await mongo.daily_picks_categories.find_one_by_id(category_id)

    if category["title"] == "არჩევნები 2024":
        tasks = order_election_tasks(tasks)

    return [Task(**task) for task in tasks]


@router.get(
    "/daily-tasks-categories",
    responses={500: {"description": "Generation error"}},
)
async def generate():
    data = await mongo.daily_picks_categories.find_all({})

    # Sort the data based on the 'order' property
    sorted_data = sorted(data, key=lambda x: x.get("order", float("inf")))

    return [TaskCategory(**task) for task in sorted_data]


@router.post("/categories")
async def add_task_category(category_data: Annotated[Dict, Body(...)]):
    new_category = await mongo.daily_picks_categories.insert_one(
        {
            "title": category_data["title"],
            "display_name": category_data["display_name"],
            "hidden": category_data["hidden"],
        }
    )
    return {"id": str(new_category.inserted_id)}


@router.put("/categories/{category_id}")
async def update_task_category(
    category_id: CustomObjectId, category_data: Annotated[Dict, Body(...)]
):
    result = await mongo.daily_picks_categories.update_one(
        {"_id": category_id},
        {
            "$set": {
                "title": category_data["title"],
                "display_name": category_data["display_name"],
                "hidden": category_data["hidden"],
            }
        },
    )
    if result.modified_count == 0:
        return {"error": "Category not found or not modified"}
    return {"message": "Category updated successfully"}


@router.patch("/categories/{category_id}/visibility")
async def toggle_task_category_visibility(
    category_id: CustomObjectId, visibility_data: Annotated[Dict, Body(...)]
):
    result = await mongo.daily_picks_categories.update_one(
        {"_id": category_id}, {"$set": {"hidden": visibility_data["hidden"]}}
    )
    if result.modified_count == 0:
        return {"error": "Category not found or not modified"}
    return {"message": "Category visibility updated successfully"}


@router.post("/notifications/push")
async def send_push_notification(notification_data: Annotated[Dict, Body(...)]):
    task_id = notification_data["data"]["taskId"]
    category_id = notification_data["data"]["categoryId"]

    # Get the task document to use its display_name if title is not provided
    task = await mongo.daily_picks.find_one({"_id": CustomObjectId(task_id)})

    title = notification_data.get("title") or task.get(
        "display_name", "New Task Available"
    )

    body = notification_data.get("body", "ახალი დავალება!")

    # Get all users
    users = await mongo.users.find_all({})

    # Send notifications to all users
    notification_tasks = [
        send_notification(
            str(user["_id"]),
            title,
            body,
            data={"taskId": task_id, "categoryId": category_id},
        )
        for user in users
    ]

    results = await asyncio.gather(*notification_tasks)

    successful_notifications = sum(results)
    total_users = len(users)

    return {
        "message": f"Push notifications sent to {successful_notifications} out of {total_users} users",
        "successful_notifications": successful_notifications,
        "total_users": total_users,
    }


# Add this new endpoint
@router.get("/anon-list")
async def anon_list(
    x_user_id: Annotated[CustomObjectId, Header(...)],
    task_id: Annotated[CustomObjectId, Query(...)],
):
    pipeline = [
        {
            "$match": {
                "task_id": task_id,
                "expiration_date": {"$gt": datetime.utcnow()},
                "author_id": {"$ne": x_user_id},
            }
        },
        {
            "$lookup": {
                "from": "users",
                "localField": "author_id",
                "foreignField": "_id",
                "as": "author",
            }
        },
        {"$unwind": "$author"},
        {
            "$lookup": {
                "from": "friendships",
                "let": {"author_id": "$author_id"},
                "pipeline": [
                    {
                        "$match": {
                            "$expr": {
                                "$and": [
                                    {"$eq": ["$user_id", x_user_id]},
                                    {"$eq": ["$friend_id", "$$author_id"]},
                                ]
                            }
                        }
                    }
                ],
                "as": "friendship",
            }
        },
        {"$addFields": {"is_friend": {"$gt": [{"$size": "$friendship"}, 0]}}},
        {
            "$project": {
                "user": "$author",
                "is_friend": 1,
                "_id": 0,
            }
        },
        {"$sort": {"is_friend": -1}},  # Sort friends first
    ]
    results = await mongo.live_users.aggregate(pipeline)

    return [AnonListEntry(**result) for result in results]


@router.get("/anon-list/count")
async def count_public_challenges(
    x_user_id: Annotated[CustomObjectId, Header(...)],
    task_id: Annotated[CustomObjectId, Query(...)],
):
    count = await mongo.live_users.count_documents(
        {
            "task_id": task_id,
            "expiration_date": {"$gt": datetime.utcnow()},
        }
    )

    return {"count": count}


@router.get("/single/{task_id}", response_model=Task)
async def get_single_task(
    task_id: Annotated[CustomObjectId, Path(...)],
    x_user_id: Annotated[CustomObjectId, Header(...)],
):
    task = await mongo.daily_picks.find_one_by_id(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    return Task(**task)


@router.get("/check-location")
async def check_location(
    task_id: Annotated[CustomObjectId, Query()],
    latitude: Annotated[float, Query()],
    longitude: Annotated[float, Query()],
) -> Tuple[bool, Optional[Location]]:
    loc = (latitude, longitude)
    return (True, None)
    return await is_on_task_location(task_id, loc)


class GoLiveRequest(BaseModel):
    task_id: CustomObjectId


@router.post("/go-live")
async def create_challenge_request(
    x_user_id: Annotated[CustomObjectId, Header(...)],
    request: GoLiveRequest,
):
    task_id = request.task_id

    async with await client.start_session() as session:
        async with session.start_transaction():
            expire_hours = 12
            try:
                expiration_date = datetime.now(timezone.utc) + timedelta(
                    hours=expire_hours
                )
                live_doc = await db[mongo.live_users.collection].find_one(
                    {
                        "author_id": x_user_id,
                        "task_id": task_id,
                        "expiration_date": {"$gt": datetime.now(timezone.utc)},
                    },
                    session=session,
                )

                if live_doc:
                    live_doc_id = str(live_doc["_id"])
                    random_id = str(uuid.uuid4())
                    task_expire.delete_task(task_name=f"{live_doc['task_expire_id']}")
                    task_name = f"expire-live-{random_id}"
                    task_expire.create_task(
                        in_seconds=timedelta(hours=expire_hours).total_seconds(),
                        path="/tasks/live/expire",
                        payload={"live_doc_id": live_doc_id},
                        task_name=task_name,
                    )
                    await db[mongo.live_users.collection].update_one(
                        {"_id": live_doc["_id"]},
                        {
                            "$set": {
                                "expiration_date": expiration_date,
                                "task_expire_id": task_name,
                            }
                        },
                        session=session,
                    )
                    return {"ok": True}

                random_id = str(uuid.uuid4())
                task_name = f"expire-live-{random_id}"

                result = await db[mongo.live_users.collection].insert_one(
                    {
                        "author_id": x_user_id,
                        "task_id": task_id,
                        "expiration_date": expiration_date,
                        "created_at": datetime.now(timezone.utc),
                        "task_expire_id": task_name,
                    },
                    session=session,
                )
                live_doc_id = str(result.inserted_id)

                task_expire.create_task(
                    in_seconds=timedelta(hours=expire_hours).total_seconds(),
                    path="/tasks/live/expire",
                    payload={"live_doc_id": live_doc_id},
                    task_name=task_name,
                )

                return {"ok": True}

            except DuplicateKeyError:
                raise HTTPException(
                    status_code=200,
                    detail="A challenge for this user and task already exists",
                )


@router.post("/live/expire")
async def live_expire(request: dict = Body(...)):
    live_doc_id = request.get("live_doc_id")
    if not live_doc_id:
        raise HTTPException(status_code=400, detail="live ID is required")

    live_user = await mongo.live_users.find_one_by_id(ObjectId(live_doc_id))
    if not live_user:
        return {"message": "Live user not found or already expired"}

    if live_user["expiration_date"] > datetime.now(timezone.utc):
        return {"message": "Challenge has not expired yet"}

    await mongo.live_users.delete_one({"_id": ObjectId(live_doc_id)})
    return {"message": "Challenge expired and removed successfully"}


class PublishPostRequest(BaseModel):
    task_id: CustomObjectId
    content: str


@router.post("/publish-post", responses={500: {"description": "Generation error"}})
async def publish_post(
    x_user_id: Annotated[CustomObjectId, Header()],
    request: PublishPostRequest,
):
    try:
        # Create a new verification document
        verification_doc = {
            "task_id": request.task_id,
            "assignee_user_id": x_user_id,
            "text_content": request.content,
            "state": "READY_FOR_USE",
            "last_modified_date": datetime.now(timezone.utc),
            "is_public": True,
        }

        result = await mongo.verifications.insert_one(verification_doc)
        verification_doc["_id"] = result.inserted_id

        return LocationFeedPost(**verification_doc)

    except Exception as e:
        print(e)
        raise HTTPException(status_code=500, detail="Internal server error")
