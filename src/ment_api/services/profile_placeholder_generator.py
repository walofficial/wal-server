import random
import io
import uuid
import asyncio
from concurrent.futures import ThreadPoolExecutor
from PIL import Image
import cairosvg
import blurhash
from ment_api.services.external_clients.cloud_flare_client import upload_image
from ment_api.persistence import mongo


def _generate_avatar_sync(user_id, username, size=256):
    """Synchronous avatar generation function to run in thread pool"""
    # Seed random with username for consistent generation per user
    random.seed(username)

    # List of diverse background colors suitable for messenger/signal style and iOS notifications
    dark_colors = [
        (18, 89, 181),  # Deep blue
        (147, 37, 166),  # Purple
        (179, 27, 27),  # Deep red
        (13, 105, 94),  # Forest green
        (166, 91, 10),  # Burnt orange
        (82, 45, 128),  # Royal purple
        (153, 61, 0),  # Rust
        (0, 105, 148),  # Ocean blue
        (135, 53, 84),  # Burgundy
        (48, 100, 48),  # Hunter green
        (107, 36, 43),  # Wine red
        (77, 58, 25),  # Dark brown
        (36, 37, 46),  # Dark slate
        (89, 49, 95),  # Plum
        (0, 71, 64),  # Deep teal
    ]

    # Pick a random dark color for background
    bg_color = random.choice(dark_colors)

    # Generate contrasting lighter version for the icon
    # Increase each channel by a random amount between 90-130 for more variation
    light_color = tuple(min(255, c + random.randint(90, 130)) for c in bg_color)
    light_color_hex = f"#{light_color[0]:02x}{light_color[1]:02x}{light_color[2]:02x}"

    # Create image with square background
    img = Image.new("RGBA", (size, size), bg_color)

    # Calculate icon size and position (make icon take up about 40% of the image)
    icon_size = int(size * 0.4)
    icon_x = (size - icon_size) // 2
    icon_y = (size - icon_size) // 2

    # Create SVG string with proper scaling and positioning
    svg_content = f"""
        <svg xmlns="http://www.w3.org/2000/svg" width="{icon_size}" height="{icon_size}" viewBox="0 0 24 24" fill="none" stroke="{light_color_hex}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
            <circle cx="12" cy="8" r="5" fill="{light_color_hex}"/>
            <path d="M20 21a8 8 0 0 0-16 0h16z" fill="{light_color_hex}"/>
        </svg>  
    """

    # Convert SVG to PNG bytes using cairosvg
    icon_png_bytes = cairosvg.svg2png(bytestring=svg_content.encode("utf-8"))

    # Load the SVG-rendered icon as PIL Image
    icon_img = Image.open(io.BytesIO(icon_png_bytes)).convert("RGBA")

    # Paste the icon onto the background image
    img.paste(icon_img, (icon_x, icon_y), icon_img)

    # Generate blurhash for the image
    blur_hash = blurhash.encode(img, x_components=4, y_components=3)

    # Convert PIL Image to bytes
    img_buffer = io.BytesIO()
    img.save(img_buffer, format="PNG")
    img_bytes = img_buffer.getvalue()

    return img_bytes, blur_hash


async def _upload_avatar_async(img_bytes, blur_hash):
    """Async upload function"""
    # Upload to Cloudflare storage
    random_id = str(uuid.uuid4())
    destination_filename = f"avatar_{random_id}.png"
    uploaded_image = await upload_image(img_bytes, destination_filename, "image/png")

    return {
        "message": "Avatar generated and uploaded successfully",
        "image_url": uploaded_image.url,
        "width": uploaded_image.width,
        "height": uploaded_image.height,
        "blur_hash": blur_hash,
    }


async def generate_avatar(user_id, username, size=256):
    """
    Async function to generate and upload avatar image.
    Runs blocking operations in thread pool to maintain asyncio compatibility.
    """
    loop = asyncio.get_running_loop()

    # Run image generation in thread pool
    with ThreadPoolExecutor() as executor:
        # Generate avatar image
        img_bytes, blur_hash = await loop.run_in_executor(
            executor, _generate_avatar_sync, user_id, username, size
        )

        # Upload to storage
        result = await _upload_avatar_async(img_bytes, blur_hash)

    return result


async def set_placeholder_avatar(user_id, username, size=256):
    """
    Async function to set placeholder avatar.
    """
    result = await generate_avatar(user_id, username, size)
    await mongo.users.update_one(
        {"external_user_id": user_id},
        {
            "$set": {
                "photos": [
                    {
                        "image_url": [result["image_url"]],
                        "image_id": str(uuid.uuid4()),
                        "blur_hash": result["blur_hash"],
                    }
                ]
            }
        },
    )
    return result
