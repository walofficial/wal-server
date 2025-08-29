"""
Redis Service with Async Support

This service provides both synchronous and asynchronous Redis clients using redis-py 4.2.0+.
The aioredis library has been merged into redis-py and is now available as `redis.asyncio`.

Migration Guide:
================

1. For new async functions, use:
   ```python
   from ment_api.services.redis_service import get_async_redis_client

   async def my_function():
       redis = get_async_redis_client()
       value = await redis.get("key")
       await redis.set("key", "value", ex=60)
   ```

2. For existing sync code (no changes needed):
   ```python
   from ment_api.services.redis_service import get_redis_client

   def my_function():
       redis = get_redis_client()
       value = redis.get("key")
       redis.set("key", "value", ex=60)
   ```

3. Common async Redis operations:
   - await redis.get(key)
   - await redis.set(key, value, ex=seconds)
   - await redis.sadd(key, *values)
   - await redis.smembers(key)
   - await redis.srem(key, *values)
   - await redis.mget(keys)
   - await redis.incr(key)
   - await redis.exists(key)
   - await redis.setex(key, time, value)

4. Benefits of async Redis:
   - Better performance in async contexts
   - Non-blocking I/O operations
   - Proper integration with FastAPI async endpoints
   - Connection pooling optimized for async workloads
"""

from redis import Redis, ConnectionPool
from redis import asyncio as aioredis
import logging
from functools import lru_cache
from ment_api.configurations.config import settings

logger = logging.getLogger(__name__)


class RedisService:
    def __init__(self):
        logger.info("Creating Redis connection pools (sync and async)")

        # Sync Redis connection pool
        self.sync_pool = ConnectionPool(
            host=settings.redis_host,
            port=settings.redis_port,
            password=settings.redis_password,
            decode_responses=True,
            max_connections=10,
        )

        # Async Redis connection pool
        self.async_pool = aioredis.ConnectionPool(
            host=settings.redis_host,
            port=settings.redis_port,
            password=settings.redis_password,
            decode_responses=True,
            max_connections=10,
        )

        # Create reusable clients
        self._sync_client = Redis(connection_pool=self.sync_pool)
        self._async_client = aioredis.Redis(connection_pool=self.async_pool)
        logger.info("Redis clients (sync and async) initialized successfully")

    @property
    def client(self) -> Redis:
        """Get the singleton sync Redis client - for backward compatibility"""
        return self._sync_client

    @property
    def async_client(self) -> aioredis.Redis:
        """Get the singleton async Redis client - for new async operations"""
        return self._async_client

    def close(self):
        """Close the Redis clients and connection pools - only called during app shutdown"""
        logger.info("Closing Redis connection pools")
        self._sync_client.close()
        # Note: async client close should be awaited, but this is for shutdown
        # In a proper shutdown sequence, you would await self._async_client.close()

    async def aclose(self):
        """Async close for proper cleanup of async client"""
        logger.info("Closing async Redis connection pool")
        await self._async_client.close()


@lru_cache()
def get_redis_service() -> RedisService:
    logger.info("Initializing RedisService (should happen once)")
    return RedisService()


def get_redis_client() -> Redis:
    """Get the singleton sync Redis client for direct usage - backward compatibility"""
    return get_redis_service().client


def get_async_redis_client() -> aioredis.Redis:
    """Get the singleton async Redis client for new async operations"""
    return get_redis_service().async_client


async def get_redis_dependency():
    """FastAPI dependency that provides the sync Redis client - backward compatibility"""
    return get_redis_service().client


async def get_async_redis_dependency():
    """FastAPI dependency that provides the async Redis client"""
    return get_redis_service().async_client
