import asyncio
import os
import hashlib

import blurhash

from ment_api.services.external_clients.cloud_flare_client import upload_image
from ment_api.services.external_clients.fal_client import generate_image
from uuid import uuid4


async def generate_and_upload(prompt: str):
    # Hash the prompt to create a safe filename
    filename = hashlib.md5(prompt.encode()).hexdigest()
    image_path = f"{filename}.jpeg"

    (image_blob, _) = await generate_image(prompt, image_path)
    # blur_hash = None
    # with open(image_path, "rb") as image_file:
    #     blur_hash = blurhash.encode(image_file, x_components=4, y_components=3)

    random_file_name = str(uuid4())
    response = upload_image(image_blob, random_file_name, "image/jpeg")

    # remove image file
    # os.remove(image_path)

    return {
        "image_url": [response],
        "blur_hash": None,
    }


async def generates_images_and_upload_from_list(prompts: list):
    tasks = [generate_and_upload(prompt) for prompt in prompts]
    response = await asyncio.gather(*tasks)
    return response
