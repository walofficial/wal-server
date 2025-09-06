import json
import uuid
from datetime import datetime, timezone
from typing import Annotated, Optional

import aiohttp
from fastapi import APIRouter, Body, Header, Query, Request, HTTPException
from livekit.api.access_token import AccessToken, VideoGrants
from livekit.api.ingress_service import IngressService
from livekit.api.room_service import RoomService
from livekit.protocol.egress import (
    AutoParticipantEgress,
    EncodingOptionsPreset,
    GCPUpload,
    SegmentedFileOutput,
)
from livekit.protocol.ingress import CreateIngressRequest, IngressInput
from livekit.protocol.room import CreateRoomRequest, RoomConfiguration, RoomEgress, DeleteRoomRequest
from pydantic import BaseModel

from ment_api.common.custom_object_id import CustomObjectId
from ment_api.configurations.config import settings
from ment_api.models.verification_state import VerificationState
from ment_api.persistence import mongo
from livekit.api.webhook import WebhookReceiver
from livekit.api.access_token import TokenVerifier

router = APIRouter(prefix="/live", tags=["live"])
LIVEKIT_API_KEY = settings.livekit_api_key
LIVEKIT_API_SECRET = settings.livekit_api_secret


class GetLiveStreamTokenResponse(BaseModel):
    livekit_token: str
    room_name: str


class RequestLivekitIngressResponse(BaseModel):
    livekit_token: str
    room_name: str
    ingress_url: str
    ingress_id: str
    ingress_name: str
    ingress_key: str


class StartLiveResponse(BaseModel):
    livekit_token: str
    room_name: str


room_service = RoomService(
    session=aiohttp.ClientSession(connector=aiohttp.TCPConnector(ssl=False)),
    url=settings.livekit_url,
    api_key=LIVEKIT_API_KEY,
    api_secret=LIVEKIT_API_SECRET,
)

ingress_service = IngressService(
    session=aiohttp.ClientSession(connector=aiohttp.TCPConnector(ssl=False)),
    url=settings.livekit_url,
    api_key=LIVEKIT_API_KEY,
    api_secret=LIVEKIT_API_SECRET,
)


@router.post("/webhook", operation_id="live_webhook", response_model=bool)
async def web(request: Request,  authorization: str = Header(None)):
    rawBody = await request.body()
    webhook_receiver = WebhookReceiver(
        TokenVerifier(LIVEKIT_API_KEY, LIVEKIT_API_SECRET)
    )
    print(rawBody)
    try:
        event_data_string = rawBody.decode()
        event_data_json = webhook_receiver.receive(event_data_string, authorization)
        # Stringify the event data for logging/debugging
        # Verify and process the webhook event
        event = event_data_json.event
        if event == "room_started":
            roomInfo = event_data_json.room
            roomName = roomInfo.name if roomInfo else None
            print(roomName)
            print("NAME")
           
            await mongo.verifications.update_one(
                {"livekit_room_name": roomName},
                {
                    "$set": {
                        "is_live": True,
                        "has_recording": False,
                    }
                },
            )

        if event == "egress_updated":
            egressInfo = event_data_json.egress_info
            roomName = egressInfo.room_name if egressInfo else None
         
            status = egressInfo.status
            if status == "EGRESS_ACTIVE":
                await mongo.verifications.update_one(
                    {"livekit_room_name": roomName},
                    {
                        "$set": {
                            # It means we can show this verification in the feed
                            "state": VerificationState.READY_FOR_USE,
                        }
                    },
                )

        if event == "room_finished":
            roomInfo = event_data_json.room
            roomName = roomInfo.name if roomInfo else None
            # Means creator disconnected from the livestream, We should wait for egress_ended event after that
            await mongo.verifications.update_one(
                {"livekit_room_name": roomName},
                {
                    "$set": {
                        "is_live": False,
                        "live_ended_at": datetime.now(timezone.utc),
                    }
                },
            )
        if event == "egress_ended":
            # Egress can be aborted or completed, we need to check if it's completed
            egressInfo = event_data_json.egress_info
            roomName = egressInfo.room_name if egressInfo else None
            if egressInfo.status == "EGRESS_COMPLETE":
                # Egress ended means that we have a recording and we can set has_recording to true
                await mongo.verifications.update_one(
                    {"livekit_room_name": roomName},
                    {"$set": {"has_recording": True, "is_live": False}},
                )
            else:
                await mongo.verifications.update_one(
                    {"livekit_room_name": roomName},
                    {
                        "$set": {
                            "has_recording": False,
                            "is_live": False,
                            # Set verification in progress so that this errored item will not show up in the feed
                            "state": VerificationState.VERIFICATION_IN_PROGRESS,
                        }
                    },
                )

        return True
    except Exception as e:
        print(e)
        raise HTTPException(status_code=500, detail="Failed to process webhook")


@router.get(
    "/get-live-stream-token",
    operation_id="get_live_stream_token",
    response_model=GetLiveStreamTokenResponse,
)
async def get_live_stream_token(
    request: Request,
    room_name: Annotated[str, Query(embed=True)],
):
    user_id = request.state.supabase_user_id
    token = (
        AccessToken(LIVEKIT_API_KEY, LIVEKIT_API_SECRET)
        .with_identity(str(user_id))
        .with_name(room_name)
        .with_grants(
            VideoGrants(
                room_join=True,
                room=str(room_name),
                can_publish=False,
                can_publish_data=False,
                can_subscribe=True,
            )
        )
    )
    return {
        "livekit_token": token.to_jwt(),
        "room_name": room_name,
    }


def generate_live_verification_doc(
    feed_id: CustomObjectId,
    user_id: str,
    room_name: str,
    dest_file_name: str,
    text_content: Optional[str] = None,
):
    return {
        "feed_id": feed_id,
        "assignee_user_id": user_id,
        # Set verification in progress so that this item will not show up in the feed
        "state": VerificationState.VERIFICATION_IN_PROGRESS,
        "file_name": dest_file_name,
        "file_extension": ".m3u8",
        "file_content_type": "video/m3u8",
        "last_modified_date": datetime.now(timezone.utc),
        "is_public": True,
        "is_live": False,
        "has_recording": False,
        "text_content": text_content,
        "livekit_room_name": room_name,
        "verified_media_playback": {
            "hls": "https://cdn.wal.ge/livekit-recording/"
            + str(room_name)
            + "/"
            + str(room_name)
            + ".m3u8",
            "dash": "",
            "mp4": "",
        },
    }


@router.post(
    "/request-livekit-ingress",
    operation_id="request_livekit_ingress",
    response_model=RequestLivekitIngressResponse,
)
async def request_livekit_ingress(
    request: Request,
    feed_id: Annotated[CustomObjectId, Body()],
    text_content: Annotated[Optional[str], Body()] = None,
):
    user_id = request.state.supabase_user_id
    room_name = str("ment-live-") + str(uuid.uuid4())

    dest_file_name = str(uuid.uuid4())

    insert_doc = generate_live_verification_doc(
        feed_id, user_id, room_name, dest_file_name, text_content
    )
    verification_doc = await mongo.verifications.insert_one(insert_doc)

    ingress = await ingress_service.create_ingress(
        CreateIngressRequest(
            room_name=room_name,
            input_type=IngressInput.RTMP_INPUT,
            participant_identity="identity",
            participant_name="identity",
            enable_transcoding=True,
        )
    )

    roomId = str(room_name)
    token = (
        AccessToken(LIVEKIT_API_KEY, LIVEKIT_API_SECRET)
        # Identity here means that this token is for the user who created the ingress and the room
        .with_identity(user_id)
        .with_name(roomId)
        .with_room_config(
            RoomConfiguration(
                max_participants=10,
                egress=RoomEgress(
                    participant=AutoParticipantEgress(
                        preset=EncodingOptionsPreset.PORTRAIT_H264_1080P_60,
                        segment_outputs=[
                            SegmentedFileOutput(
                                # Filename prefix is the each of the segment file prefix, that's why we make sure they are in a sub folder
                                filename_prefix=f"livekit-recording/{roomId}/{roomId}",
                                segment_duration=3,
                                gcp=GCPUpload(
                                    bucket="ment-verification",
                                ),
                            ),
                        ],
                    )
                ),
            )
        )
        .with_grants(
            VideoGrants(
                room_join=True,
                room=str(room_name),
            )
        )
    )

    livekit_token = token.to_jwt()

    insert_doc["_id"] = verification_doc.inserted_id

    return {
        "livekit_token": livekit_token,
        "room_name": room_name,
        "ingress_url": ingress.url,
        "ingress_id": ingress.ingress_id,
        "ingress_name": ingress.name,
        "ingress_key": ingress.stream_key,
    }


@router.post(
    "/request-live",
    operation_id="start_live",
    response_model=StartLiveResponse,
)
async def start_live(
    request: Request,
    feed_id: Annotated[CustomObjectId, Body()],
    text_content: Annotated[Optional[str], Body()] = None,
):
    user_id = request.state.supabase_user_id
    room_name = str("ment-live-") + str(uuid.uuid4())

    dest_file_name = str(uuid.uuid4())

    insert_doc = generate_live_verification_doc(
        feed_id, user_id, room_name, dest_file_name, text_content
    )

    verification_doc = await mongo.verifications.insert_one(insert_doc)
    roomId = str(room_name)
    room = await room_service.create_room(
            CreateRoomRequest(
            name=room_name,
            empty_timeout=30,
            max_participants=1000,
        )
    )
    token = (
        AccessToken(LIVEKIT_API_KEY, LIVEKIT_API_SECRET)
        .with_identity("identity")
        .with_name(roomId)
        .with_room_config(
            RoomConfiguration(
                max_participants=10,
                # egress=RoomEgress(
                #     participant=AutoParticipantEgress(
                #         preset=EncodingOptionsPreset.PORTRAIT_H264_720P_30,
                #         segment_outputs=[
                #             SegmentedFileOutput(
                #                 # Filename prefix is the each of the segment file prefix, that's why we make sure they are in a sub folder
                #                 filename_prefix=f"livekit-recording/{roomId}/{roomId}",
                #                 segment_duration=3,
                #                 gcp=GCPUpload(
                #                     bucket="ment-verification",
                #                 ),
                #             ),
                #         ],
                #     )
                # ),
            )
        )
        .with_grants(
            VideoGrants(
                room_join=True,
                room=str(room_name),
            )
        )
    )

    livekit_token = token.to_jwt()

    insert_doc["_id"] = verification_doc.inserted_id

    return {"livekit_token": livekit_token, "room_name": room_name}


@router.post("/stop-live", operation_id="stop_live")
async def stop_live(
    room_name: Annotated[str, Query(embed=True)],
):
    await mongo.verifications.update_one(
        {"livekit_room_name": room_name},
        {"$set": {"is_live": False}},
    )
    await room_service.delete_room(DeleteRoomRequest(room=room_name))
    return {"message": "Live stream stopped successfully"}