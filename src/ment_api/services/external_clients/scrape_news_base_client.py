import json
import logging
from abc import ABC, abstractmethod
from typing import Optional, Type, TypeVar

import httpx
from json_repair import repair_json

from ment_api.services.external_clients.models.scrape_news_models import (
    NewsResponse,
)

logger = logging.getLogger(__name__)

T = TypeVar("T")


class ScrapeNewsBaseClient(ABC):
    def __init__(self, client: httpx.AsyncClient):
        self.client = client

    async def _handle_response(
        self, response: httpx.Response, model_class: Type[T]
    ) -> Optional[T]:
        try:
            response.raise_for_status()
            try:
                data = response.json()
            except ValueError:
                data = json.loads(repair_json(response.text))
            result = model_class(**data)
            return result
        except httpx.HTTPStatusError as e:
            error_detail = {}
            try:
                error_detail = response.json()
            except ValueError:
                error_detail = {"detail": response.text}

            logger.error(
                f"HTTP status error {e.response.status_code} from {e.request.url.host}: {error_detail}"
            )
        except httpx.RequestError as e:
            logger.error(
                f"Request error while processing response from {e.request.url.host}: {e}"
            )
        except ValueError as e:
            logger.error(
                f"Failed to parse JSON response: {response.text[:100]} with exception {e}"
            )
        return None

    @abstractmethod
    async def scrape_news(self, news_quantity: int = 20) -> Optional[NewsResponse]:
        pass
