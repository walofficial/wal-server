import asyncio
import logging
from contextlib import asynccontextmanager
from datetime import datetime
from typing import List, Optional
from urllib.parse import urlencode

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
    RawTV1NewsItemDetails,
    RawTV1NewsResponse,
)
from ment_api.services.external_clients.scrape_news_base_client import (
    ScrapeNewsBaseClient,
)

logger = logging.getLogger(__name__)


class ScrapeNews1tvClient(ScrapeNewsBaseClient):
    def __init__(
        self,
        client: httpx.AsyncClient,
        use_scrape_do: bool = False,
        target_base_url: Optional[str] = None,
    ):
        super().__init__(client)
        self.use_scrape_do = use_scrape_do
        self.target_base_url = target_base_url

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=3),
        retry=(
            retry_if_exception_type((httpx.RequestError,))
            | retry_if_exception(
                lambda e: isinstance(e, httpx.HTTPStatusError)
                and e.response.status_code in [429, 502, 503, 504]
            )
        ),
        before_sleep=before_sleep_log(logger, logging.WARNING),
        reraise=True,
    )
    async def _make_http_request(
        self, endpoint: str = "", params: dict = None
    ) -> httpx.Response:
        try:
            if self.use_scrape_do:
                response = await self.client.get("", params=params)
                if response.status_code != 200:
                    logger.error(f"Response: {response.text[:100]}, Params: {params}")
            else:
                response = await self.client.get(endpoint, params=params)
                if response.status_code != 200:
                    logger.error(f"Response: {response.text[:100]}, Params: {params}")
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

    async def fetch_item_details(self, item_id: int) -> Optional[RawTV1NewsItemDetails]:
        endpoint = "mobile/posts/detail"
        params = {"id": item_id}

        if self.use_scrape_do:
            if not self.target_base_url:
                logger.error("Target base URL must be provided when using scrape.do")
                return None

            target_url = f"{self.target_base_url}/{endpoint}?{urlencode(params)}"
            scrape_do_params = {
                "token": settings.scrape_do_token,
                "url": target_url,
                "geoCode": "GE",
                "super": True,
            }
            logger.debug(f"Using scrape.do for 1TV details. Target: {target_url}")
            response = await self._make_http_request(params=scrape_do_params)
            return await self._handle_response(response, RawTV1NewsItemDetails)
        else:
            logger.debug(
                f"Using direct connection for 1TV details. Endpoint: {endpoint}"
            )
            response = await self._make_http_request(endpoint, params)
            return await self._handle_response(response, RawTV1NewsItemDetails)

    async def fetch_items_in_batches(
        self, item_ids: List[int], batch_size: int = 2, batch_delay: float = 0.3
    ) -> List[Optional[RawTV1NewsItemDetails]]:
        """Fetch multiple items in controlled batches to respect rate limits."""
        all_results = []

        for i in range(0, len(item_ids), batch_size):
            batch = item_ids[i : i + batch_size]
            logger.debug(
                f"Processing batch {i // batch_size + 1}/{(len(item_ids) - 1) // batch_size + 1} with {len(batch)} items"
            )

            batch_tasks = [self.fetch_item_details(item_id) for item_id in batch]
            batch_results = await asyncio.gather(*batch_tasks)
            all_results.extend(batch_results)

            if i + batch_size < len(item_ids):
                await asyncio.sleep(batch_delay)

        return all_results

    async def scrape_news(self, news_quantity: int = 13) -> Optional[NewsResponse]:
        try:
            endpoint = "witv/posts"
            params = {
                "page_id": 1131,
                "offset": 0,
                "lang": "ge",
                "per_page": news_quantity,
            }

            raw_tv1_response: Optional[RawTV1NewsResponse] = None

            if self.use_scrape_do:
                if not self.target_base_url:
                    logger.error(
                        "Target base URL must be provided when using scrape.do"
                    )
                    return NewsResponse(news_items=[])

                target_url = f"{self.target_base_url}/{endpoint}?{urlencode(params)}"
                scrape_do_params = {
                    "token": settings.scrape_do_token,
                    "url": target_url,
                    "geoCode": "GE",
                    "super": True,
                }
                logger.debug(f"Using scrape.do for 1TV news list. Target: {target_url}")
                response = await self._make_http_request(params=scrape_do_params)
                raw_tv1_response = await self._handle_response(
                    response, RawTV1NewsResponse
                )
            else:
                logger.debug(
                    f"Using direct connection for 1TV news list. Endpoint: {endpoint}"
                )
                response = await self._make_http_request(endpoint, params)
                raw_tv1_response = await self._handle_response(
                    response, RawTV1NewsResponse
                )

            if not raw_tv1_response:
                logger.error("Failed to scrape news or parse response")
                return NewsResponse(news_items=[])

            item_ids = [item.id for item in raw_tv1_response.news_items]

            details_results = await self.fetch_items_in_batches(item_ids)

            news_items = []
            for item, details in zip(raw_tv1_response.news_items, details_results):
                if details:
                    news_items.append(
                        NewsItem(
                            external_id=str(item.id),
                            title=item.title,
                            content=details.content,
                            details_url=item.details_url,
                            small_image_url=item.small_image_url,
                            medium_image_url=item.medium_image_url,
                            big_image_url=item.big_image_url,
                            created_at=datetime.strptime(item.date, "%H:%M, %d.%m.%Y"),
                            category=NewsCategory.POLITICS,
                            source=NewsSource.TV1,
                        )
                    )
                else:
                    logger.warning(
                        f"Could not fetch details for 1TV item ID: {item.id}"
                    )

            return NewsResponse(news_items=news_items)
        except httpx.HTTPError as e:
            logger.error(f"HTTP error scraping 1TV news: {str(e)}", exc_info=True)
            return NewsResponse(news_items=[])
        except Exception as e:
            logger.error(f"Error scraping 1TV news: {str(e)}", exc_info=True)
            return NewsResponse(news_items=[])


@asynccontextmanager
async def get_scrape_1tv_news_client():
    client = None
    try:
        client_config = {
            "headers": {
                "Accept": "application/json",
            },
            "timeout": httpx.Timeout(connect=30.0, read=180.0, write=30.0, pool=300.0),
            "http2": False,
            "follow_redirects": True,
            "limits": httpx.Limits(
                max_keepalive_connections=5, max_connections=10, keepalive_expiry=30.0
            ),
        }

        if settings.env == "prod":
            logger.debug(
                "Production: Configuring 1TV client via Scrape.do with HTTP/1.1"
            )
            client = httpx.AsyncClient(
                base_url=settings.scrape_do_base_url, **client_config
            )
            yield ScrapeNews1tvClient(
                client,
                use_scrape_do=True,
                target_base_url=settings.scrapable_1tv_news_endpiont,
            )
        else:
            logger.debug("Development: Configuring direct 1TV client with HTTP/1.1")
            client = httpx.AsyncClient(
                base_url=settings.scrapable_1tv_news_endpiont, **client_config
            )
            yield ScrapeNews1tvClient(client, use_scrape_do=False)
    finally:
        if client:
            logger.debug("Closing 1TV client transport")
            await client.aclose()
        else:
            logger.debug("No 1TV client was created.")


async def get_scrape_1tv_news_dependency():
    async with get_scrape_1tv_news_client() as client:
        yield client
