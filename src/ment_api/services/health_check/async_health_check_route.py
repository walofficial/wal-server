from collections.abc import Awaitable, Callable

from fastapi.responses import JSONResponse
from fastapi_healthcheck.enum import HealthCheckStatusEnum

from ment_api.services.health_check.async_health_check_factory import (
    HealthCheckFactory,
)


def create_health_check_route(
    factory: HealthCheckFactory,
) -> Callable[[], Awaitable[JSONResponse]]:
    """
    Creates an async endpoint function for health checks that can be used with FastAPI's add_api_route.

    Args:
        factory: The HealthCheckFactory instance containing health check items.

    Returns:
        An async endpoint function that returns JSONResponse with health check results.
        Returns 503 (Service Unavailable) if any health check is unhealthy, 200 (OK) otherwise.
    """

    async def endpoint() -> JSONResponse:
        result = await factory.check()

        status_code_mapping = {
            HealthCheckStatusEnum.HEALTHY.value: 200,
            HealthCheckStatusEnum.UNHEALTHY.value: 503,
        }

        status_code = status_code_mapping.get(result["status"], 503)

        return JSONResponse(content=result, status_code=status_code)

    return endpoint
