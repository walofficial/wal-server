import logging
from typing import Annotated, Any, Optional

from bson.objectid import ObjectId
from fastapi import (
    APIRouter,
    Depends,
    Path,
    Request,
    HTTPException,
    Query,
    Header,
)

from pymongo import UpdateOne

from redis import Redis
from ment_api.common.custom_object_id import CustomObjectId
from ment_api.models.create_user_request import CreateUserRequest
from ment_api.models.create_daily_task_request import CreateDailyTaskRequest

from ment_api.models.update_user_request import UpdateUserRequest
from ment_api.models.update_verification_visibility_request import (
    UpdateVerificationVisibilityRequest,
)
from ment_api.models.check_registered_users import CheckRegisteredUsersRequest
from ment_api.models.user import User

from ment_api.models.verification import Verification
from ment_api.persistence import mongo
from ment_api.services.cf import clear_all_user_caches_from_cloudflare_kv
from ment_api.services.notification_service import send_new_sms_notification

from ment_api.services.redis_service import get_redis_dependency
from ment_api.models.locaiton_feed_post import LocationFeedPost

router = APIRouter(
    prefix="/user",
    tags=["user"],
    responses={404: {"description": "Not found"}},
)


@router.post("/create-user", responses={500: {"description": "Generation error"}})
async def create_user(request: CreateUserRequest):
    try:
        result = await mongo.users.insert_one(
            {
                "city": request.city,
                "date_of_birth": request.date_of_birth,
                "email": request.email,
                "gender": request.gender,
                "external_user_id": request.external_user_id,
                "interests": request.interests,
                "phone_number": request.phone_number,
                "username": request.username,
                "photos": request.photos,
                "profile_image": request.profile_image,
                "is_photos_hidden": request.is_photos_hidden,
                "is_in_waitlist": False,
            }
        )
        return {"id": str(result.inserted_id), **request.dict()}
    except Exception as e:
        logging.error("Something went wrong during 'create_user'", exc_info=True)
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/get-user", responses={404: {"description": "User not found"}})
async def get_user(x_user_external_id: Annotated[str, Header()]):
    user = await mongo.users.find_one({"external_user_id": x_user_external_id})
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return User(**user)


@router.post("/create-task", responses={500: {"description": "Generation error"}})
async def create_task(request: CreateDailyTaskRequest):
    try:
        task_id = await mongo.daily_picks.insert_one(
            {
                "task_title": request.task_title,
                "display_name": request.display_name,
                "task_location": request.task_location,
                "task_locations": [request.task_location],
                "task_verification_media_type": request.task_verification_media_type,
                "task_description": request.task_description,
                "task_category_id": request.task_category_id,
                "task_verification_requirements": request.task_verification_requirements,
            }
        )

        return {"ok": "created"}
    except Exception as e:
        print(e)
        raise HTTPException(status_code=400, detail=str(e))


@router.post(
    "/update-verification-visibility",
    responses={500: {"description": "Generation error"}},
)
async def update_verification_visibility(request: UpdateVerificationVisibilityRequest):
    try:
        update_result = await mongo.verifications.update_one(
            {
                "_id": request.verification_id,
            },
            {"$set": {"is_public": request.is_public}},
        )

        if update_result.modified_count == 0:
            raise HTTPException(
                status_code=404, detail="Verification not found or no changes made"
            )

        return {"success": True}
    except Exception as e:
        logging.error(
            "Something went wrong during update_verification_visibility", exc_info=True
        )
        raise HTTPException(status_code=500, detail=str(e))


@router.get(
    "/get-verifications", responses={500: {"description": "Internal server error"}}
)
async def get_verifications(
    x_user_id: Annotated[CustomObjectId, Header()],
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 10,
):
    try:
        skip = (page - 1) * page_size
        pipeline = [
            {
                "$match": {
                    "assignee_user_id": x_user_id,
                }
            },
            {"$sort": {"_id": -1}},
            {"$skip": skip},
            {"$limit": page_size},
            {
                "$lookup": {
                    "from": "daily_picks",
                    "localField": "task_id",
                    "foreignField": "_id",
                    "as": "task",
                }
            },
            {"$unwind": "$task"},
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
                    "task": 1,
                    "is_public": 1,
                    "text_content": 1,
                }
            },
        ]

        verifications = await mongo.verifications.aggregate(pipeline)

        return [LocationFeedPost(**verification) for verification in verifications]
    except Exception as e:
        print(e)
        raise HTTPException(status_code=500, detail="Internal server error")


@router.get("/get-verification", responses={500: {"description": "Generation error"}})
async def get_user_verification(
    match_id: Optional[Annotated[CustomObjectId, Query()]] = None,
    x_user_id: Annotated[Optional[CustomObjectId], Header()] = None,
    user_id: Optional[Annotated[CustomObjectId, Query()]] = None,
    verification_id: Optional[Annotated[CustomObjectId, Query()]] = None,
):

    try:
        pipeline = []
        if verification_id is not None:
            pipeline.append({"$match": {"_id": verification_id}})
        else:
            pipeline.append(
                {
                    "$match": {
                        "match_id": match_id,
                        "assignee_user_id": (
                            user_id if user_id is not None else x_user_id
                        ),
                    }
                }
            )

        pipeline.extend(
            [
                {
                    "$lookup": {
                        "from": "users",
                        "localField": "assignee_user_id",
                        "foreignField": "_id",
                        "as": "assignee_user",
                    }
                },
                {"$unwind": "$assignee_user"},
            ]
        )

        verifications = await mongo.verifications.aggregate(pipeline)
        verification_list = [verification for verification in verifications]

        if not verification_list:
            raise HTTPException(status_code=404, detail="verification-not-found")

        return Verification(**verification_list[0])
    except Exception as e:
        logging.error(
            "Something went wrong during get_user_verification", exc_info=True
        )
        raise HTTPException(status_code=500, detail=str(e))


@router.put("/update", responses={500: {"description": "Generation error"}})
async def update_user(
    request: UpdateUserRequest, x_user_id: Annotated[CustomObjectId, Header()]
):
    try:
        update_data = request.dict(exclude_none=True)

        await mongo.users.update_one({"_id": x_user_id}, {"$set": update_data})

        return {"ok": True}
    except Exception as e:
        raise HTTPException(status_code=400, detail="update error")


@router.post(
    "/update-waitlist-users", responses={500: {"description": "Generation error"}}
)
async def give_access():
    try:
        users_in_waitlist = await mongo.users.find_all({"is_in_waitlist": True})
        updated = await mongo.users.bulk_update(
            [UpdateOne({"is_in_waitlist": True}, {"$set": {"is_in_waitlist": False}})]
        )

        print(updated)

        await clear_all_user_caches_from_cloudflare_kv()

        for user in users_in_waitlist:
            await send_new_sms_notification(user, "თქვენ გაქვთ წვდომა პლატფორმაზე")

        return {"ok": True}
    except Exception as e:
        logging.debug(e)


@router.put("/upsert-fcm", responses={500: {"description": "Generation error"}})
async def upsert_fcm(x_user_id: Annotated[CustomObjectId, Header()], request: Request):
    try:
        body = await request.json()
        expo_push_token = body.get("data", {}).get("expo_push_token")

        if not expo_push_token:
            raise HTTPException(status_code=400, detail="expo_push_token is required")

        # Update or insert the token for the current user
        update_result = await mongo.push_notification_tokens.update_one(
            {"ownerId": x_user_id},
            {
                "$set": {"expo_push_token": expo_push_token},
            },
            upsert=True,
        )

        # Remove this token from any other users that may have it
        await mongo.push_notification_tokens.delete_all(
            {"ownerId": {"$ne": x_user_id}, "expo_push_token": expo_push_token},
        )

        if update_result.modified_count > 0:
            return {"ok": True, "message": "Token added successfully"}
        elif update_result.upserted_id:
            return {"ok": True, "message": "New document created with token"}
        else:
            return {"ok": True, "message": "Token already exists for this user"}

    except Exception as e:
        logging.error(f"Error in upsert_fcm: {str(e)}")
        raise HTTPException(status_code=500, detail="Internal server error")


@router.get("/get-fcm", responses={500: {"description": "Generation error"}})
async def get_fcm_token(x_user_id: Annotated[CustomObjectId, Header()]):
    try:
        existing_doc = await mongo.push_notification_tokens.find_one(
            {"ownerId": x_user_id}
        )
        if existing_doc and "expo_push_token" in existing_doc:
            return {"expo_push_token": existing_doc["expo_push_token"]}
        else:
            return {"expo_push_token": None}
    except Exception as e:
        logging.error(f"Error in get_fcm_token: {str(e)}")
        raise HTTPException(status_code=500, detail="Internal server error")


@router.delete("/delete-fcm", responses={500: {"description": "Generation error"}})
async def delete_fcm(x_user_id: Annotated[CustomObjectId, Header()], request: Request):
    try:
        body = await request.json()
        expo_push_token = body.get("expo_push_token")

        if not expo_push_token:
            raise HTTPException(status_code=400, detail="expo_push_token is required")

        update_result = await mongo.push_notification_tokens.update_one(
            {"ownerId": x_user_id}, {"$set": {"expo_push_token": None}}
        )

        if update_result.modified_count > 0:
            return {"ok": True, "message": "Token removed successfully"}
        else:
            return {"ok": False, "message": "Token not found"}

    except Exception as e:
        logging.error(f"Error in delete_fcm: {str(e)}")
        raise HTTPException(status_code=500, detail="Internal server error")


@router.post("/location", responses={500: {"description": "Generation error"}})
async def get_user_location(request: Request):
    try:
        body = await request.json()
        latitude = body.get("latitude")
        longitude = body.get("longitude")

        if latitude is None or longitude is None:
            raise HTTPException(
                status_code=400, detail="Latitude and longitude are required"
            )
            # INSERT_YOUR_REWRITE_HERE
        import requests

        api_key = "AIzaSyDwHT9EKvHnwdjj8ErH1knHS43At8CN46g"
        url = f"https://maps.googleapis.com/maps/api/geocode/json?latlng={latitude},{longitude}&key={api_key}"
        response = requests.get(url)
        data = response.json()
        if data["status"] == "OK" and len(data["results"]) > 0:
            city = next(
                (
                    component["long_name"]
                    for component in data["results"][0]["address_components"]
                    if "locality" in component["types"]
                ),
                "Unknown City",
            )
            return {"city": city}
        else:
            raise HTTPException(status_code=404, detail="location-not-found")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.delete(
    "/delete",
    status_code=200,
    responses={500: {"description": "Deletion error"}},
)
async def delete_user(x_user_id: Annotated[CustomObjectId, Header()]) -> dict[str, Any]:
    try:
        # Delete user from users collection
        user_deletion = await mongo.users.delete_one({"_id": x_user_id})

        # Delete user's swipes
        swipes_deletion = await mongo.swipes.delete_all(
            {"$or": [{"initiator_user_id": x_user_id}, {"target_user_id": x_user_id}]}
        )

        # Delete user's matches
        matches_deletion = await mongo.matches.delete_all(
            {"$or": [{"user1_id": x_user_id}, {"user2_id": x_user_id}]}
        )

        # Delete user's task verifications
        verifications_deletion = await mongo.verifications.delete_all(
            {"assignee_user_id": x_user_id}
        )

        liveusers_deletion = await mongo.live_users.delete_all({"author_id": x_user_id})

        # Clear user's cache from Cloudflare KV
        try:
            await clear_all_user_caches_from_cloudflare_kv()
        except Exception as e:
            logging.error(
                f"Error in clear_all_user_caches_from_cloudflare_kv: {str(e)}"
            )

        return {
            "message": "User data deleted successfully",
            "user_deleted": user_deletion.deleted_count,
            "swipes_deleted": swipes_deletion.deleted_count,
            "matches_deleted": matches_deletion.deleted_count,
            "verifications_deleted": verifications_deletion.deleted_count,
        }
    except Exception as e:
        raise HTTPException(
            status_code=500, detail=f"Error deleting user data: {str(e)}"
        )


@router.post(
    "/check_registered_users",
    responses={
        400: {"description": "Invalid input"},
        500: {"description": "Server error"},
    },
)
async def check_registered_users(
    request: CheckRegisteredUsersRequest, x_user_id: Annotated[CustomObjectId, Header()]
):
    if not request.phone_numbers:
        raise HTTPException(status_code=400, detail="No phone numbers provided")
    if len(request.phone_numbers) > 500:
        raise HTTPException(status_code=400, detail="Too many phone numbers provided")

    if len(request.phone_numbers) == 0:
        return []

    # Query the database for existing users
    sanitized_phone_numbers = []
    for phone_number in request.phone_numbers:
        # Remove any non-numeric characters
        sanitized_number = "".join(filter(str.isdigit, phone_number))
        sanitized_number = sanitized_number.replace("995", "")
        # Check if the number is a Georgian phone number or starts with 5
        if sanitized_number.startswith("995") or sanitized_number.startswith("5"):
            sanitized_phone_numbers.append("995" + sanitized_number)

    existing_users = await mongo.users.find_all(
        {"phone_number": {"$in": sanitized_phone_numbers}}
    )

    # Get the list of friends for the current user
    friends = await mongo.friendships.find_all({"user_id": x_user_id})
    friend_ids = set(friendship["friend_id"] for friendship in friends)

    # Filter out users who are already friends
    non_friend_users = [
        user for user in existing_users if user["_id"] not in friend_ids
    ]

    return [User(**user) for user in non_friend_users]


@router.post("/block/{target_id}")
async def block(
    x_user_id: Annotated[CustomObjectId, Header()],
    target_id: Annotated[CustomObjectId, Path()],
    redis: Redis = Depends(get_redis_dependency),
):
    x_user_id_str = str(x_user_id)
    target_id_str = str(target_id)

    redis.sadd(x_user_id_str, target_id_str)
    redis.sadd(target_id_str, x_user_id_str)

    updated = await mongo.friendships.bulk_update(
        [
            UpdateOne(
                {
                    "user_id": ObjectId(x_user_id_str),
                    "friend_id": ObjectId(target_id_str),
                },
                {"$set": {"is_blocked": True}},
            ),
            UpdateOne(
                {
                    "user_id": ObjectId(target_id_str),
                    "friend_id": ObjectId(x_user_id_str),
                },
                {"$set": {"is_blocked": True}},
            ),
        ]
    )

    await mongo.friend_requests.delete_all(
        {
            "$or": [
                {"sender_id": x_user_id, "receiver_id": target_id},
                {"sender_id": target_id, "receiver_id": x_user_id},
            ]
        }
    )

    return {"blockedIds": redis.smembers(x_user_id_str)}


@router.post("/unblock/{target_id}")
async def block(
    x_user_id: Annotated[CustomObjectId, Header()],
    target_id: Annotated[CustomObjectId, Path()],
    redis: Redis = Depends(get_redis_dependency),
):
    x_user_id_str = str(x_user_id)
    target_id_str = str(target_id)

    redis.srem(x_user_id_str, target_id_str)
    redis.srem(target_id_str, x_user_id_str)

    await mongo.friendships.bulk_update(
        [
            UpdateOne(
                {"user_id": x_user_id, "friend_id": target_id},
                {"$set": {"is_blocked": False}},
            ),
            UpdateOne(
                {"user_id": target_id, "friend_id": x_user_id},
                {"$set": {"is_blocked": False}},
            ),
        ]
    )

    return {"blockedIds": redis.smembers(x_user_id_str)}


@router.post("/report/{target_id}")
async def block(
    x_user_id: Annotated[CustomObjectId, Header()], target_id: Annotated[str, Path()]
):
    return {"acceptedToReview": True}


@router.get("/check-username/{username}")
async def check_username(username: str):
    # Check if username exists in database
    existing_user = await mongo.users.find_one({"username": username})

    if existing_user:
        return {"available": False, "message": "სახელი უკვე დაკავებულია"}

    return {"available": True, "message": "სახელი თავისუფალია"}
