from typing import List, Optional
import logging

from fastapi_healthcheck.enum import HealthCheckStatusEnum

from ment_api.services.health_check.async_health_check_interface import (
    HealthCheckProtocol,
)
from ment_api.services.redis_service import get_redis_service


class RedisHealthCheck(HealthCheckProtocol):
    """Health check for Redis service connectivity."""

    def __init__(
        self,
        alias: str,
        tags: Optional[List[str]] = None,
    ) -> None:
        self.alias = alias
        self.tags = tags or []
        self._logger = logging.getLogger(__name__)

    async def check_health(self) -> HealthCheckStatusEnum:
        """
        Check Redis health by performing a ping operation.

        Returns:
            HealthCheckStatusEnum.HEALTHY if Redis is accessible
            HealthCheckStatusEnum.UNHEALTHY if Redis connection fails
        """
        try:
            redis_service = get_redis_service()

            # Use async client for health check
            result = await redis_service.async_client.ping()

            if result:
                return HealthCheckStatusEnum.HEALTHY
            else:
                return HealthCheckStatusEnum.UNHEALTHY

        except Exception as e:
            self._logger.error(f"Redis health check failed: {e}")
            return HealthCheckStatusEnum.UNHEALTHY
