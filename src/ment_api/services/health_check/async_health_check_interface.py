from abc import ABC, abstractmethod
from typing import List, Optional

from fastapi_healthcheck.enum import HealthCheckStatusEnum


class HealthCheckProtocol(ABC):
    """Protocol for health check implementations."""

    alias: str
    tags: Optional[List[str]]

    @abstractmethod
    async def check_health(self) -> HealthCheckStatusEnum:
        """Check the health of the service asynchronously."""
        pass
