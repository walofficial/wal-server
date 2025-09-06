import logging
from typing import Annotated, Optional

from bson.objectid import ObjectId
from fastapi import (
    APIRouter,
    HTTPException,
    Body,
    Request,
)
from livekit.api.access_token import AccessToken, VideoGrants
from livekit.api.room_service import RoomService

from livekit.protocol.room import (
    UpdateParticipantRequest,
    DeleteRoomRequest,
    ListParticipantsRequest,
    RoomParticipantIdentity,
    ListRoomsRequest,
)
from ment_api.configurations.config import settings
import aiohttp
from ment_api.persistence import mongo

from ment_api.common.custom_object_id import CustomObjectId
from ment_api.services.google_tasks_service import create_http_task
from ment_api.services.notification_service import send_new_sms_notification
import asyncio
from datetime import datetime, timezone
import uuid
import json
from typing import Dict
from pydantic import BaseModel
from ment_api.models.user import User


BATCH_SIZE = 100  # Number of notifications to send in each batch
BATCH_DELAY = 1  # Delay in seconds between batches

router = APIRouter(
    prefix="/space",
    tags=["space"],
    responses={404: {"description": "Not found"}},
)

logger = logging.getLogger(__name__)


async def get_room_service():
    return RoomService(
        session=aiohttp.ClientSession(connector=aiohttp.TCPConnector(ssl=False)),
        url=settings.livekit_url,
        api_key=settings.livekit_api_key,
        api_secret=settings.livekit_api_secret,
    )


class RoomPreviewData(BaseModel):
    description: str
    number_of_participants: int
    exists: bool
    space_state: str
    is_subscribed: bool


@router.get(
    "/preview/{livekit_room_name}",
    operation_id="get_room_preview_data",
    response_model=RoomPreviewData,
)
async def get_room_preview_data(
    request: Request,
    livekit_room_name: str,
):
    try:
        user_id = request.state.supabase_user_id
        # First get the verification doc
        verification_doc = await mongo.verifications.find_one(
            {"livekit_room_name": livekit_room_name}
        )
        if not verification_doc:
            raise HTTPException(status_code=404, detail="Verification not found")

        room_service = await get_room_service()
        # Then get the other data
        is_subscribed, response = await asyncio.gather(
            mongo.subscribed_space_users.find_one(
                {"user_id": user_id, "verification_id": verification_doc["_id"]}
            ),
            room_service.list_rooms(ListRoomsRequest(names=[livekit_room_name])),
        )

        if len(response.rooms) == 0:
            return {
                "description": verification_doc["text_content"],
                "number_of_participants": 0,
                "exists": False,
                "space_state": verification_doc["space_state"],
                "is_subscribed": is_subscribed is not None,
            }

        response = await room_service.list_participants(
            ListParticipantsRequest(room=livekit_room_name)
        )

        participants = response.participants

        return {
            "description": verification_doc["text_content"],
            "number_of_participants": len(participants),
            "exists": True,
            "space_state": verification_doc["space_state"],
            "is_subscribed": is_subscribed is not None,
        }
    except Exception as e:
        logger.error(f"Error in get_room_preview_data: {str(e)}")
        if isinstance(e, HTTPException):
            raise
        raise HTTPException(status_code=400, detail="Cannot get preview data")


class StopStreamRequest(BaseModel):
    livekit_room_name: str


@router.post("/stop-stream", operation_id="stop_stream")
async def stop_stream(
    request: Request,
    request_body: StopStreamRequest,
):
    room_service = await get_room_service()
    await room_service.delete_room(
        DeleteRoomRequest(room=request_body.livekit_room_name)
    )
    await mongo.verifications.update_one(
        {"livekit_room_name": request_body.livekit_room_name},
        {
            "$set": {
                "live_ended_at": datetime.now(timezone.utc),
            }
        },
    )
    return {"ok": True}


class CreateStreamRequest(BaseModel):
    livekit_room_name: str


@router.post("/create-stream", operation_id="create_stream")
async def create_stream(
    request: Request,
    request_body: CreateStreamRequest,
):
    user_id = request.state.supabase_user_id
    user, verification_doc = await asyncio.gather(
        mongo.users.find_one({"external_user_id": user_id}),
        mongo.verifications.find_one(
            {"livekit_room_name": request_body.livekit_room_name}
        ),
    )

    if not verification_doc:
        raise HTTPException(status_code=404, detail="Verification not found.")

    if verification_doc["space_state"] == "ended":
        raise HTTPException(status_code=400, detail="Space has ended.")

    is_host = verification_doc["assignee_user_id"] == user_id
    user = await mongo.users.find_one({"external_user_id": user_id})
    userObj = User(**user)

    default_grants = VideoGrants(
        room_join=True,
        room=request_body.livekit_room_name,
        can_publish=False,
        can_publish_data=True,
        can_subscribe=True,
        can_update_own_metadata=True,
    )

    if is_host:
        default_grants.can_publish = True
    token = (
        AccessToken(settings.livekit_api_key, settings.livekit_api_secret)
        .with_identity(str(user_id))
        .with_name(request_body.livekit_room_name)
        .with_metadata(
            json.dumps(
                {
                    "is_host": is_host,
                    "username": userObj.username,
                    "avatar_image": userObj.photos[0]["image_url"][0],
                }
            )
        )
        .with_grants(default_grants)
    )

    livekit_token = token.to_jwt()

    return {
        "verification_id": str(verification_doc["_id"]),
        "is_host": is_host,
        "livekit_token": livekit_token,
        "livekit_room_name": request_body.livekit_room_name,
    }


class CreateSpaceResponse(BaseModel):
    room_name: str
    verification_id: str
    space_state: str
    scheduled_at: datetime


@router.post(
    "/create-space",
    operation_id="create_space",
    response_model=CreateSpaceResponse,
)
async def create_space(
    request: Request,
    scheduled_at: Annotated[Optional[datetime], Body()] = None,
    text_content: Annotated[Optional[str], Body()] = None,
    feed_id: Annotated[Optional[CustomObjectId], Body()] = None,
):
    """
    Creates a 'space' (analogous to a LiveKit room) and inserts a new Verification document.
    - If scheduled_at is in the future, we set the space_state to 'scheduled'.
    - If no scheduled_at, we treat it as 'started' immediately.
    - Optionally insert text_content to the verification doc.
    - Also store 'creator_identity' with x_user_id in the doc and plan to store metadata in livekit.
    """
    user_id = request.state.supabase_user_id
    # Generate a unique name for the "space" (room).
    room_name = f"ment-space-{uuid.uuid4()}"
    current_time = datetime.now(timezone.utc)

    # Decide space_state based on scheduled_at
    if scheduled_at and scheduled_at > current_time:
        space_state = "scheduled"
    else:
        space_state = "ready_to_start"

    # Insert a corresponding Verification doc
    insert_doc = {
        "assignee_user_id": user_id,
        "feed_id": feed_id,
        "state": "READY_TO_USE",
        "is_live": False,
        "is_space": True,
        "has_recording": False,
        "text_content": text_content,
        "livekit_room_name": room_name,
        "last_modified_date": current_time,
        "is_public": True,
        "space_state": space_state,  # Might require adding this field to Verification model
        "scheduled_at": scheduled_at,
        "verified_media_playback": {},
    }
    new_verification = await mongo.verifications.insert_one(insert_doc)
    inserted_id = new_verification.inserted_id

    # If scheduled_at is in the future, set up a Google Cloud Task that triggers start endpoint
    if space_state == "scheduled":
        gtask_payload = {
            "verification_id": str(inserted_id),
        }
        create_http_task(
            f"{settings.api_url}/space/trigger-space-start",
            gtask_payload,
            scheduled_at,
        )

    return {
        "room_name": room_name,
        "verification_id": str(inserted_id),
        "space_state": space_state,
        "scheduled_at": scheduled_at,
    }


class InviteToStageRequest(BaseModel):
    livekit_room_name: str
    participant_identity: str


@router.post("/invite-to-stage", operation_id="invite_to_stage")
async def invite_to_stage(
    request: Request,
    request_body: InviteToStageRequest,
):
    """
    Allows the creator (x_user_id) to invite another participant (invitee_identity) to the stage by giving them publish permissions if they had their hand raised.
    """
    user_id = request.state.supabase_user_id
    verification_doc = await mongo.verifications.find_one(
        {"livekit_room_name": request_body.livekit_room_name}
    )
    if not verification_doc:
        raise HTTPException(status_code=404, detail="Verification not found.")

    # Ensure caller is the original space creator
    creator_identity = str(verification_doc["assignee_user_id"])
    if str(user_id) != creator_identity:
        raise HTTPException(
            status_code=403, detail="Only the creator can invite to stage."
        )

    room_service = await get_room_service()
    participant = await room_service.get_participant(
        RoomParticipantIdentity(
            room=verification_doc["livekit_room_name"],
            identity=request_body.participant_identity,
        )
    )
    permission = participant.permission
    metadata = get_or_create_participant_metadata(participant)
    metadata["invited_to_stage"] = True

    if metadata["hand_raised"]:
        permission.can_publish = True

    await room_service.update_participant(
        UpdateParticipantRequest(
            room=verification_doc["livekit_room_name"],
            identity=request_body.participant_identity,
            metadata=json.dumps(metadata),
            permission=permission,
        )
    )

    return {
        "participant_identity": request_body.participant_identity,
        "invited_to_stage": True,
    }


class RemoveFromStageRequest(BaseModel):
    livekit_room_name: str
    participant_identity: str


@router.post("/remove-from-stage", operation_id="remove_from_stage")
async def remove_from_stage(
    request: Request,
    request_body: RemoveFromStageRequest,
):
    """
    Removes participant from stage.
    If participant_identity not given, remove the caller themself from stage.
    """
    user_id = request.state.supabase_user_id
    verification_doc = await mongo.verifications.find_one(
        {"livekit_room_name": request_body.livekit_room_name}
    )
    if not verification_doc:
        raise HTTPException(status_code=404, detail="Verification not found.")

    participant_identity = request_body.participant_identity

    # Check if caller is either the creator or the same user
    creator_identity = str(verification_doc["assignee_user_id"])
    if creator_identity != str(user_id) and participant_identity != str(user_id):
        raise HTTPException(
            status_code=403,
            detail="Only the creator or the participant themself can remove from stage.",
        )

    room_service = await get_room_service()
    response = await room_service.list_rooms(
        ListRoomsRequest(names=[verification_doc["livekit_room_name"]])
    )

    if not response.rooms:
        raise HTTPException(status_code=404, detail="Room does not exist")

    room = response.rooms[0]

    participant = await room_service.get_participant(
        RoomParticipantIdentity(room=room.name, identity=participant_identity)
    )
    permission = participant.permission
    metadata = get_or_create_participant_metadata(participant)

    # Reset everything and disallow them from publishing (this will un-publish them automatically)
    metadata["hand_raised"] = False
    metadata["invited_to_stage"] = False
    permission.can_publish = False

    await room_service.update_participant(
        UpdateParticipantRequest(
            room=verification_doc["livekit_room_name"],
            identity=request_body.participant_identity,
            metadata=json.dumps(metadata),
            permission=permission,
        )
    )

    return {
        "participant_identity": request_body.participant_identity,
        "removed_from_stage": True,
    }


class RaiseHandRequest(BaseModel):
    livekit_room_name: str


@router.post("/raise-hand", operation_id="raise_hand")
async def raise_hand(
    request: Request,
    request_body: RaiseHandRequest,
):
    """
    Allows participant to set 'hand_raised' = True in metadata.
    If they've also been invited, canPublish might be granted.
    """
    user_id = request.state.supabase_user_id
    verification_doc = await mongo.verifications.find_one(
        {"livekit_room_name": request_body.livekit_room_name}
    )
    if not verification_doc:
        raise HTTPException(status_code=404, detail="Verification not found.")
    if verification_doc["space_state"] == "ended":
        raise HTTPException(status_code=400, detail="Space has ended.")

    room_service = await get_room_service()
    # First check if room exists
    response = await room_service.list_rooms(
        ListRoomsRequest(names=[request_body.livekit_room_name])
    )
    if not response.rooms:
        raise HTTPException(status_code=404, detail="Room does not exist")

    try:
        participant = await room_service.get_participant(
            RoomParticipantIdentity(
                room=verification_doc["livekit_room_name"],
                identity=str(user_id),
            )
        )
    except Exception as e:
        logger.error(f"Error getting participant: {str(e)}")
        raise HTTPException(status_code=404, detail="Participant not found in room")

    permission = participant.permission or {}
    metadata = get_or_create_participant_metadata(participant)
    metadata["hand_raised"] = True

    if metadata["invited_to_stage"]:
        permission.can_publish = True
    await room_service.update_participant(
        UpdateParticipantRequest(
            room=request_body.livekit_room_name,
            identity=str(user_id),
            metadata=json.dumps(metadata),
            permission=permission,
        )
    )


class SubscribeSpaceRequest(BaseModel):
    livekit_room_name: str


@router.post("/subscribe-space", operation_id="subscribe_space")
async def subscribe_space(
    request: Request,
    request_body: SubscribeSpaceRequest,
):
    """
    Allows a user to subscribe to a space.
    For example, track in a separate 'subscriptions' collection or a user list in the verification doc.
    """
    user_id = request.state.supabase_user_id
    verification_doc = await mongo.verifications.find_one(
        {"livekit_room_name": request_body.livekit_room_name}
    )
    if not verification_doc:
        raise HTTPException(status_code=404, detail="Verification not found.")

    await mongo.subscribed_space_users.insert_one(
        {
            "verification_id": verification_doc["_id"],
            "user_id": user_id,
            "subscribed_at": datetime.now(timezone.utc),
        }
    )

    return {
        "verification_id": str(verification_doc["_id"]),
        "subscribed_user_id": str(user_id),
    }


@router.post("/trigger-space-start", operation_id="trigger_space_start")
async def trigger_space_start(payload: Annotated[Dict, Body(...)]):
    """
    Called by a scheduled Cloud Task (or similar) to flip a 'scheduled' space to 'started',
    then send notifications to all subscribed users in batches.
    """
    verification_id = ObjectId(payload["verification_id"])
    try:
        verification_doc = await mongo.verifications.find_one({"_id": verification_id})
        if not verification_doc:
            raise HTTPException(status_code=404, detail="Verification not found.")
        if verification_doc.get("space_state") != "scheduled":
            raise HTTPException(status_code=400, detail="Not in scheduled state.")

        await mongo.verifications.update_one(
            {"_id": verification_id},
            {
                "$set": {
                    "space_state": "started",
                    "last_modified_date": datetime.now(timezone.utc),
                }
            },
        )

        # Get all subscribers with their push tokens in one query using aggregation
        pipeline = [
            {"$match": {"verification_id": verification_id}},
            {
                "$lookup": {
                    "from": "push-notification-tokens",
                    "localField": "user_id",
                    "foreignField": "ownerId",
                    "as": "push_token",
                }
            },
            {"$unwind": {"path": "$push_token", "preserveNullAndEmptyArrays": False}},
            {"$match": {"push_token.expo_push_token": {"$exists": True}}},
        ]

        subscribers = await mongo.subscribed_space_users.aggregate(pipeline)

        if not subscribers:
            logger.info("No subscribers found with push tokens")
            return {"verification_id": str(verification_id), "space_state": "started"}

        # Prepare notification data
        notification_title = "ოთახი დაიწყო"
        notification_body = verification_doc.get("text_content", "A space has started!")
        notification_data = {
            "type": "space_started",
            "verification_id": str(verification_id),
            "room_name": verification_doc["livekit_room_name"],
        }

        # Remove duplicate tokens
        seen_tokens = set()
        unique_subscribers = []
        for sub in subscribers:
            token = sub["push_token"]["expo_push_token"]
            if token not in seen_tokens:
                seen_tokens.add(token)
                unique_subscribers.append(sub)

        # Group subscribers into batches
        subscriber_batches = [
            unique_subscribers[i : i + BATCH_SIZE]
            for i in range(0, len(unique_subscribers), BATCH_SIZE)
        ]

        logger.info(
            f"Sending notifications to {len(unique_subscribers)} users in {len(subscriber_batches)} batches"
        )

        # Process each batch
        for batch_index, subscriber_batch in enumerate(subscriber_batches):
            try:
                notification_tasks = []

                for subscriber in subscriber_batch:
                    notification_tasks.append(
                        send_new_sms_notification(
                            token=subscriber["push_token"]["expo_push_token"],
                            title=notification_title,
                            message=notification_body,
                            data=notification_data,
                        )
                    )

                # Send notifications in parallel within the batch
                await asyncio.gather(*notification_tasks)

                # Add delay between batches (except for the last batch)
                if batch_index < len(subscriber_batches) - 1:
                    await asyncio.sleep(BATCH_DELAY)

                logger.info(
                    f"Processed batch {batch_index + 1}/{len(subscriber_batches)}"
                )

            except Exception as e:
                logger.error(f"Error processing batch {batch_index + 1}: {str(e)}")
                continue

        logger.info("Finished sending space start notifications")
        return {"verification_id": str(verification_id), "space_state": "started"}

    except Exception as e:
        logger.error(f"Error in trigger_space_start: {str(e)}")
        raise


def get_or_create_participant_metadata(participant):
    if participant.metadata:
        metadata = json.loads(participant.metadata)
        if "invited_to_stage" not in metadata:
            metadata["invited_to_stage"] = False

        if "hand_raised" not in metadata:
            metadata["hand_raised"] = False

        return metadata

    return {
        "hand_raised": False,
        "invited_to_stage": False,
        "avatar_image": f"https://api.multiavatar.com/{participant.identity}.png",
    }
