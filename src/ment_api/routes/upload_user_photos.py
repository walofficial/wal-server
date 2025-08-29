from fastapi import APIRouter, HTTPException
from fastapi import UploadFile, File
from typing import List
import blurhash
import io
from PIL import Image
import asyncio
import uuid
from concurrent.futures import ThreadPoolExecutor

from pydantic import BaseModel
from ment_api.services.external_clients.cloud_flare_client import upload_image

router = APIRouter(
    prefix="/upload-photos",
    tags=["user"],
    responses={404: {"description": "Not found"}},
)


class UploadPhotosResponse(BaseModel):
    blur_hash: str
    image_id: str
    image_url: List[str]


@router.post(
    "",
    response_model=List[UploadPhotosResponse],
    operation_id="upload_user_photos",
    responses={500: {"description": "Generation error"}},
)
async def upload_photos(files: List[UploadFile] = File(...)):
    try:
        uploaded = await asyncio.gather(*[process_file(file) for file in files])

    except Exception:
        raise HTTPException(status_code=500, detail="Failed to upload images")

    return uploaded


async def process_file(file: UploadFile):
    contents = await file.read()
    loop = asyncio.get_running_loop()

    # Run image processing in thread pool
    with ThreadPoolExecutor() as executor:
        # Process image and generate blur hash
        def process_image():
            image = Image.open(io.BytesIO(contents))
            return blurhash.encode(image, x_components=4, y_components=3)

        blur_hash = await loop.run_in_executor(executor, process_image)

    dest_file_extension = ".jpeg"
    generate_random_file_name = str(uuid.uuid4())
    dest_file_full_name = f"{generate_random_file_name}{dest_file_extension}"

    uploaded_url = await upload_image(
        file=contents,
        destination_file_name=dest_file_full_name,
        content_type=file.content_type,
    )

    if uploaded_url:
        return {
            "blur_hash": blur_hash,
            "image_id": generate_random_file_name,
            "image_url": [uploaded_url.url],
        }
    else:
        raise Exception("Failed to upload image")
