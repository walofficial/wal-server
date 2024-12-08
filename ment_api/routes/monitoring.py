from fastapi import APIRouter
from ment_api.services.unified_tracker import tracker


router = APIRouter(
    prefix="/monitoring",
    tags=["monitoring"]
)

@router.get("get-requests")
async def generate():
    return tracker.get_request_info_list()