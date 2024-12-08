from typing import Optional, List

from fastapi_healthcheck.domain import HealthCheckInterface
from fastapi_healthcheck.enum import HealthCheckStatusEnum
from fastapi_healthcheck.service import HealthCheckBase

from ment_api.persistence.mongo_client import check_mongo_connection_sync


class HealthCheckMongoDB(HealthCheckBase, HealthCheckInterface):
    _connectionUri: str
    _database: str
    _message: str

    def __init__(
        self,
        connectionUri: str,
        database: str,
        alias: str,
        tags: Optional[List[str]] = None,
    ) -> None:
        self._connectionUri = connectionUri
        self._alias = alias
        self._database = database
        self._tags = tags

    def __checkHealth__(self) -> HealthCheckStatusEnum:
        res: HealthCheckStatusEnum = HealthCheckStatusEnum.UNHEALTHY
        try:
            check_mongo_connection_sync()
            res = HealthCheckStatusEnum.HEALTHY
        except Exception:
            pass
        return res
