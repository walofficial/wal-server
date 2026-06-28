# /// script
# requires-python = ">=3.12"
# dependencies = ["motor", "pymongo", "python-dotenv", "pydantic-settings"]
# ///
"""
Seed Haus demo houses and events. Uses the first user in `users` as host when available.

  uv run scripts/seed_haus.py
"""
import asyncio
import logging
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from bson import ObjectId

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

SCRIPT_DIR = Path(__file__).parent.resolve()
BASE_DIR = SCRIPT_DIR.parent
sys.path.insert(0, str(BASE_DIR / "src"))

from ment_api.configurations.config import settings  # noqa: E402
from motor.motor_asyncio import AsyncIOMotorClient  # noqa: E402


async def main() -> None:
    client = AsyncIOMotorClient(settings.mongodb_uri)
    db = client[settings.mongodb_db_name]

    user = await db.users.find_one({})
    host_id = user["external_user_id"] if user else "demo-host-supabase-id"
    logger.info("Using host external_user_id=%s", host_id)

    now = datetime.now(timezone.utc)
    houses = [
        {
            "host_external_user_id": host_id,
            "title": "Vake Loft",
            "neighborhood": "Vake",
            "vibe_tag": "#LoFiLounge",
            "capacity": 24,
            "payment_instructions": "TBC Bank — IBAN GE00TB0000000000000000 — Reference: WALHAUS + your booking code",
            "image_urls": [
                "https://images.unsplash.com/photo-1566737236500-c8ac43014a67?q=80&w=800&auto=format&fit=crop",
            ],
            "bathroom_note": "Renovated guest bath",
            "created_at": now,
        },
        {
            "host_external_user_id": host_id,
            "title": "Sololaki Terrace",
            "neighborhood": "Sololaki",
            "vibe_tag": "#GrooveSuite",
            "capacity": 30,
            "payment_instructions": "BOG — IBAN GE00BG0000000000000000 — Reference: WALHAUS + your booking code",
            "image_urls": [
                "https://images.unsplash.com/photo-1516450360452-9312f5e86fc7?q=80&w=800&auto=format&fit=crop",
            ],
            "bathroom_note": "Clean + stocked",
            "created_at": now,
        },
    ]

    await db.haus_houses.delete_many({"title": {"$in": ["Vake Loft", "Sololaki Terrace"]}})
    res_h = await db.haus_houses.insert_many(houses)
    h_ids = list(res_h.inserted_ids)
    logger.info("Inserted %d houses", len(h_ids))

    events = [
        {
            "house_id": h_ids[0],
            "title": "Neon Nights",
            "starts_at": now + timedelta(days=2, hours=20),
            "ends_at": now + timedelta(days=3, hours=2),
            "price_gel": 50.0,
            "spots_total": 24,
            "spots_taken": 2,
            "midnight_drop_percent": 40,
            "qr_activate_hours_before": 1.0,
            "created_at": now,
        },
        {
            "house_id": h_ids[0],
            "title": "Sunday Social",
            "starts_at": now + timedelta(days=9, hours=18),
            "ends_at": now + timedelta(days=9, hours=23),
            "price_gel": 40.0,
            "spots_total": 24,
            "spots_taken": 0,
            "midnight_drop_percent": 40,
            "qr_activate_hours_before": 1.0,
            "created_at": now,
        },
        {
            "house_id": h_ids[1],
            "title": "Terrace Groove",
            "starts_at": now + timedelta(days=5, hours=21),
            "ends_at": now + timedelta(days=6, hours=3),
            "price_gel": 55.0,
            "spots_total": 30,
            "spots_taken": 5,
            "midnight_drop_percent": 40,
            "qr_activate_hours_before": 1.0,
            "created_at": now,
        },
    ]
    await db.haus_events.delete_many({"title": {"$in": [e["title"] for e in events]}})
    await db.haus_events.insert_many(events)
    logger.info("Inserted %d events", len(events))

    await client.close()
    logger.info("Done.")


if __name__ == "__main__":
    asyncio.run(main())
