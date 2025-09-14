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
    RawMtavariNewsItemDetails,
    RawMtavariNewsResponse,
)
from ment_api.services.external_clients.scrape_news_base_client import (
    ScrapeNewsBaseClient,
)

logger = logging.getLogger(__name__)


class ScrapeNewsMtavariClient(ScrapeNewsBaseClient):
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
                    logger.error(
                        "Scrape.do request failed",
                        extra={
                            "json_fields": {
                                "status_code": response.status_code,
                                "response_preview": response.text[:100],
                                "params": params,
                                "operation": "mtavari_scrape_do_request",
                            },
                            "labels": {
                                "component": "mtavari_client",
                                "service": "scrape_do",
                            },
                        },
                    )
            else:
                response = await self.client.get(endpoint, params=params)
                if response.status_code != 200:
                    logger.error(
                        "Direct request failed",
                        extra={
                            "json_fields": {
                                "status_code": response.status_code,
                                "response_preview": response.text[:100],
                                "params": params,
                                "endpoint": endpoint,
                                "operation": "mtavari_direct_request",
                            },
                            "labels": {
                                "component": "mtavari_client",
                                "service": "direct",
                            },
                        },
                    )
            response.raise_for_status()
            return response
        except httpx.RequestError as exc:
            logger.warning(
                "Request error occurred",
                extra={
                    "json_fields": {
                        "url": str(exc.request.url),
                        "error": str(exc),
                        "operation": "mtavari_request_error",
                    },
                    "labels": {"component": "mtavari_client", "error_type": "request"},
                },
            )
            raise
        except httpx.HTTPStatusError as exc:
            logger.error(
                "HTTP status error occurred",
                extra={
                    "json_fields": {
                        "status_code": exc.response.status_code,
                        "url": str(exc.request.url),
                        "operation": "mtavari_http_error",
                    },
                    "labels": {
                        "component": "mtavari_client",
                        "error_type": "http_status",
                    },
                },
            )
            raise

    async def fetch_item_details(
        self, item_url: str
    ) -> Optional[RawMtavariNewsItemDetails]:
        """Fetch detailed content for a single news item using its self link URL."""
        try:
            if self.use_scrape_do:
                if not self.target_base_url:
                    logger.error(
                        "Target base URL required for scrape.do",
                        extra={
                            "json_fields": {"operation": "mtavari_fetch_details"},
                            "labels": {
                                "component": "mtavari_client",
                                "error_type": "config",
                            },
                        },
                    )
                    return None

                scrape_do_params = {
                    "token": settings.scrape_do_token,
                    "url": item_url,
                    "geoCode": "GE",
                    "super": True,
                }
                logger.debug(
                    "Fetching Mtavari details via scrape.do",
                    extra={
                        "json_fields": {
                            "target_url": item_url,
                            "operation": "mtavari_fetch_details_scrape_do",
                        },
                        "labels": {
                            "component": "mtavari_client",
                            "service": "scrape_do",
                        },
                    },
                )
                response = await self._make_http_request(params=scrape_do_params)
            else:
                logger.debug(
                    "Fetching Mtavari details directly",
                    extra={
                        "json_fields": {
                            "url": item_url,
                            "operation": "mtavari_fetch_details_direct",
                        },
                        "labels": {"component": "mtavari_client", "service": "direct"},
                    },
                )
                response = await self.client.get(item_url)
                response.raise_for_status()

            return await self._handle_response(response, RawMtavariNewsItemDetails)
        except Exception as exc:
            logger.warning(
                "Failed to fetch item details",
                extra={
                    "json_fields": {
                        "url": item_url,
                        "error": str(exc),
                        "operation": "mtavari_fetch_details_error",
                    },
                    "labels": {
                        "component": "mtavari_client",
                        "error_type": "fetch_details",
                    },
                },
            )
            return None

    async def fetch_items_in_batches(
        self, item_urls: List[str], batch_size: int = 2, batch_delay: float = 0.3
    ) -> List[Optional[RawMtavariNewsItemDetails]]:
        """Fetch multiple items in controlled batches to respect rate limits."""
        all_results = []

        for i in range(0, len(item_urls), batch_size):
            batch = item_urls[i : i + batch_size]
            logger.debug(
                "Processing batch of Mtavari items",
                extra={
                    "json_fields": {
                        "batch_number": i // batch_size + 1,
                        "total_batches": (len(item_urls) - 1) // batch_size + 1,
                        "batch_size": len(batch),
                        "operation": "mtavari_batch_processing",
                    },
                    "labels": {"component": "mtavari_client"},
                },
            )

            batch_tasks = [self.fetch_item_details(item_url) for item_url in batch]
            batch_results = await asyncio.gather(*batch_tasks)
            all_results.extend(batch_results)

            if i + batch_size < len(item_urls):
                await asyncio.sleep(batch_delay)

        return all_results

    def _extract_image_urls(
        self, included_data: List[dict], thumbnail_proxy_id: str
    ) -> tuple[str, str, str]:
        """Extract image URLs from included data based on thumbnail proxy ID."""
        big_image_url = medium_image_url = small_image_url = ""

        for item in included_data:
            if (
                item.get("type") == "file--file"
                and item.get("id") == thumbnail_proxy_id
            ):
                meta = item.get("meta", {})
                image_derivatives = meta.get("imageDerivatives", {})
                links = image_derivatives.get("links", {})

                big_image_url = links.get("news_thumb_lg", {}).get("href", "")
                medium_image_url = links.get("news_thumb_md", {}).get("href", "")
                small_image_url = links.get("news_thumb_sm", {}).get("href", "")
                break

        return big_image_url, medium_image_url, small_image_url

    async def scrape_news(self, news_quantity: int = 15) -> Optional[NewsResponse]:
        try:
            endpoint = "jsonapi/node/news"
            params = {
                "filter[status][value]": 1,
                "filter[langcode]": "ka",
                "filter[categories.slug][value]": "politics",
                "include": "thumbnail,categories",
                "sort": "-created",
                "page[limit]": news_quantity,
            }

            raw_mtavari_response: Optional[RawMtavariNewsResponse] = None

            if self.use_scrape_do:
                if not self.target_base_url:
                    logger.error(
                        "Target base URL required for scrape.do",
                        extra={
                            "json_fields": {"operation": "mtavari_scrape_news"},
                            "labels": {
                                "component": "mtavari_client",
                                "error_type": "config",
                            },
                        },
                    )
                    return NewsResponse(news_items=[])

                target_url = f"{self.target_base_url}/{endpoint}?{urlencode(params)}"
                scrape_do_params = {
                    "token": settings.scrape_do_token,
                    "url": target_url,
                    "geoCode": "GE",
                    "super": True,
                }
                logger.debug(
                    "Fetching Mtavari news list via scrape.do",
                    extra={
                        "json_fields": {
                            "target_url": target_url,
                            "news_quantity": news_quantity,
                            "operation": "mtavari_scrape_news_scrape_do",
                        },
                        "labels": {
                            "component": "mtavari_client",
                            "service": "scrape_do",
                        },
                    },
                )
                response = await self._make_http_request(params=scrape_do_params)
                raw_mtavari_response = await self._handle_response(
                    response, RawMtavariNewsResponse
                )
            else:
                logger.debug(
                    "Fetching Mtavari news list directly",
                    extra={
                        "json_fields": {
                            "endpoint": endpoint,
                            "news_quantity": news_quantity,
                            "operation": "mtavari_scrape_news_direct",
                        },
                        "labels": {"component": "mtavari_client", "service": "direct"},
                    },
                )
                response = await self._make_http_request(endpoint, params)
                raw_mtavari_response = await self._handle_response(
                    response, RawMtavariNewsResponse
                )

            if not raw_mtavari_response:
                logger.error(
                    "Failed to scrape Mtavari news or parse response",
                    extra={
                        "json_fields": {"operation": "mtavari_scrape_news_failed"},
                        "labels": {
                            "component": "mtavari_client",
                            "error_type": "response_parsing",
                        },
                    },
                )
                return NewsResponse(news_items=[])

            item_urls = [item.self_link for item in raw_mtavari_response.news_items]

            details_results = await self.fetch_items_in_batches(item_urls)

            news_items = []
            for item, details in zip(raw_mtavari_response.news_items, details_results):
                if details:
                    # Extract image URLs from included data
                    big_image_url = medium_image_url = small_image_url = ""
                    if (
                        raw_mtavari_response.included
                        and item.relationships
                        and item.relationships.thumbnail_proxy_id
                    ):
                        # Get thumbnail proxy ID from relationships and find corresponding image data
                        proxy_id = item.relationships.thumbnail_proxy_id
                        big_image_url, medium_image_url, small_image_url = (
                            self._extract_image_urls(
                                raw_mtavari_response.included, proxy_id
                            )
                        )

                    # Parse the created date
                    try:
                        created_at = datetime.fromisoformat(
                            item.created.replace("Z", "+00:00")
                        )
                    except (ValueError, AttributeError):
                        created_at = datetime.now()

                    news_items.append(
                        NewsItem(
                            external_id=item.id,
                            title=item.title,
                            content=self._clean_html_content(details.body),
                            details_url=f"https://mtavari.tv/news/{item.drupal_internal_nid}-{item.slug}",
                            small_image_url=small_image_url,
                            medium_image_url=medium_image_url,
                            big_image_url=big_image_url,
                            created_at=created_at,
                            category=NewsCategory.POLITICS,
                            source=NewsSource.MTAVARI,
                        )
                    )
                else:
                    logger.warning(
                        "Could not fetch details for Mtavari item",
                        extra={
                            "json_fields": {
                                "item_id": item.id,
                                "item_title": item.title,
                                "operation": "mtavari_missing_details",
                            },
                            "labels": {
                                "component": "mtavari_client",
                                "warning_type": "missing_details",
                            },
                        },
                    )

            logger.info(
                "Successfully scraped Mtavari news",
                extra={
                    "json_fields": {
                        "items_found": len(news_items),
                        "requested_quantity": news_quantity,
                        "operation": "mtavari_scrape_success",
                    },
                    "labels": {"component": "mtavari_client"},
                },
            )

            return NewsResponse(news_items=news_items)
        except httpx.HTTPError as e:
            logger.error(
                "HTTP error scraping Mtavari news",
                extra={
                    "json_fields": {
                        "error": str(e),
                        "operation": "mtavari_scrape_http_error",
                    },
                    "labels": {"component": "mtavari_client", "error_type": "http"},
                },
                exc_info=True,
            )
            return NewsResponse(news_items=[])
        except Exception as e:
            logger.error(
                "Error scraping Mtavari news",
                extra={
                    "json_fields": {
                        "error": str(e),
                        "operation": "mtavari_scrape_general_error",
                    },
                    "labels": {"component": "mtavari_client", "error_type": "general"},
                },
                exc_info=True,
            )
            return NewsResponse(news_items=[])


@asynccontextmanager
async def get_scrape_mtavari_news_client():
    client = None
    try:
        client_config = {
            "headers": {
                "Accept": "application/json",
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
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
                "Production: Configuring Mtavari client via Scrape.do",
                extra={
                    "json_fields": {"operation": "mtavari_client_init_prod"},
                    "labels": {
                        "component": "mtavari_client",
                        "environment": "production",
                    },
                },
            )
            client = httpx.AsyncClient(
                base_url=settings.scrape_do_base_url, **client_config
            )
            yield ScrapeNewsMtavariClient(
                client,
                use_scrape_do=True,
                target_base_url=settings.scrapable_mtavari_news_endpoint,
            )
        else:
            logger.debug(
                "Development: Configuring direct Mtavari client",
                extra={
                    "json_fields": {"operation": "mtavari_client_init_dev"},
                    "labels": {
                        "component": "mtavari_client",
                        "environment": "development",
                    },
                },
            )
            client = httpx.AsyncClient(
                base_url=settings.scrapable_mtavari_news_endpoint, **client_config
            )
            yield ScrapeNewsMtavariClient(client, use_scrape_do=False)
    finally:
        if client:
            logger.debug(
                "Closing Mtavari client transport",
                extra={
                    "json_fields": {"operation": "mtavari_client_cleanup"},
                    "labels": {"component": "mtavari_client"},
                },
            )
            await client.aclose()
        else:
            logger.debug(
                "No Mtavari client was created",
                extra={
                    "json_fields": {"operation": "mtavari_client_cleanup_no_client"},
                    "labels": {"component": "mtavari_client"},
                },
            )


async def get_scrape_mtavari_news_dependency():
    async with get_scrape_mtavari_news_client() as client:
        yield client
