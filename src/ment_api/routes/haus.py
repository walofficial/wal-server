import logging
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import jwt
from bson import ObjectId
from bson.errors import InvalidId
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from ment_api.configurations.config import settings
from ment_api.models.haus import (
    HAUS_PUSH_BOOKING_APPROVED,
    HAUS_PUSH_BOOKING_REJECTED,
    HAUS_PUSH_CHECKED_IN,
    HAUS_PUSH_JOIN_REQUEST,
    HAUS_PUSH_PAYMENT_PROOF,
    HausBookingProofBody,
    HausBookingResponse,
    HausBookingStatus,
    HausCheckInBody,
    HausEventDetailResponse,
    HausEventResponse,
    HausHouseResponse,
    HausProfileResponse,
    HausProfileUpsert,
    HausTicketResponse,
)
from ment_api.models.notification import NotificationType
from ment_api.persistence import mongo
from ment_api.services.notification_service import send_notification

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/haus", tags=["haus"])


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _require_user(request: Request) -> str:
    uid = getattr(request.state, "supabase_user_id", None)
    if not uid or uid == "unknown":
        raise HTTPException(status_code=401, detail="Unauthorized")
    return str(uid)


def _house_from_doc(doc: Dict[str, Any]) -> HausHouseResponse:
    return HausHouseResponse(
        id=str(doc["_id"]),
        host_external_user_id=doc["host_external_user_id"],
        title=doc["title"],
        neighborhood=doc["neighborhood"],
        vibe_tag=doc.get("vibe_tag", ""),
        capacity=int(doc.get("capacity", 30)),
        payment_instructions=doc.get("payment_instructions", ""),
        image_urls=list(doc.get("image_urls") or []),
        bathroom_note=doc.get("bathroom_note"),
        created_at=doc["created_at"],
    )


def _event_from_doc(doc: Dict[str, Any]) -> HausEventResponse:
    return HausEventResponse(
        id=str(doc["_id"]),
        house_id=str(doc["house_id"]),
        title=doc["title"],
        starts_at=doc["starts_at"],
        ends_at=doc["ends_at"],
        price_gel=float(doc.get("price_gel", 0)),
        spots_total=int(doc.get("spots_total", 0)),
        spots_taken=int(doc.get("spots_taken", 0)),
        midnight_drop_percent=int(doc.get("midnight_drop_percent", 40)),
        qr_activate_hours_before=float(doc.get("qr_activate_hours_before", 1.0)),
        created_at=doc["created_at"],
    )


def _booking_from_doc(doc: Dict[str, Any]) -> HausBookingResponse:
    return HausBookingResponse(
        id=str(doc["_id"]),
        event_id=str(doc["event_id"]),
        guest_external_user_id=doc["guest_external_user_id"],
        status=HausBookingStatus(doc["status"]),
        booking_code=doc["booking_code"],
        payment_proof_url=doc.get("payment_proof_url"),
        created_at=doc["created_at"],
        updated_at=doc["updated_at"],
    )


async def _notify_haus(
    *,
    from_user_id: str,
    to_user_id: str,
    ntype: NotificationType,
    title: str,
    body: str,
    push_type: str,
    booking_id: str,
    event_id: str,
    house_id: str,
) -> None:
    doc = {
        "from_user_id": from_user_id,
        "to_user_id": to_user_id,
        "type": ntype,
        "created_at": _utcnow(),
        "read": False,
        "haus_booking_id": ObjectId(booking_id),
        "haus_event_id": ObjectId(event_id),
        "haus_house_id": ObjectId(house_id),
        "message": body[:500],
    }
    await mongo.notifications.insert_one(doc)
    await send_notification(
        str(to_user_id),
        title,
        body[:120],
        {
            "type": push_type,
            "bookingId": booking_id,
            "eventId": event_id,
            "houseId": house_id,
        },
    )


@router.put(
    "/profile",
    operation_id="haus_upsert_profile",
    response_model=HausProfileResponse,
)
async def haus_upsert_profile(request: Request, body: HausProfileUpsert):
    external_user_id = _require_user(request)
    now = _utcnow()
    doc = {
        "external_user_id": external_user_id,
        "invite_code": body.invite_code.strip(),
        "instagram_handle": body.instagram_handle.strip(),
        "age_confirmed": body.age_confirmed,
        "completed_at": now,
        "is_host": False,
    }
    await mongo.haus_profiles.update_one(
        {"external_user_id": external_user_id},
        {"$set": doc},
        upsert=True,
    )
    saved = await mongo.haus_profiles.find_one({"external_user_id": external_user_id})
    return HausProfileResponse(
        external_user_id=saved["external_user_id"],
        invite_code=saved["invite_code"],
        instagram_handle=saved["instagram_handle"],
        age_confirmed=bool(saved.get("age_confirmed")),
        is_host=bool(saved.get("is_host")),
        completed_at=saved["completed_at"],
    )


@router.get("/profile", operation_id="haus_get_profile")
async def haus_get_profile(request: Request):
    external_user_id = _require_user(request)
    saved = await mongo.haus_profiles.find_one({"external_user_id": external_user_id})
    if not saved:
        return {"completed": False}
    return {
        "completed": True,
        "profile": HausProfileResponse(
            external_user_id=saved["external_user_id"],
            invite_code=saved["invite_code"],
            instagram_handle=saved["instagram_handle"],
            age_confirmed=bool(saved.get("age_confirmed")),
            is_host=bool(saved.get("is_host")),
            completed_at=saved["completed_at"],
        ),
    }


@router.get("/houses", operation_id="haus_list_houses", response_model=List[HausHouseResponse])
async def haus_list_houses(request: Request):
    _require_user(request)
    docs = await mongo.haus_houses.find_all({}, sort=[("created_at", -1)])
    return [_house_from_doc(d) for d in docs]


@router.get(
    "/houses/{house_id}",
    operation_id="haus_get_house",
    response_model=HausHouseResponse,
)
async def haus_get_house(request: Request, house_id: str):
    _require_user(request)
    try:
        oid = ObjectId(house_id)
    except InvalidId:
        raise HTTPException(status_code=400, detail="Invalid house id")
    doc = await mongo.haus_houses.find_one_by_id(oid)
    if not doc:
        raise HTTPException(status_code=404, detail="House not found")
    return _house_from_doc(doc)


@router.get("/events", operation_id="haus_list_events", response_model=List[HausEventResponse])
async def haus_list_events(request: Request, house_id: Optional[str] = None):
    _require_user(request)
    q: Dict[str, Any] = {}
    if house_id:
        try:
            q["house_id"] = ObjectId(house_id)
        except InvalidId:
            raise HTTPException(status_code=400, detail="Invalid house id")
    docs = await mongo.haus_events.find_all(q, sort=[("starts_at", 1)])
    return [_event_from_doc(d) for d in docs]


@router.get(
    "/events/{event_id}",
    operation_id="haus_get_event_detail",
    response_model=HausEventDetailResponse,
)
async def haus_get_event_detail(request: Request, event_id: str):
    _require_user(request)
    try:
        eid = ObjectId(event_id)
    except InvalidId:
        raise HTTPException(status_code=400, detail="Invalid event id")
    ev = await mongo.haus_events.find_one_by_id(eid)
    if not ev:
        raise HTTPException(status_code=404, detail="Event not found")
    house = await mongo.haus_houses.find_one_by_id(ev["house_id"])
    if not house:
        raise HTTPException(status_code=404, detail="House not found")
    return HausEventDetailResponse(
        event=_event_from_doc(ev),
        house=_house_from_doc(house),
    )


@router.post(
    "/events/{event_id}/request",
    operation_id="haus_request_join",
    response_model=HausBookingResponse,
)
async def haus_request_join(request: Request, event_id: str):
    guest_id = _require_user(request)
    profile = await mongo.haus_profiles.find_one({"external_user_id": guest_id})
    if not profile:
        raise HTTPException(status_code=400, detail="Complete Haus profile first")

    try:
        eid = ObjectId(event_id)
    except InvalidId:
        raise HTTPException(status_code=400, detail="Invalid event id")

    ev = await mongo.haus_events.find_one_by_id(eid)
    if not ev:
        raise HTTPException(status_code=404, detail="Event not found")

    existing = await mongo.haus_bookings.find_one(
        {"event_id": eid, "guest_external_user_id": guest_id}
    )
    if existing:
        return _booking_from_doc(existing)

    spots_total = int(ev.get("spots_total", 0))
    spots_taken = int(ev.get("spots_taken", 0))
    waitlisted = spots_taken >= spots_total
    status = (
        HausBookingStatus.WAITLISTED.value
        if waitlisted
        else HausBookingStatus.PAYMENT_PENDING.value
    )
    booking_code = secrets.token_hex(3).upper()
    now = _utcnow()
    house = await mongo.haus_houses.find_one_by_id(ev["house_id"])
    if not house:
        raise HTTPException(status_code=404, detail="House not found")

    ins = await mongo.haus_bookings.insert_one(
        {
            "event_id": eid,
            "guest_external_user_id": guest_id,
            "status": status,
            "booking_code": booking_code,
            "payment_proof_url": None,
            "created_at": now,
            "updated_at": now,
        }
    )
    bid = ins.inserted_id
    doc = await mongo.haus_bookings.find_one_by_id(bid)

    await _notify_haus(
        from_user_id=guest_id,
        to_user_id=house["host_external_user_id"],
        ntype=NotificationType.HAUS_JOIN_REQUEST,
        title="Haus: New join request",
        body=(
            f"Waitlist: {ev.get('title', 'your event')}"
            if waitlisted
            else f"Someone requested to join {ev.get('title', 'your event')}."
        ),
        push_type=HAUS_PUSH_JOIN_REQUEST,
        booking_id=str(bid),
        event_id=str(eid),
        house_id=str(ev["house_id"]),
    )

    return _booking_from_doc(doc)


@router.post(
    "/bookings/{booking_id}/proof",
    operation_id="haus_upload_booking_proof",
    response_model=HausBookingResponse,
)
async def haus_upload_booking_proof(
    request: Request, booking_id: str, body: HausBookingProofBody
):
    guest_id = _require_user(request)
    try:
        bid = ObjectId(booking_id)
    except InvalidId:
        raise HTTPException(status_code=400, detail="Invalid booking id")

    b = await mongo.haus_bookings.find_one_by_id(bid)
    if not b or b["guest_external_user_id"] != guest_id:
        raise HTTPException(status_code=404, detail="Booking not found")

    if b["status"] not in (
        HausBookingStatus.PAYMENT_PENDING.value,
        HausBookingStatus.PROOF_UPLOADED.value,
        HausBookingStatus.REQUESTED.value,
    ):
        raise HTTPException(status_code=400, detail="Cannot upload proof for this status")

    now = _utcnow()
    await mongo.haus_bookings.update_one(
        {"_id": bid},
        {
            "$set": {
                "payment_proof_url": body.proof_image_url,
                "status": HausBookingStatus.PROOF_UPLOADED.value,
                "updated_at": now,
            }
        },
    )
    b = await mongo.haus_bookings.find_one_by_id(bid)
    ev = await mongo.haus_events.find_one_by_id(b["event_id"])
    house = await mongo.haus_houses.find_one_by_id(ev["house_id"])

    await _notify_haus(
        from_user_id=guest_id,
        to_user_id=house["host_external_user_id"],
        ntype=NotificationType.HAUS_PAYMENT_PROOF,
        title="Haus: Payment proof uploaded",
        body="A guest uploaded transfer proof. Review in the app.",
        push_type=HAUS_PUSH_PAYMENT_PROOF,
        booking_id=str(bid),
        event_id=str(b["event_id"]),
        house_id=str(ev["house_id"]),
    )

    return _booking_from_doc(b)


@router.get(
    "/bookings/me",
    operation_id="haus_my_bookings",
    response_model=List[HausBookingResponse],
)
async def haus_my_bookings(request: Request):
    guest_id = _require_user(request)
    docs = await mongo.haus_bookings.find_all(
        {"guest_external_user_id": guest_id},
        sort=[("created_at", -1)],
    )
    return [_booking_from_doc(d) for d in docs]


@router.get(
    "/host/incoming",
    operation_id="haus_host_incoming",
    response_model=List[Dict[str, Any]],
)
async def haus_host_incoming(request: Request):
    host_id = _require_user(request)
    houses = await mongo.haus_houses.find_all({"host_external_user_id": host_id})
    if not houses:
        return []
    house_ids = [h["_id"] for h in houses]
    events = await mongo.haus_events.find_all({"house_id": {"$in": house_ids}})
    eid_map = {e["_id"]: e for e in events}
    event_ids = list(eid_map.keys())
    if not event_ids:
        return []

    bookings = await mongo.haus_bookings.find_all(
        {
            "event_id": {"$in": event_ids},
            "status": {
                "$in": [
                    HausBookingStatus.PAYMENT_PENDING.value,
                    HausBookingStatus.PROOF_UPLOADED.value,
                    HausBookingStatus.WAITLISTED.value,
                ]
            },
        },
        sort=[("updated_at", -1)],
    )
    out = []
    for b in bookings:
        ev = eid_map.get(b["event_id"])
        if not ev:
            continue
        house = next((h for h in houses if h["_id"] == ev["house_id"]), None)
        guest = await mongo.users.find_one({"external_user_id": b["guest_external_user_id"]})
        out.append(
            {
                "booking": _booking_from_doc(b).model_dump(mode="json"),
                "event": _event_from_doc(ev).model_dump(mode="json"),
                "house": _house_from_doc(house).model_dump(mode="json") if house else None,
                "guest_username": (guest or {}).get("username"),
            }
        )
    return out


@router.post(
    "/bookings/{booking_id}/approve",
    operation_id="haus_approve_booking",
    response_model=HausBookingResponse,
)
async def haus_approve_booking(request: Request, booking_id: str):
    host_id = _require_user(request)
    try:
        bid = ObjectId(booking_id)
    except InvalidId:
        raise HTTPException(status_code=400, detail="Invalid booking id")

    b = await mongo.haus_bookings.find_one_by_id(bid)
    if not b:
        raise HTTPException(status_code=404, detail="Booking not found")
    ev = await mongo.haus_events.find_one_by_id(b["event_id"])
    house = await mongo.haus_houses.find_one_by_id(ev["house_id"])
    if house["host_external_user_id"] != host_id:
        raise HTTPException(status_code=403, detail="Not the host")

    spots_total = int(ev.get("spots_total", 0))
    spots_taken = int(ev.get("spots_taken", 0))
    if spots_taken >= spots_total:
        raise HTTPException(status_code=400, detail="Event is full")

    now = _utcnow()
    await mongo.haus_bookings.update_one(
        {"_id": bid},
        {"$set": {"status": HausBookingStatus.APPROVED.value, "updated_at": now}},
    )
    await mongo.haus_events.update_one(
        {"_id": ev["_id"]},
        {"$inc": {"spots_taken": 1}},
    )

    b = await mongo.haus_bookings.find_one_by_id(bid)
    await _notify_haus(
        from_user_id=host_id,
        to_user_id=b["guest_external_user_id"],
        ntype=NotificationType.HAUS_BOOKING_APPROVED,
        title="Haus: You're in",
        body=f"Approved for {ev.get('title', 'the party')}. Your QR will activate before doors open.",
        push_type=HAUS_PUSH_BOOKING_APPROVED,
        booking_id=str(bid),
        event_id=str(ev["_id"]),
        house_id=str(ev["house_id"]),
    )
    return _booking_from_doc(b)


@router.post(
    "/bookings/{booking_id}/reject",
    operation_id="haus_reject_booking",
    response_model=HausBookingResponse,
)
async def haus_reject_booking(request: Request, booking_id: str):
    host_id = _require_user(request)
    try:
        bid = ObjectId(booking_id)
    except InvalidId:
        raise HTTPException(status_code=400, detail="Invalid booking id")

    b = await mongo.haus_bookings.find_one_by_id(bid)
    if not b:
        raise HTTPException(status_code=404, detail="Booking not found")
    ev = await mongo.haus_events.find_one_by_id(b["event_id"])
    house = await mongo.haus_houses.find_one_by_id(ev["house_id"])
    if house["host_external_user_id"] != host_id:
        raise HTTPException(status_code=403, detail="Not the host")

    now = _utcnow()
    await mongo.haus_bookings.update_one(
        {"_id": bid},
        {"$set": {"status": HausBookingStatus.REJECTED.value, "updated_at": now}},
    )
    b = await mongo.haus_bookings.find_one_by_id(bid)
    await _notify_haus(
        from_user_id=host_id,
        to_user_id=b["guest_external_user_id"],
        ntype=NotificationType.HAUS_BOOKING_REJECTED,
        title="Haus: Request update",
        body="Your request was not approved for this event.",
        push_type=HAUS_PUSH_BOOKING_REJECTED,
        booking_id=str(bid),
        event_id=str(ev["_id"]),
        house_id=str(ev["house_id"]),
    )
    return _booking_from_doc(b)


def _encode_ticket_token(booking_id: str, guest_id: str) -> str:
    exp_dt = _utcnow() + timedelta(days=7)
    return jwt.encode(
        {
            "typ": "haus_ticket",
            "bid": booking_id,
            "gid": guest_id,
            "exp": int(exp_dt.timestamp()),
        },
        settings.supabase_jwt_secret,
        algorithm="HS256",
    )


@router.get(
    "/bookings/{booking_id}/ticket",
    operation_id="haus_get_booking_ticket",
    response_model=HausTicketResponse,
)
async def haus_get_booking_ticket(request: Request, booking_id: str):
    guest_id = _require_user(request)
    try:
        bid = ObjectId(booking_id)
    except InvalidId:
        raise HTTPException(status_code=400, detail="Invalid booking id")

    b = await mongo.haus_bookings.find_one_by_id(bid)
    if not b or b["guest_external_user_id"] != guest_id:
        raise HTTPException(status_code=404, detail="Booking not found")

    if b["status"] != HausBookingStatus.APPROVED.value:
        return HausTicketResponse(active=False, message="Booking not approved")

    ev = await mongo.haus_events.find_one_by_id(b["event_id"])
    starts = ev["starts_at"]
    if starts.tzinfo is None:
        starts = starts.replace(tzinfo=timezone.utc)
    ends = ev["ends_at"]
    if ends.tzinfo is None:
        ends = ends.replace(tzinfo=timezone.utc)

    hours_before = float(ev.get("qr_activate_hours_before", 1.0))
    valid_from = starts - timedelta(hours=hours_before)
    now = _utcnow()

    if now < valid_from:
        return HausTicketResponse(
            active=False,
            valid_from=valid_from,
            valid_until=ends,
            message="QR activates closer to the event start time.",
        )
    if now > ends:
        return HausTicketResponse(
            active=False,
            valid_from=valid_from,
            valid_until=ends,
            message="This event has ended.",
        )

    token = _encode_ticket_token(str(bid), guest_id)
    return HausTicketResponse(
        token=token,
        active=True,
        valid_from=valid_from,
        valid_until=ends,
    )


@router.post(
    "/bookings/check-in",
    operation_id="haus_check_in",
)
async def haus_check_in(request: Request, body: HausCheckInBody):
    host_id = _require_user(request)
    try:
        payload = jwt.decode(
            body.token,
            settings.supabase_jwt_secret,
            algorithms=["HS256"],
            options={"require": ["exp"]},
        )
    except jwt.PyJWTError:
        raise HTTPException(status_code=400, detail="Invalid ticket token")

    if payload.get("typ") != "haus_ticket":
        raise HTTPException(status_code=400, detail="Invalid ticket type")

    bid_s = payload.get("bid")
    gid = payload.get("gid")
    if not bid_s or not gid:
        raise HTTPException(status_code=400, detail="Invalid ticket payload")

    try:
        bid = ObjectId(bid_s)
    except InvalidId:
        raise HTTPException(status_code=400, detail="Invalid booking id in token")

    b = await mongo.haus_bookings.find_one_by_id(bid)
    if not b or b["guest_external_user_id"] != gid:
        raise HTTPException(status_code=400, detail="Ticket does not match booking")

    if b["status"] != HausBookingStatus.APPROVED.value:
        raise HTTPException(status_code=400, detail="Booking not approved")

    ev = await mongo.haus_events.find_one_by_id(b["event_id"])
    house = await mongo.haus_houses.find_one_by_id(ev["house_id"])
    if house["host_external_user_id"] != host_id:
        raise HTTPException(status_code=403, detail="Only the host can check in guests")

    now = _utcnow()
    await mongo.haus_bookings.update_one(
        {"_id": bid},
        {"$set": {"status": HausBookingStatus.CHECKED_IN.value, "updated_at": now}},
    )

    await _notify_haus(
        from_user_id=host_id,
        to_user_id=gid,
        ntype=NotificationType.HAUS_CHECKED_IN,
        title="Haus: Checked in",
        body="You're checked in. Enjoy the party.",
        push_type=HAUS_PUSH_CHECKED_IN,
        booking_id=str(bid),
        event_id=str(ev["_id"]),
        house_id=str(ev["house_id"]),
    )

    return {"success": True, "booking_id": str(bid)}


class HausMarkHostBody(BaseModel):
    """Dev convenience: mark current user as host (for testing)."""

    is_host: bool = True


@router.post("/profile/host", operation_id="haus_mark_host")
async def haus_mark_host(request: Request, body: HausMarkHostBody):
    """Allow user to flag as host for MVP testing (listing creation is seed/script)."""
    external_user_id = _require_user(request)
    await mongo.haus_profiles.update_one(
        {"external_user_id": external_user_id},
        {
            "$set": {"is_host": body.is_host},
            "$setOnInsert": {
                "external_user_id": external_user_id,
                "invite_code": "HOST",
                "instagram_handle": "@host",
                "age_confirmed": True,
                "completed_at": _utcnow(),
            },
        },
        upsert=True,
    )
    return {"success": True}
