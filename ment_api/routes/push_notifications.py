from fastapi import APIRouter, Request, HTTPException

from bson.objectid import ObjectId
import pydantic

from ment_api.persistence import mongo

pydantic.json.ENCODERS_BY_TYPE[ObjectId] = str
router = APIRouter(
    prefix="/push-notifications",
    tags=["push-notification"],
    responses={404: {"description": "Not found"}},
)


@router.put("/save", responses={500: {"description": "Generation error"}})
async def update_user(request: Request):
    try:
        body = await request.json()
        user_id = request.headers.get("x-user-id")
        user_data = body.get("data", {})

        if not user_id:
            raise HTTPException(status_code=400, detail="userId is required")

        existing_doc = await mongo.firebase_fcm.find_one({"ownerId": user_id})
        if existing_doc:
            await mongo.firebase_fcm.update_one(
                {"ownerId": user_id}, {"$set": user_data}
            )
        else:
            await mongo.firebase_fcm.insert_one({"ownerId": user_id, **user_data})
        return {"ok": True}
    except Exception as e:
        raise HTTPException(status_code=400, detail="update error")
