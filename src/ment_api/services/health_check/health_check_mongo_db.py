from typing import List, Optional

from fastapi_healthcheck.enum import HealthCheckStatusEnum

from ment_api.persistence.mongo_client import (
    check_mongo_connection,
)
from ment_api.services.health_check.async_health_check_interface import (
    HealthCheckProtocol,
)


class MongoHealthCheck(HealthCheckProtocol):
    def __init__(
        self,
        alias: str,
        tags: Optional[List[str]] = None,
    ) -> None:
        self.alias = alias
        self.tags = tags

    async def check_health(self) -> HealthCheckStatusEnum:
        try:
            # Ensure client is initialized even if app lifespan didn't run (e.g., due to ASGI wrapper)
            await check_mongo_connection()
            return HealthCheckStatusEnum.HEALTHY
        except Exception:
            return HealthCheckStatusEnum.UNHEALTHY
