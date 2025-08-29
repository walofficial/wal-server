# Add Health Checks
from fastapi import FastAPI

from ment_api.services.health_check.async_health_check_factory import HealthCheckFactory
from ment_api.services.health_check.async_health_check_route import (
    create_health_check_route,
)
from ment_api.services.health_check.health_check_mongo_db import MongoHealthCheck
from ment_api.services.health_check.redis_health_check import RedisHealthCheck

_health_checks = HealthCheckFactory()

_health_checks.add(
    MongoHealthCheck(
        alias="mongodb",
        tags=["database", "mongodb"],
    )
)

_health_checks.add(
    RedisHealthCheck(
        alias="redis",
        tags=["cache", "redis", "storage"],
    )
)


def setup_health_checks(app: FastAPI) -> None:
    app.add_api_route(
        "/health", endpoint=create_health_check_route(factory=_health_checks)
    )
