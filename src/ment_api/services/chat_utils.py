"""
Chat utilities module.

Contains shared utilities for chat functionality to avoid circular imports.
"""

from datetime import datetime, timezone

# Presence TTL configuration
PRESENCE_SID_TTL_SECONDS = 90
PRESENCE_ONLINE_WINDOW_SECONDS = 75


# Redis key helpers for scalable connection tracking
def user_sids_key(user_id: str) -> str:
    """ZSET: member=sid, score=last_seen_epoch_seconds."""
    return f"user_presence_sids:{user_id}"


def sid_user_key(sid: str) -> str:
    """Redis key for mapping sid to user_id."""
    return f"sid_user:{sid}"


def sid_device_key(sid: str) -> str:
    """Redis key for mapping sid to device_id."""
    return f"sid_device:{sid}"


def user_info_key(user_id: str) -> str:
    """Redis key for cached user info."""
    return f"user_info:{user_id}"


def _now_epoch_seconds() -> int:
    """Get current time as epoch seconds."""
    return int(datetime.now(tz=timezone.utc).timestamp())


async def _prune_and_count_online(redis, user_id: str) -> int:
    """Remove stale sids and return number of active sids within online window."""
    key = user_sids_key(user_id)
    now = _now_epoch_seconds()
    min_score = now - PRESENCE_ONLINE_WINDOW_SECONDS
    pipe = redis.pipeline()
    pipe.zremrangebyscore(key, 0, min_score - 1)
    pipe.zcount(key, min_score, "+inf")
    results = await pipe.execute()
    # results[0] is removed count, results[1] is active count
    return int(results[1] or 0)


async def _touch_presence(redis, user_id: str, sid: str) -> None:
    """Mark a sid as recently seen and keep the per-user ZSET bounded."""
    key = user_sids_key(user_id)
    now = _now_epoch_seconds()
    min_score = now - PRESENCE_ONLINE_WINDOW_SECONDS
    pipe = redis.pipeline()
    pipe.zadd(key, {sid: now})
    pipe.expire(key, PRESENCE_SID_TTL_SECONDS)
    pipe.zremrangebyscore(key, 0, min_score - 1)
    await pipe.execute()

