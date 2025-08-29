import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Optional
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
    RawImediNewsResponse,
)
from ment_api.services.external_clients.scrape_news_base_client import (
    ScrapeNewsBaseClient,
)

logger = logging.getLogger(__name__)


class ScrapeNewsImediClient(ScrapeNewsBaseClient):
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
                f"HTTP error {exc.response.status_code} while requesting {exc.request.url!r} params: {params}"
            )
            raise

    async def scrape_news(self, news_quantity: int = 13) -> Optional[NewsResponse]:
        try:
            imedi_params = {
                "skipCount": 0,
                "portion": news_quantity,
                "categoryId": 3,
                "pageId": 1,
            }

            if self.use_scrape_do:
                if not self.target_base_url:
                    logger.error(
                        "Target base URL must be provided when using scrape.do"
                    )
                    return NewsResponse(news_items=[])

                query_string = urlencode(imedi_params)
                full_target_url = f"{self.target_base_url}?{query_string}"

                scrape_do_params = {
                    "token": settings.scrape_do_token,
                    "url": full_target_url,
                    "geoCode": "GE",
                    "super": True,
                }
                logger.debug(f"Using scrape.do for Imedi. Target: {full_target_url}")
                raw_response = await self._make_http_request(scrape_do_params)

            else:
                logger.debug("Using direct connection for Imedi.")
                raw_response = await self._make_http_request(imedi_params)

            raw_imedi_response = await self._handle_response(
                raw_response, RawImediNewsResponse
            )

            if not raw_imedi_response:
                logger.error("Failed to scrape news or parse response")
                return NewsResponse(news_items=[])

            response = NewsResponse(
                news_items=[
                    NewsItem(
                        external_id=str(item.id),
                        title=item.title,
                        content=item.content,
                        details_url=self.insert_category_in_url(item.details_url),
                        small_image_url=item.small_image_url,
                        medium_image_url=item.medium_image_url,
                        big_image_url=item.big_image_url,
                        created_at=self.extract_datetime(
                            item.date, datetime.now(timezone.utc)
                        ),
                        category=NewsCategory.POLITICS,
                        source=NewsSource.IMEDI,
                    )
                    for item in raw_imedi_response.news_items
                ]
            )

            return response
        except httpx.HTTPError as e:
            logger.error(
                f"HTTP error scraping Imedi news: {str(e)[:100]}", exc_info=True
            )
            return NewsResponse(news_items=[])
        except Exception as e:
            logger.error(f"Error scraping Imedi news: {str(e)[:100]}", exc_info=True)
            return NewsResponse(news_items=[])

    def extract_datetime(self, parsed_date: str, default: datetime) -> datetime:
        if parsed_date.startswith("/Date(") and parsed_date.endswith(")/"):
            return datetime.fromtimestamp(float(parsed_date[6:-2]) / 1000.0)

        return default

    def insert_category_in_url(self, url: str, category="politika"):
        if "/ge/" not in url:
            return url

        base, rest = url.split("/ge/", 1)

        rest = rest.lstrip("/")

        return f"{base}/ge/{category}/{rest}"


@asynccontextmanager
async def get_scrape_imedi_news_client():
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
                "Production: Configuring Imedi client via Scrape.do with HTTP/1.1"
            )
            client = httpx.AsyncClient(
                base_url=settings.scrape_do_base_url, **client_config
            )
            yield ScrapeNewsImediClient(
                client,
                use_scrape_do=True,
                target_base_url=settings.scrapable_imedi_news_endpiont,
            )
        else:
            logger.debug("Development: Configuring direct Imedi client with HTTP/1.1")
            client = httpx.AsyncClient(
                base_url=settings.scrapable_imedi_news_endpiont, **client_config
            )
            yield ScrapeNewsImediClient(client, use_scrape_do=False)
    finally:
        if client:
            logger.debug("Closing Imedi client transport")
            await client.aclose()
        else:
            logger.debug("No Imedi client was created.")


async def get_scrape_imedi_news_dependency():
    async with get_scrape_imedi_news_client() as client:
        yield client
