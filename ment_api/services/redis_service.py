from fastapi import FastAPI, Depends
from redis import Redis, ConnectionPool
from contextlib import asynccontextmanager
import logging
from functools import lru_cache
from ment_api.config import settings

logger = logging.getLogger(__name__)


class RedisService:
    def __init__(self):
        logger.info("Creating new ConnectionPool")

        self.pool = ConnectionPool(
            host=settings.redis_host,
            port=settings.redis_port,
            password=settings.redis_password,
            decode_responses=True,
            max_connections=10,
        )

    def get_client(self) -> Redis:
        logger.info("Getting new Redis client from pool")
        return Redis(connection_pool=self.pool)


@lru_cache()
def get_redis_service() -> RedisService:
    logger.info("Initializing RedisService (should happen once)")
    return RedisService()


@asynccontextmanager
async def get_redis_client():
    redis_service = get_redis_service()
    client = redis_service.get_client()
    logger.info("Created new Redis client for request")
    try:
        yield client
    finally:
        logger.info("Closing Redis client after request")
        client.close()


async def get_redis_dependency():
    async with get_redis_client() as redis:
        yield redis
