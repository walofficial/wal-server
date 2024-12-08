import base64
import os

import fal_client
from ment_api.config import settings

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
    # or, more concisely using with statement
    # generate random image path
    decoded = base64.b64decode(img_data)
    with open(image_path, "wb") as fh:
        fh.write(decoded)

    return decoded, image_path


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
