# Add Health Checks
from fastapi import FastAPI
from fastapi_healthcheck import HealthCheckFactory, healthCheckRoute

from ment_api.config import settings
from ment_api.services.health_check.health_check_mongo_db import HealthCheckMongoDB

_healthChecks = HealthCheckFactory()

_healthChecks.add(HealthCheckMongoDB(alias='mongo db',
                                     connectionUri=settings.mongodb_uri,
                                     database=settings.mongodb_db_name,
                                     tags=['mongodb', settings.mongodb_db_name]))


def setup_health_checks(app: FastAPI) -> None:
    app.add_api_route('/health', endpoint=healthCheckRoute(factory=_healthChecks))
