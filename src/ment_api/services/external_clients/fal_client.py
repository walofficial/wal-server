import base64
import os
import asyncio

import fal_client
import aiofiles
from ment_api.configurations.config import settings

os.environ["FAL_KEY"] = settings.fal_key


async def generate_image(prompt: str, image_path: str):
    result = await fal_client.run_async(
        "fal-ai/flux-pro/v1.1-ultra",
        arguments={
            "prompt": prompt,
            "aspect_ratio": "9:16",
            "raw": True,
            "sync_mode": "true",
            "enable_safety_checker": "true",
        },
    )

    img_data = result["images"][0]["url"]
    if img_data.startswith("data:image/jpeg;base64,"):
        img_data = img_data.replace("data:image/jpeg;base64,", "")

    # Ensure proper padding for base64 decoding
    padding = len(img_data) % 4
    if padding:
        img_data += "=" * (4 - padding)

    # Decode base64 data
    decoded = base64.b64decode(img_data)

    # Use async file I/O to avoid blocking the event loop
    async with aiofiles.open(image_path, "wb") as fh:
        await fh.write(decoded)

    return decoded, image_path


def generate_image_sync(prompt: str, image_path: str):
    """Synchronous version for backward compatibility if needed"""

    return asyncio.run(generate_image(prompt, image_path))


async def generate_image_raw(prompt: str):
    result = await fal_client.run_async(
        "vfal-ai/flux/de",
        arguments={
            "prompt": prompt + " " + "cinematic",
            "image_size": "portrait_4_3",
            "sync_mode": "false",
            "enable_safety_checker": "true",
        },
    )

    img_data = result["images"][0]["url"]
    # or, more concisely using with statement

    return img_data
