import asyncio
import logging
from contextlib import asynccontextmanager
from typing import List, Optional

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
    RawInterPressNewsItem,
    RawInterPressNewsItemDetails,
    RawInterPressNewsResponse,
)
from ment_api.services.external_clients.scrape_news_base_client import (
    ScrapeNewsBaseClient,
)

logger = logging.getLogger(__name__)


# Single retry decorator for all HTTP requests
http_retry = retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=1, max=10),
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


class ScrapeNewsInterPressClient(ScrapeNewsBaseClient):
    def __init__(
        self,
        client: httpx.AsyncClient,
        use_scrape_do: bool = False,
        target_base_url: Optional[str] = None,
    ):
        super().__init__(client)
        self.use_scrape_do = use_scrape_do
        self.target_base_url = target_base_url

    @http_retry
    async def _http_get(self, endpoint_or_params) -> httpx.Response:
        """Make GET request - either direct or via scrape.do"""
        try:
            if self.use_scrape_do:
                response = await self.client.get("", params=endpoint_or_params)
            else:
                response = await self.client.get(
                    endpoint_or_params, follow_redirects=True
                )
            response.raise_for_status()
            return response
        except httpx.RequestError as exc:
            logger.warning(
                f"Request error occurred while requesting {exc.request.url!r}: {exc}"
            )
            raise
        except httpx.HTTPStatusError as exc:
            logger.error(
                f"HTTP error {exc.response.status_code} while requesting {exc.request.url!r} params: {endpoint_or_params}"
            )
            raise

    @http_retry
    async def _http_post(self, endpoint_or_params) -> httpx.Response:
        """Make POST request - either direct or via scrape.do"""
        try:
            if self.use_scrape_do:
                logger.info(
                    f"Using scrape.do for InterPress. Params: {endpoint_or_params}"
                )
                response = await self.client.post("", params=endpoint_or_params)
            else:
                response = await self.client.post(
                    endpoint_or_params, follow_redirects=True
                )
            if response.status_code != 200:
                logger.error(f"Response: {response.text[:100]}")
            response.raise_for_status()
            return response
        except httpx.RequestError as exc:
            logger.warning(
                f"Request error occurred while requesting {exc.request.url!r}: {exc}"
            )
            raise
        except httpx.HTTPStatusError as exc:
            logger.error(
                f"HTTP error {exc.response.status_code} while requesting {exc.request.url!r} params: {endpoint_or_params}"
            )
            raise

    async def fetch_item_details(
        self, item_id: int
    ) -> Optional[RawInterPressNewsItemDetails]:
        try:
            if self.use_scrape_do:
                if not self.target_base_url:
                    logger.error(
                        "Target base URL must be provided when using scrape.do"
                    )
                    return None

                target_url = f"{self.target_base_url}/ka/api/article/{item_id}"
                scrape_do_params = {
                    "token": settings.scrape_do_token,
                    "url": target_url,
                    "geoCode": "GE",
                    "super": True,
                }
                logger.debug(
                    f"Using scrape.do for InterPress details. Target: {target_url}"
                )
                response = await self._http_get(scrape_do_params)
            else:
                logger.debug("Using direct connection for InterPress details.")
                response = await self._http_get(f"ka/api/article/{item_id}")
            logger.info(f"Response: {response.text[:1000]} {item_id}")
            return await self._handle_response(response, RawInterPressNewsItemDetails)
        except Exception as e:
            logger.error(f"Error fetching details for item {item_id}: {str(e)}")
            return None

    async def fetch_details_concurrently(
        self, items: List[RawInterPressNewsItem], chunk_size: int = 1
    ) -> List[Optional[RawInterPressNewsItemDetails]]:
        all_results = []
        for i in range(0, len(items), chunk_size):
            chunk = items[i : i + chunk_size]
            chunk_tasks = [self.fetch_item_details(item.id) for item in chunk]
            chunk_results = await asyncio.gather(*chunk_tasks)
            all_results.extend(chunk_results)
            if i + chunk_size < len(items):
                await asyncio.sleep(1)  # Rate limiting between chunks
        return all_results

    async def scrape_news(self, news_quantity: int = 4) -> Optional[NewsResponse]:
        try:
            if self.use_scrape_do:
                if not self.target_base_url:
                    logger.error(
                        "Target base URL must be provided when using scrape.do"
                    )
                    return None

                target_url = f"{self.target_base_url}/ka/api/category/5"
                scrape_do_params = {
                    "token": settings.scrape_do_token,
                    "url": target_url,
                    "geoCode": "GE",
                    "super": True,
                }
                logger.debug(f"Using scrape.do for InterPress. Target: {target_url}")
                response = await self._http_post(scrape_do_params)
            else:
                logger.debug("Using direct connection for InterPress.")
                response = await self._http_post("ka/api/category/5")
            logger.info(f"Response: {response.text[:100]}")
            raw_response = await self._handle_response(
                response, RawInterPressNewsResponse
            )

            if not raw_response:
                logger.error("Failed to scrape news")
                return NewsResponse(news_items=[])

            # Limit the number of items we'll fetch details for
            items_to_process = raw_response.news_items[:news_quantity]
            details_results = await self.fetch_details_concurrently(items_to_process)

            news_items = []
            for item, details in zip(items_to_process, details_results):
                if details:
                    news_items.append(
                        NewsItem(
                            external_id=str(item.id),
                            title=item.title,
                            content=details.content,
                            details_url=f"{settings.scrapable_interpress_news_endpiont}/ka{details.details_url_part}",
                            small_image_url=item.small_image_url,
                            medium_image_url=item.medium_image_url,
                            big_image_url=item.big_image_url,
                            created_at=item.date,
                            category=NewsCategory.POLITICS,
                            source=NewsSource.INTERPRESS,
                        )
                    )
                else:
                    logger.warning(
                        f"Could not fetch details for InterPress item ID: {item.id}"
                    )

            return NewsResponse(news_items=news_items)
        except httpx.HTTPError as e:
            # Catch both RequestError and HTTPStatusError as per official docs
            logger.error(
                f"HTTP error scraping InterPress news: {str(e)[:100]}", exc_info=True
            )
            return NewsResponse(news_items=[])
        except Exception as e:
            logger.error(
                f"Error scraping InterPress news: {str(e)[:100]}", exc_info=True
            )
            return NewsResponse(news_items=[])


@asynccontextmanager
async def get_scrape_interpress_news_client():
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
                read=180.0,  # Read timeout
                write=30.0,  # Write timeout
                pool=300.0,  # Pool timeout
            ),
            # Explicitly disable HTTP/2 for stability (per docs recommendation)
            "http2": False,
            "follow_redirects": True,
            # Set connection limits for better resource management
            "limits": httpx.Limits(
                max_keepalive_connections=5, max_connections=10, keepalive_expiry=30.0
            ),
        }

        logger.debug(
            "Production: Configuring InterPress client via Scrape.do with HTTP/1.1"
        )
        client = httpx.AsyncClient(
            base_url=settings.scrape_do_base_url, **client_config
        )
        yield ScrapeNewsInterPressClient(
            client,
            use_scrape_do=True,
            target_base_url=settings.scrapable_interpress_news_endpiont,
        )
    finally:
        if client:
            logger.debug("Closing InterPress client transport")
            await client.aclose()
        else:
            logger.debug("No InterPress client was created.")


async def get_scrape_interpress_news_dependency():
    async with get_scrape_interpress_news_client() as client:
        yield client
