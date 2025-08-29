import asyncio
import logging
import os
from typing import Annotated, Any, List, Optional

from fastapi import (
    APIRouter,
    Depends,
    Header,
    HTTPException,
    Path,
    Query,
    Request,
)
from pydantic import BaseModel
from pymongo import UpdateOne
from redis import Redis

from ment_api.common.custom_object_id import CustomObjectId
from ment_api.configurations.config import settings
from ment_api.models.check_registered_users import CheckRegisteredUsersRequest
from ment_api.models.create_user_request import CreateUserRequest
from ment_api.models.location_feed_post import FeedPost
from ment_api.models.update_user_request import UpdateUserRequest
from ment_api.models.update_verification_visibility_request import (
    UpdateVerificationVisibilityRequest,
)
from ment_api.models.user import User, UserPhoto
from ment_api.persistence import mongo
from ment_api.persistence.mongo import create_translation_projection
from ment_api.services.profile_placeholder_generator import set_placeholder_avatar
from ment_api.services.redis_service import get_redis_dependency
from ment_api.utils.language_utils import normalize_language_code

router = APIRouter(
    prefix="/user",
    tags=["user"],
    responses={404: {"description": "Not found"}},
)


class ProfileInformationResponse(BaseModel):
    username: str
    stats: dict
    photos: List[UserPhoto]
    is_friend: bool
    user_id: str


@router.post(
    "/create-user",
    response_model=User,
    operation_id="create_user",
    responses={500: {"description": "Generation error"}},
)
async def create_user(request: CreateUserRequest, http_request: Request):
    try:
        from ment_api.services.country_service import get_country_for_request

        country_code, _, _ = get_country_for_request(http_request)

        update_fields = {}
        if country_code == "US":
            update_fields["preferred_news_feed_id"] = "687960db5051460a7afd6e63"
            update_fields["preferred_fact_check_feed_id"] = "67bb256786841cb3e7074bcd"
            if request.preferred_content_language:
                update_fields["preferred_content_language"] = (
                    request.preferred_content_language
                )
            else:
                update_fields["preferred_content_language"] = "english"
        else:
            if request.preferred_content_language:
                update_fields["preferred_content_language"] = (
                    request.preferred_content_language
                )
            else:
                update_fields["preferred_content_language"] = "georgian"
            update_fields["preferred_news_feed_id"] = "687960db5051460a7afd6e63"
            update_fields["preferred_fact_check_feed_id"] = "67bb256786841cb3e7074bcd"

        await mongo.users.insert_one(
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
                "is_in_waitlist": False,
                **update_fields,
            }
        )
        await set_placeholder_avatar(request.external_user_id, request.username, 256)
        return User(**request.dict())
    except Exception as e:
        logging.error("Something went wrong during 'create_user'", exc_info=True)
        raise HTTPException(status_code=400, detail=str(e))


@router.get(
    "/get-user",
    response_model=User,
    operation_id="get_user",
    responses={404: {"description": "User not found"}},
)
async def get_user(request: Request):
    # Get the Supabase user ID from the request state
    supabase_user_id = request.state.supabase_user_id
    # Query using the external_user_id field which contains the Supabase ID
    user = await mongo.users.find_one({"external_user_id": supabase_user_id})
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    return User(**user)


@router.post(
    "/update-verification-visibility",
    response_model=dict,
    operation_id="update_verification_visibility",
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
    "/get-verifications",
    response_model=List[FeedPost],
    operation_id="get_verifications",
    responses={500: {"description": "Internal server error"}},
)
async def get_verifications(
    request: Request,
    accept_language: Annotated[str, Header()] = "ka",
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 10,
    target_user_id: Optional[Annotated[str, Query()]] = None,
):
    # Normalize language code
    accept_language = normalize_language_code(accept_language)

    try:
        external_user_id = request.state.supabase_user_id

        skip = (page - 1) * page_size
        match_condition = {
            "assignee_user_id": target_user_id or external_user_id,
        }

        if target_user_id != external_user_id:
            match_condition["is_public"] = True

        pipeline = [
            {"$match": match_condition},
            {"$sort": {"_id": -1}},
            {"$skip": skip},
            {"$limit": page_size},
            {
                "$lookup": {
                    "from": "daily_picks",
                    "localField": "feed_id",
                    "foreignField": "_id",
                    "as": "task",
                }
            },
            {"$unwind": "$task"},
        ]

        pipeline.extend(
            [
                {
                    "$lookup": {
                        "from": "users",
                        "localField": "assignee_user_id",
                        "foreignField": "external_user_id",
                        "as": "assignee_user",
                    }
                },
                {"$unwind": "$assignee_user"},
            ]
        )

        # Create translation projections for multilingual fields
        translatable_fields = ["text_content", "ai_video_summary"]
        fact_check_fields = ["fact_check_data.reason", "fact_check_data.reason_summary"]

        translation_projections = create_translation_projection(
            translatable_fields, accept_language
        )
        fact_check_projections = create_translation_projection(
            fact_check_fields, accept_language
        )

        pipeline.append(
            {
                "$project": {
                    "_id": 1,
                    "assignee_user_id": 1,
                    "feed_id": 1,
                    "state": 1,
                    "transcode_job_name": 1,
                    "verified_media_playback": 1,
                    "assignee_user": 1,
                    "last_modified_date": 1,
                    "task": 1,
                    "is_public": 1,
                    "is_live": 1,
                    "is_space": 1,
                    "space_state": 1,
                    "scheduled_at": 1,
                    "has_recording": 1,
                    "livekit_room_name": 1,
                    "image_gallery_with_dims": 1,
                    "is_generated_news": 1,
                    "external_video": 1,
                    "ai_video_summary_status": 1,
                    "metadata_status": 1,
                    "fact_check_status": 1,
                    "fact_check_data": {
                        "factuality": "$fact_check_data.factuality",
                        "reason": fact_check_projections["fact_check_data.reason"],
                        "reason_summary": fact_check_projections[
                            "fact_check_data.reason_summary"
                        ],
                        "references": "$fact_check_data.references",
                    },
                    "preview_data": 1,
                    # Translated fields with fallback logic
                    **translation_projections,
                }
            }
        )

        verifications = await mongo.verifications.aggregate(pipeline)

        return [FeedPost(**verification) for verification in verifications]
    except Exception as e:
        print(e)
        raise HTTPException(status_code=500, detail="Internal server error")


@router.get(
    "/get-verification",
    response_model=FeedPost,
    operation_id="get_user_verification",
    responses={500: {"description": "Generation error"}},
)
async def get_user_verification(
    request: Request,
    verification_id: CustomObjectId,
    accept_language: Annotated[str, Header()] = "ka",
) -> FeedPost:
    # Normalize language code
    accept_language = normalize_language_code(accept_language)

    is_guest: bool = request.state.is_guest

    # Always filter by verification_id since it's required
    pipeline = [{"$match": {"_id": verification_id}}]

    # Add user lookup if not guest
    if not is_guest:
        pipeline.extend(
            [
                {
                    "$lookup": {
                        "from": "users",
                        "localField": "assignee_user_id",
                        "foreignField": "external_user_id",
                        "as": "assignee_user",
                    }
                },
                {"$unwind": "$assignee_user"},
            ]
        )

    # Create translation projections for multilingual fields
    translatable_fields = [
        "text_content",
        "title",
        "text_summary",
        "government_summary",
        "opposition_summary",
        "neutral_summary",
    ]
    fact_check_fields = ["fact_check_data.reason", "fact_check_data.reason_summary"]

    translation_projections = create_translation_projection(
        translatable_fields, accept_language
    )
    fact_check_projections = create_translation_projection(
        fact_check_fields, accept_language
    )

    # Add projection pipeline using FeedPost attributes with language-specific formatting
    pipeline.append(
        {
            "$project": {
                "_id": 1,
                "assignee_user_id": 1,
                "feed_id": 1,
                "state": 1,
                "transcode_job_name": 1,
                "verified_media_playback": 1,
                "news_id": 1,
                "visited_urls": 1,
                "read_urls": 1,
                "is_public": 1,
                "is_live": 1,
                "is_space": 1,
                "has_recording": 1,
                "livekit_room_name": 1,
                "space_state": 1,
                "scheduled_at": 1,
                "assignee_user": 1,
                "last_modified_date": 1,
                "image_gallery_with_dims": 1,
                "feed": 1,
                "is_factchecked": 1,
                "text_content_in_english": 1,
                "news_date": 1,
                "sources": 1,
                "fact_check_id": 1,
                "fact_check_data": {
                    "factuality": "$fact_check_data.factuality",
                    "reason": fact_check_projections["fact_check_data.reason"],
                    "reason_summary": fact_check_projections[
                        "fact_check_data.reason_summary"
                    ],
                    "references": "$fact_check_data.references",
                },
                "fact_check_status": 1,
                "is_generated_news": 1,
                "external_video": 1,
                "ai_video_summary": 1,
                "ai_video_summary_status": 1,
                "metadata_status": 1,
                "ai_video_summary_error": 1,
                "social_media_scrape_details": 1,
                "social_media_scrape_status": 1,
                "social_media_scrape_error": 1,
                "preview_data": 1,
                # Translated fields with fallback logic
                **translation_projections,
            }
        }
    )

    verifications = await mongo.verifications.aggregate(pipeline)
    verification_list: List[dict] = [verification for verification in verifications]

    if not verification_list:
        raise HTTPException(status_code=404, detail="verification-not-found")

    return FeedPost(**verification_list[0])


@router.put(
    "/update",
    response_model=dict,
    operation_id="update_user",
    responses={500: {"description": "Generation error"}},
)
async def update_user(
    request: Request,
    update_user_request: UpdateUserRequest,
):
    try:
        external_user_id = request.state.supabase_user_id
        update_data = update_user_request.dict(exclude_none=True)
        await mongo.users.update_one(
            {"external_user_id": external_user_id}, {"$set": update_data}
        )

        return {"ok": True}
    except Exception:
        raise HTTPException(status_code=400, detail="update error")


class FCPResponse(BaseModel):
    ok: bool
    message: str
    expo_push_token: Optional[str] = None


@router.put(
    "/upsert-fcm",
    response_model=FCPResponse,
    operation_id="upsert_fcm",
    responses={500: {"description": "Generation error"}},
)
async def upsert_fcm(request: Request):
    try:
        external_user_id = request.state.supabase_user_id
        body = await request.json()
        expo_push_token = body.get("expo_push_token")

        if not expo_push_token:
            raise HTTPException(status_code=400, detail="expo_push_token is required")

        # Update or insert the token for the current user
        update_result = await mongo.push_notification_tokens.update_one(
            {"ownerId": external_user_id},
            {
                "$set": {"expo_push_token": expo_push_token},
            },
            upsert=True,
        )

        # Remove this token from any other users that may have it
        await mongo.push_notification_tokens.delete_all(
            {"ownerId": {"$ne": external_user_id}, "expo_push_token": expo_push_token},
        )

        if update_result.modified_count > 0:
            return FCPResponse(ok=True, message="Token added successfully")
        elif update_result.upserted_id:
            return FCPResponse(ok=True, message="New document created with token")
        else:
            return FCPResponse(ok=True, message="Token already exists for this user")

    except Exception as e:
        logging.error(f"Error in upsert_fcm: {str(e)}")
        raise HTTPException(status_code=500, detail="Internal server error")


@router.get(
    "/get-fcm",
    response_model=FCPResponse,
    operation_id="get_fcm_token",
    responses={500: {"description": "Generation error"}},
)
async def get_fcm_token(request: Request):
    try:
        external_user_id = request.state.supabase_user_id
        existing_doc = await mongo.push_notification_tokens.find_one(
            {"ownerId": external_user_id}
        )
        if existing_doc and "expo_push_token" in existing_doc:
            return FCPResponse(
                ok=True,
                message="Token found",
                expo_push_token=existing_doc["expo_push_token"],
            )
        else:
            return FCPResponse(ok=True, message="Token not found", expo_push_token=None)
    except Exception as e:
        logging.error(f"Error in get_fcm_token: {str(e)}")
        raise HTTPException(status_code=500, detail="Internal server error")


@router.delete(
    "/delete-fcm",
    response_model=FCPResponse,
    operation_id="delete_fcm",
    responses={500: {"description": "Generation error"}},
)
async def delete_fcm(request: Request):
    try:
        external_user_id = request.state.supabase_user_id

        update_result = await mongo.push_notification_tokens.update_one(
            {"ownerId": external_user_id}, {"$set": {"expo_push_token": None}}
        )

        if update_result.modified_count > 0:
            return FCPResponse(ok=True, message="Token removed successfully")
        else:
            return FCPResponse(ok=False, message="Token not found")

    except Exception as e:
        logging.error(f"Error in delete_fcm: {str(e)}")
        raise HTTPException(status_code=500, detail="Internal server error")


class UserLocationRequest(BaseModel):
    latitude: float
    longitude: float


class UserLocationResponse(BaseModel):
    city: str


@router.post(
    "/location",
    response_model=UserLocationResponse,
    operation_id="get_user_location",
    responses={500: {"description": "Generation error"}},
)
async def get_user_location(request: UserLocationRequest):
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

        api_key = settings.google_maps_api_key
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
            return UserLocationResponse(city=city)
        else:
            raise HTTPException(status_code=404, detail="location-not-found")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.delete(
    "/delete",
    status_code=200,
    operation_id="delete_user",
    responses={500: {"description": "Deletion error"}},
)
async def delete_user(request: Request) -> dict[str, Any]:
    try:
        external_user_id = request.state.supabase_user_id
        # Delete user from users collection
        await mongo.users.delete_one({"external_user_id": external_user_id})

        await mongo.live_users.delete_all({"author_id": external_user_id})

        return {
            "message": "User data deleted successfully",
        }
    except Exception as e:
        raise HTTPException(
            status_code=500, detail=f"Error deleting user data: {str(e)}"
        )


@router.post(
    "/check_registered_users",
    response_model=List[User],
    operation_id="check_registered_users",
    responses={
        400: {"description": "Invalid input"},
        500: {"description": "Server error"},
    },
)
async def check_registered_users(
    check_registered_users_request: CheckRegisteredUsersRequest,
    request: Request,
):
    external_user_id = request.state.supabase_user_id
    if not check_registered_users_request.phone_numbers:
        raise HTTPException(status_code=400, detail="No phone numbers provided")
    if len(check_registered_users_request.phone_numbers) > 500:
        raise HTTPException(status_code=400, detail="Too many phone numbers provided")

    if len(check_registered_users_request.phone_numbers) == 0:
        return []

    # Query the database for existing users
    sanitized_phone_numbers = []
    for phone_number in check_registered_users_request.phone_numbers:
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
    friends = await mongo.friendships.find_all({"user_id": external_user_id})
    friend_ids = set(friendship["friend_id"] for friendship in friends)
    # Filter out users who are already friends
    non_friend_users = [
        user for user in existing_users if user["external_user_id"] not in friend_ids
    ]

    return [User(**user) for user in non_friend_users]


@router.post("/block/{target_id}")
async def block(
    request: Request,
    target_id: Annotated[CustomObjectId, Path()],
    redis: Redis = Depends(get_redis_dependency),
):
    external_user_id = request.state.supabase_user_id
    target_id_str = str(target_id)

    redis.sadd(external_user_id, target_id_str)
    redis.sadd(target_id_str, external_user_id)

    await mongo.friendships.bulk_update(
        [
            UpdateOne(
                {
                    "user_id": external_user_id,
                    "friend_id": target_id,
                },
                {"$set": {"is_blocked": True}},
            ),
            UpdateOne(
                {
                    "user_id": target_id,
                    "friend_id": external_user_id,
                },
                {"$set": {"is_blocked": True}},
            ),
        ]
    )

    await mongo.friend_requests.delete_all(
        {
            "$or": [
                {"sender_id": external_user_id, "receiver_id": target_id},
                {"sender_id": target_id, "receiver_id": external_user_id},
            ]
        }
    )

    return {"blockedIds": redis.smembers(external_user_id)}


@router.post("/unblock/{target_id}")
async def unblock(
    request: Request,
    target_id: str,
    redis: Redis = Depends(get_redis_dependency),
):
    external_user_id = request.state.supabase_user_id
    target_id_str = target_id

    redis.srem(external_user_id, target_id_str)
    redis.srem(target_id_str, external_user_id)

    await mongo.friendships.bulk_update(
        [
            UpdateOne(
                {"user_id": external_user_id, "friend_id": target_id},
                {"$set": {"is_blocked": False}},
            ),
            UpdateOne(
                {"user_id": target_id, "friend_id": external_user_id},
                {"$set": {"is_blocked": False}},
            ),
        ]
    )

    return {"blockedIds": redis.smembers(external_user_id)}


class ReportRequest(BaseModel):
    target_id: str


class ReportResponse(BaseModel):
    acceptedToReview: bool


@router.post("/report/{target_id}", response_model=dict)
async def report(request: ReportRequest):
    logging.error(
        "Report endpoint not fully implemented but still logging just in case: "
        + request.target_id
    )
    return ReportResponse(acceptedToReview=True)


class CheckUsernameRequest(BaseModel):
    username: str


class CheckUsernameResponse(BaseModel):
    available: bool
    message: str


@router.get(
    "/check-username/{username}",
    response_model=CheckUsernameResponse,
    responses={400: {"description": "Invalid input"}},
)
async def check_username(username: str):
    # Check if username exists in database
    existing_user = await mongo.users.find_one({"username": username})

    if existing_user:
        return CheckUsernameResponse(available=False, message="სახელი უკვე დაკავებულია")

    return CheckUsernameResponse(available=True, message="სახელი თავისუფალია")


@router.get("/profile/{user_id}", response_model=ProfileInformationResponse)
async def get_user_profile(
    user_id: str,
    request: Request,
    redis: Redis = Depends(get_redis_dependency),
):
    external_user_id = request.state.supabase_user_id
    # Get user data and run other queries in parallel
    user_future = mongo.users.find_one({"external_user_id": user_id})

    # pipeline = [
    #     {"$match": {"assignee_user_id": user_id}},
    #     {
    #         "$lookup": {
    #             "from": "likes",
    #             "localField": "_id",
    #             "foreignField": "verification_id",
    #             "as": "likes",
    #         }
    #     },
    #     {"$project": {"like_count": {"$size": "$likes"}}},
    #     {"$group": {"_id": None, "total_likes": {"$sum": "$like_count"}}},
    # ]
    # likes_future = mongo.verifications.aggregate(pipeline)
    # verifications_future = mongo.verifications.find_all({"assignee_user_id": user_id})
    is_friend_future = mongo.friendships.find_one(
        {"user_id": external_user_id, "friend_id": user_id, "is_blocked": {"$ne": True}}
    )

    # Wait for all futures to complete
    user, is_friend = await asyncio.gather(
        user_future,
        # likes_future,
        # verifications_future,
        is_friend_future,
    )

    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    # total_likes = likes_result[0]["total_likes"] if likes_result else 0

    # Get total views from Redis
    # total_views = 0
    # redis_keys = [f"impressions:{str(v['_id'])}" for v in verifications]
    # if redis_keys:
    # view_counts = redis.mget(redis_keys)
    # total_views = sum(int(count) for count in view_counts if count)

    return ProfileInformationResponse(
        username=user["username"],
        stats={"likes": 0, "views": 0},
        photos=user.get("photos", []),
        is_friend=is_friend is not None,
        user_id=str(user_id),
    )


@router.get(
    "/profile/username/{username}",
    response_model=dict,
    operation_id="get_user_profile_by_username",
)
async def get_user_profile_by_username(username: str):
    IS_DEV = os.environ.get("ENV") == "dev"

    pipeline = [
        {
            "$search": {
                "index": (
                    "username_search_index_dev"
                    if IS_DEV
                    else "username_search_index_prod"
                ),
                "wildcard": {
                    "query": f"*{username}*",
                    "path": "username",
                    "allowAnalyzedField": True,
                },
            }
        },
        {"$limit": 1},
    ]

    users = await mongo.users.aggregate(pipeline)

    user = users[0] if users else None
    if user:
        raise HTTPException(status_code=400, detail="Username exists")
    return {"ok": True}
