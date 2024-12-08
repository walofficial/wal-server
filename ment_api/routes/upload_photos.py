from fastapi import APIRouter
from fastapi import Request, UploadFile
from typing import List
from fastapi import APIRouter
import blurhash
import io
from PIL import Image
import asyncio
import uuid
from ment_api.services.external_clients.cloud_flare_client import upload_image

router = APIRouter(
    prefix="/upload-photos",
    tags=["generate-interests"],
    responses={404: {"description": "Not found"}},
)


@router.post(
    "",
    responses={500: {"description": "Generation error"}},
)
async def upload_photos(files: List[UploadFile]):
    try:
        uploaded = await asyncio.gather(*[process_file(file) for file in files])

    except Exception as e:
        return {"ok": True}

    return {"message": "ok", "uploaded": uploaded}


async def process_file(file):
    contents = await file.read()
    image = Image.open(io.BytesIO(contents))
    blur_hash = blurhash.encode(image, x_components=4, y_components=3)
    dest_file_extension = ".jpeg"

    generate_random_file_name = str(uuid.uuid4())
    dest_file_full_name = f"{generate_random_file_name}{dest_file_extension}"

    uploaded_url = upload_image(
        file=contents,
        destination_file_name=dest_file_full_name,
        content_type=file.content_type,
    )
    if uploaded_url:
        return {
            "blur_hash": blur_hash,
            "image_id": generate_random_file_name,
            "image_url": [uploaded_url],
        }
    else:
        raise Exception("Failed to upload image")
