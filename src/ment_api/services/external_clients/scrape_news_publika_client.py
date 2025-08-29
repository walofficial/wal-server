import logging
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Optional

import httpx
from tenacity import (
    before_sleep_log,
    retry,
    retry_if_exception,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from ment_api.configurations.config import settings
from ment_api.services.external_clients.models.scrape_news_models import (
    NewsCategory,
    NewsItem,
    NewsResponse,
    NewsSource,
    RawPublikaNewsResponse,
)
from ment_api.services.external_clients.scrape_news_base_client import (
    ScrapeNewsBaseClient,
)

logger = logging.getLogger(__name__)


class ScrapeNewsPublikaClient(ScrapeNewsBaseClient):
    def __init__(self, client: httpx.AsyncClient):
        super().__init__(client)

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=3),
        retry=(
            # Use httpx.RequestError as superclass per official docs
            retry_if_exception_type((httpx.RequestError,))
            | retry_if_exception(
                lambda e: isinstance(e, httpx.HTTPStatusError)
                and e.response.status_code in [429, 502, 503, 504]
            )
        ),
        before_sleep=before_sleep_log(logger, logging.WARNING),
        reraise=True,
    )
    async def _make_http_request(self, params: dict) -> httpx.Response:
        try:
            response = await self.client.get("", params=params)
            response.raise_for_status()
            return response
        except httpx.RequestError as exc:
            logger.warning(
                f"Request error occurred while requesting {exc.request.url!r}: {exc}"
            )
            raise
        except httpx.HTTPStatusError as exc:
            logger.error(
                f"HTTP error {exc.response.status_code} while requesting {exc.request.url!r}"
            )
            raise

    async def scrape_news(self, news_quantity: int = 13) -> Optional[NewsResponse]:
        try:
            params = {
                "tab": "recent",
                "offset": 0,
                "posts_per_page": news_quantity,
            }

            raw_response = await self._make_http_request(params)
            raw_response = await self._handle_response(
                raw_response, RawPublikaNewsResponse
            )

            if not raw_response:
                logger.error("Failed to scrape news")
                return NewsResponse(news_items=[])

            response = NewsResponse(
                news_items=[
                    NewsItem(
                        external_id=str(item.id),
                        title=item.title,
                        content=item.content,
                        details_url=item.details_url,
                        small_image_url=item.small_image_url,
                        medium_image_url=item.medium_image_url,
                        big_image_url=item.big_image_url,
                        created_at=datetime.fromtimestamp(float(item.date)),
                        category=NewsCategory.POLITICS,
                        source=NewsSource.PUBLIKA,
                    )
                    for item in raw_response.news_items
                ]
            )

            return response
        except httpx.HTTPError as e:
            # Catch both RequestError and HTTPStatusError as per official docs
            logger.error(f"HTTP error scraping Publika news: {str(e)}", exc_info=True)
            return NewsResponse(news_items=[])
        except Exception as e:
            logger.error(f"Error scraping Publika news: {str(e)}", exc_info=True)
            return NewsResponse(news_items=[])


@asynccontextmanager
async def get_scrape_publika_news_client():
    logger.debug("Creating new Publika client for request")
    client = None
    try:
        # Follow official HTTPX docs recommendations
        client_config = {
            "headers": {
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            # Granular timeout configuration as per docs
            "timeout": httpx.Timeout(
                connect=30.0,  # Connection timeout
                read=120.0,  # Read timeout
                write=30.0,  # Write timeout
                pool=180.0,  # Pool timeout
            ),
            # Explicitly disable HTTP/2 for stability (per docs recommendation)
            "http2": False,
            "follow_redirects": True,
            # Set connection limits for better resource management
            "limits": httpx.Limits(
                max_keepalive_connections=5, max_connections=10, keepalive_expiry=30.0
            ),
        }

        client = httpx.AsyncClient(
            base_url=settings.scrapable_publika_news_endpiont, **client_config
        )
        yield ScrapeNewsPublikaClient(client)
    finally:
        if client:
            logger.debug("Closing Publika client after request")
            await client.aclose()


async def get_scrape_publika_news_dependency():
    async with get_scrape_publika_news_client() as client:
        yield client
