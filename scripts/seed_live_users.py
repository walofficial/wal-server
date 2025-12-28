# /// script
# requires-python = ">=3.12"
# dependencies = ["motor", "pymongo", "python-dotenv", "pydantic-settings"]
# ///
"""
Seed script to populate live_users collection with random users from the database.

=== USAGE ===

1. Run directly with uv (from wal-server root):
   cd /path/to/wal-server
   uv run scripts/seed_live_users.py

2. Run in Docker container:
   docker compose exec mnt-api python scripts/seed_live_users.py

3. Run with custom options:
   uv run scripts/seed_live_users.py --count 20 --feed-ids 6759a55b2b00e2d26d2a6279
   uv run scripts/seed_live_users.py --no-wait  # Seed and exit immediately without cleanup

4. Using the FastAPI endpoint (if server is running):
   # Seed users
   curl -X POST http://localhost:5500/feeds/seed-live-users \
     -H "Content-Type: application/json" \
     -d '{"count": 10}'

   # Cleanup by seed_id
   curl -X DELETE http://localhost:5500/feeds/seed-live-users/{seed_id}

   # Cleanup all seeds
   curl -X DELETE http://localhost:5500/feeds/seed-live-users

=== BEHAVIOR ===

The script will:
1. Fetch feeds with hidden: true from the feeds collection
2. Fetch N random users from the users collection (default: 10)
3. Insert them as live_users for the hidden feed(s)
4. Wait for Ctrl+C or termination signal
5. Clean up (remove) the inserted live_users on shutdown

Uses the same configuration as the main app (including Google Secret Manager).
"""
import asyncio
import logging
import os
import signal
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from bson import ObjectId
from motor.motor_asyncio import AsyncIOMotorClient

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

# Add the src directory to the path so we can import the app's config
SCRIPT_DIR = Path(__file__).parent.resolve()
BASE_DIR = SCRIPT_DIR.parent
sys.path.insert(0, str(BASE_DIR / "src"))

# Import the app's settings (handles GCP Secret Manager, .env files, etc.)
try:
    from ment_api.configurations.config import settings
    MONGODB_URI = settings.mongodb_uri
    MONGODB_DB_NAME = settings.mongodb_db_name
    logger.info(f"Loaded settings from app config (db: {MONGODB_DB_NAME})")
except ImportError as e:
    logger.warning(f"Could not import app settings: {e}")
    logger.warning("Falling back to environment variables")
    from dotenv import load_dotenv
    load_dotenv("config/.env", override=True)
    load_dotenv(f"config/.env.{os.getenv('ENV', 'dev')}", override=True)
    MONGODB_URI = os.getenv("MONGODB_URI", "")
    MONGODB_DB_NAME = os.getenv("MONGODB_DB_NAME", "ment")

# Track inserted users for cleanup
inserted_user_ids: list[str] = []
feed_ids_used: list[ObjectId] = []
db_client: Optional[AsyncIOMotorClient] = None


async def get_hidden_feeds(db) -> list[ObjectId]:
    """Fetch all feeds with hidden: true from the feeds collection."""
    cursor = db["feeds"].find({}, {"_id": 1})
    feeds = await cursor.to_list(length=100)
    feed_ids = [feed["_id"] for feed in feeds]
    return feed_ids


async def get_random_users(db, count: int = 10) -> list[dict]:
    """Fetch random users from the users collection using $sample aggregation."""
    pipeline = [
        {
            "$match": {
                "external_user_id": {"$exists": True},
                "photos": {"$exists": True, "$ne": []},
            }
        },
        {"$sample": {"size": count}},
    ]
    cursor = db["users"].aggregate(pipeline)
    users = await cursor.to_list(length=count)
    return users


async def insert_live_users(
    db, users: list[dict], feed_ids: list[ObjectId]
) -> list[str]:
    """Insert users as live_users for the specified feeds."""
    inserted_ids = []
    expiration_date = datetime.now(timezone.utc) + timedelta(hours=12)

    for user in users:
        external_user_id = user.get("external_user_id")
        if not external_user_id:
            continue

        username = user.get("username", "unknown")

        for feed_id in feed_ids:
            try:
                await db["live_users"].update_one(
                    {"author_id": external_user_id, "feed_id": feed_id},
                    {
                        "$set": {"expiration_date": expiration_date},
                        "$setOnInsert": {"created_at": datetime.now(timezone.utc)},
                    },
                    upsert=True,
                )
                logger.info(
                    f"✅ Inserted live user: {username} ({external_user_id}) -> feed: {feed_id}"
                )
                if external_user_id not in inserted_ids:
                    inserted_ids.append(external_user_id)
            except Exception as e:
                logger.error(f"❌ Failed to insert live user {external_user_id}: {e}")

    return inserted_ids


async def cleanup_live_users(db, user_ids: list[str], feed_ids: list[ObjectId]) -> int:
    """Remove inserted live_users on shutdown."""
    if not user_ids:
        logger.info("No live users to clean up")
        return 0

    deleted_count = 0
    for user_id in user_ids:
        for feed_id in feed_ids:
            try:
                result = await db["live_users"].delete_one(
                    {"author_id": user_id, "feed_id": feed_id}
                )
                deleted_count += result.deleted_count
            except Exception as e:
                logger.error(
                    f"Failed to delete live user {user_id} for feed {feed_id}: {e}"
                )

    logger.info(f"🧹 Cleaned up {deleted_count} live user entries")
    return deleted_count


async def seed_and_wait(
    count: int = 10,
    feed_ids: Optional[list[str]] = None,
    auto_cleanup: bool = True,
) -> dict:
    """
    Main seed function that inserts live users and optionally waits for shutdown signal.

    Args:
        count: Number of random users to seed
        feed_ids: List of feed IDs (as strings) to seed users for (if None, uses hidden feeds)
        auto_cleanup: If True, registers cleanup on shutdown; if False, returns immediately

    Returns:
        dict with seeding results
    """
    global inserted_user_ids, feed_ids_used, db_client

    if not MONGODB_URI:
        logger.error("❌ MONGODB_URI environment variable is not set!")
        logger.error("Make sure config/.env and config/.env.dev files exist")
        raise ValueError("MONGODB_URI environment variable is not set")

    # Connect to MongoDB
    logger.info(f"🔌 Connecting to MongoDB database: {MONGODB_DB_NAME}")
    db_client = AsyncIOMotorClient(MONGODB_URI)
    db = db_client[MONGODB_DB_NAME]

    try:
        # Test connection
        await db.command("ping")
        logger.info("✅ MongoDB connection successful")

        # Get feed IDs - either from args or from hidden feeds
        if feed_ids:
            feed_ids_used = [ObjectId(fid) for fid in feed_ids]
            logger.info(f"📋 Using {len(feed_ids_used)} provided feed IDs")
        else:
            # Get feeds with hidden: true
            logger.info("🔍 Fetching feeds with hidden: true...")
            feed_ids_used = await get_hidden_feeds(db)
            if not feed_ids_used:
                logger.warning("⚠️ No hidden feeds found in database!")
                return {"message": "No hidden feeds found", "users_seeded": 0}
            logger.info(f"📋 Found {len(feed_ids_used)} hidden feeds: {[str(f) for f in feed_ids_used]}")

        # Get random users
        logger.info(f"🔍 Fetching {count} random users...")
        users = await get_random_users(db, count)

        if not users:
            logger.warning("⚠️ No users found in database")
            return {"message": "No users found", "users_seeded": 0}

        logger.info(f"📦 Found {len(users)} users to seed")

        # Insert as live users
        inserted_user_ids = await insert_live_users(db, users, feed_ids_used)

        result = {
            "message": "Live users seeded successfully",
            "users_seeded": len(inserted_user_ids),
            "user_ids": inserted_user_ids,
            "feed_ids": [str(fid) for fid in feed_ids_used],
        }

        logger.info(
            f"🎉 Seeded {len(inserted_user_ids)} live users for {len(feed_ids_used)} feeds"
        )

        if auto_cleanup:
            logger.info("")
            logger.info("=" * 50)
            logger.info("🚀 Live users are now seeded and active!")
            logger.info("   Press Ctrl+C to stop and cleanup")
            logger.info("=" * 50)
            logger.info("")

            # Setup signal handlers for cleanup
            shutdown_event = asyncio.Event()

            def handle_shutdown(signum, frame):
                logger.info(f"\n📥 Received shutdown signal, initiating cleanup...")
                shutdown_event.set()

            signal.signal(signal.SIGINT, handle_shutdown)
            signal.signal(signal.SIGTERM, handle_shutdown)

            # Wait for shutdown signal
            await shutdown_event.wait()

            # Cleanup
            logger.info("🧹 Cleaning up live users...")
            await cleanup_live_users(db, inserted_user_ids, feed_ids_used)
            logger.info("✅ Cleanup complete!")

        return result

    except Exception as e:
        logger.error(f"❌ Error during seeding: {e}")
        raise
    finally:
        if db_client:
            db_client.close()
            logger.info("🔌 MongoDB connection closed")


def run_seed():
    """Entry point for running the seed script."""
    import argparse

    parser = argparse.ArgumentParser(
        description="Seed live_users collection with random users",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s                          # Seed 10 users, wait for Ctrl+C
  %(prog)s --count 20               # Seed 20 users
  %(prog)s --no-wait                # Seed and exit immediately
  %(prog)s --feed-ids abc123 def456 # Use specific feed IDs
        """,
    )
    parser.add_argument(
        "--count",
        type=int,
        default=10,
        help="Number of random users to seed (default: 10)",
    )
    parser.add_argument(
        "--feed-ids",
        nargs="+",
        help="Feed IDs to seed users for (default: predefined IDs)",
    )
    parser.add_argument(
        "--no-wait",
        action="store_true",
        help="Don't wait for shutdown signal (seed and exit immediately without cleanup)",
    )
    args = parser.parse_args()

    try:
        asyncio.run(
            seed_and_wait(
                count=args.count,
                feed_ids=["6759a55b2b00e2d26d2a6279"],
                auto_cleanup=not args.no_wait,
            )
        )
    except KeyboardInterrupt:
        logger.info("\n👋 Goodbye!")
        sys.exit(0)


if __name__ == "__main__":
    run_seed()
