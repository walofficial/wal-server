from datetime import datetime
from typing import List

from fastapi_healthcheck.enum import HealthCheckStatusEnum
from fastapi_healthcheck.model import HealthCheckEntityModel, HealthCheckModel

from ment_api.services.health_check.async_health_check_interface import (
    HealthCheckProtocol,
)


class HealthCheckFactory:
    _health_checks: List[HealthCheckProtocol]
    _health: HealthCheckModel

    def __init__(self) -> None:
        self._health_checks = list()

    def add(self, item: HealthCheckProtocol) -> None:
        self._health_checks.append(item)

    def __startTimer__(self, entityTimer: bool) -> None:
        if entityTimer:
            self._entityStartTime = datetime.now()
        else:
            self._totalStartTime = datetime.now()

    def __stopTimer__(self, entityTimer: bool) -> None:
        if entityTimer:
            self._entityStopTime = datetime.now()
        else:
            self._totalStopTime = datetime.now()

    def __getTimeTaken__(self, entityTimer: bool) -> datetime:
        if entityTimer:
            return self._entityStopTime - self._entityStartTime
        return self._totalStopTime - self._totalStartTime

    def __dumpModel__(self, model: HealthCheckModel) -> dict:
        """Convert python objects to a json-serializable dict."""
        entities = []
        for entity in model.entities:
            entity_dict = {
                "alias": entity.alias,
                "status": entity.status.value
                if isinstance(entity.status, HealthCheckStatusEnum)
                else entity.status,
                "timeTaken": str(entity.timeTaken),
                "tags": entity.tags,
            }
            entities.append(entity_dict)

        return {
            "status": model.status.value
            if isinstance(model.status, HealthCheckStatusEnum)
            else model.status,
            "totalTimeTaken": str(model.totalTimeTaken),
            "entities": entities,
        }

    async def check(self) -> dict:
        self._health = HealthCheckModel()
        self.__startTimer__(False)

        for item in self._health_checks:
            # Generate the model
            if not hasattr(item, "tags"):
                item.tags = list()

            entity = HealthCheckEntityModel(
                alias=item.alias, tags=item.tags if item.tags else []
            )

            # Track how long the entity took to respond
            self.__startTimer__(True)
            entity.status = await item.check_health()
            self.__stopTimer__(True)
            entity.timeTaken = self.__getTimeTaken__(True)

            # If we have one dependency unhealthy, the service is unhealthy
            if entity.status == HealthCheckStatusEnum.UNHEALTHY:
                self._health.status = HealthCheckStatusEnum.UNHEALTHY

            self._health.entities.append(entity)

        self.__stopTimer__(False)
        self._health.totalTimeTaken = self.__getTimeTaken__(False)

        return self.__dumpModel__(self._health)
