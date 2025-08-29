import logging
import re
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
    RawGenericRSSResponse,
)
from ment_api.services.external_clients.scrape_news_base_client import (
    ScrapeNewsBaseClient,
)

logger = logging.getLogger(__name__)


class ScrapeNewsNetgazetiClient(ScrapeNewsBaseClient):
    def __init__(
        self,
        client: httpx.AsyncClient,
        use_scrape_do: bool = False,
        target_base_url: Optional[str] = None,
    ):
        super().__init__(client)
        self.use_scrape_do = use_scrape_do
        self.target_base_url = target_base_url

    def _clean_html_content(self, html_content: str) -> str:
        """Remove HTML tags and clean up the content."""
        # Remove HTML tags
        clean_text = re.sub(r"<[^>]+>", "", html_content)
        # Replace HTML entities
        clean_text = (
            clean_text.replace("&amp;", "&")
            .replace("&lt;", "<")
            .replace("&gt;", ">")
            .replace("&quot;", '"')
        )
        # Clean up extra whitespace
        clean_text = " ".join(clean_text.split())
        return clean_text.strip()

    def _parse_rss_date(self, date_str: str) -> datetime:
        """Parse RSS date format to datetime object."""
        try:
            # RSS date format: "Fri, 18 Jul 2025 14:47:35 +0000"
            # Remove timezone info for now since we're using naive datetime
            date_part = date_str.split(" +")[0]  # Remove timezone
            return datetime.strptime(date_part, "%a, %d %b %Y %H:%M:%S")
        except ValueError as e:
            logger.warning(f"Could not parse date {date_str}: {e}")
            return datetime.now()

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
                target_url = f"{self.target_base_url}/{endpoint}"
                if params:
                    query_string = "&".join([f"{k}={v}" for k, v in params.items()])
                    target_url = f"{target_url}?{query_string}"

                scrape_do_params = {
                    "token": settings.scrape_do_token,
                    "url": target_url,
                    "geoCode": "GE",
                    "super": True,
                }
                response = await self.client.get("", params=scrape_do_params)
                if response.status_code != 200:
                    logger.error(
                        f"Response: {response.text[:100]}, Params: {scrape_do_params}"
                    )
            else:
                response = await self.client.get(endpoint, params=params)
                if response.status_code != 200:
                    logger.error(
                        f"Response: {response.text[:100]}, Endpoint: {endpoint}"
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
                f"HTTP error {exc.response.status_code} while requesting {exc.request.url!r}"
            )
            raise

    async def _handle_xml_response(
        self, response: httpx.Response, model_class
    ) -> Optional[RawGenericRSSResponse]:
        """Handle XML response parsing using Pydantic-XML."""
        try:
            response.raise_for_status()
            xml_content = response.text
            result = model_class.from_xml(xml_content)
            return result
        except httpx.HTTPStatusError as e:
            logger.error(
                f"HTTP status error {e.response.status_code} from {e.request.url.host}: {response.text[:100]}"
            )
        except httpx.RequestError as e:
            logger.error(
                f"Request error while processing response from {e.request.url.host}: {e}"
            )
        except Exception as e:
            logger.error(
                f"Failed to parse XML response: {response.text[:100]} with exception {e}"
            )
        return None

    async def scrape_news(self, news_quantity: int = 20) -> Optional[NewsResponse]:
        try:
            endpoint = "category/life/feed/"

            raw_rss_response: Optional[RawGenericRSSResponse] = None

            if self.use_scrape_do:
                logger.debug(
                    f"Using scrape.do for Netgazeti RSS feed. Target: {self.target_base_url}/{endpoint}"
                )
                response = await self._make_http_request(endpoint)
                raw_rss_response = await self._handle_xml_response(
                    response, RawGenericRSSResponse
                )
            else:
                logger.debug(
                    f"Using direct connection for Netgazeti RSS feed. Endpoint: {endpoint}"
                )
                response = await self._make_http_request(endpoint)
                raw_rss_response = await self._handle_xml_response(
                    response, RawGenericRSSResponse
                )

            if not raw_rss_response or not raw_rss_response.channel:
                logger.error("Failed to scrape news or parse RSS response")
                return NewsResponse(news_items=[])

            news_items = []
            # Limit to requested quantity
            items_to_process = raw_rss_response.channel.items[:news_quantity]

            for item in items_to_process:
                try:
                    # Extract content from description (removing HTML tags)
                    content = self._clean_html_content(item.description)

                    # Generate external_id from guid or link
                    external_id = (
                        item.guid
                        if item.guid
                        else (
                            item.link.split("/")[-2]
                            if item.link.endswith("/")
                            else item.link.split("/")[-1]
                        )
                    )

                    news_items.append(
                        NewsItem(
                            external_id=str(external_id),
                            title=item.title,
                            content=content,
                            details_url=item.link,
                            small_image_url="",  # RSS doesn't provide image URLs
                            medium_image_url="",
                            big_image_url="",
                            created_at=self._parse_rss_date(item.pub_date),
                            category=NewsCategory.POLITICS,
                            source=NewsSource.NETGAZETI,
                        )
                    )
                except Exception as e:
                    logger.warning(
                        f"Could not process Netgazeti RSS item: {item.title}, error: {e}"
                    )
                    continue

            return NewsResponse(news_items=news_items)
        except httpx.HTTPError as e:
            logger.error(f"HTTP error scraping Netgazeti news: {str(e)}", exc_info=True)
            return NewsResponse(news_items=[])
        except Exception as e:
            logger.error(f"Error scraping Netgazeti news: {str(e)}", exc_info=True)
            return NewsResponse(news_items=[])


@asynccontextmanager
async def get_scrape_netgazeti_news_client():
    client = None
    try:
        client_config = {
            "headers": {
                "Accept": "application/rss+xml, application/xml, text/xml",
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
                "Production: Configuring Netgazeti client via Scrape.do with HTTP/1.1"
            )
            client = httpx.AsyncClient(
                base_url=settings.scrape_do_base_url, **client_config
            )
            yield ScrapeNewsNetgazetiClient(
                client,
                use_scrape_do=True,
                target_base_url=settings.scrapable_netgazeti_news_endpoint,
            )
        else:
            logger.debug(
                "Development: Configuring direct Netgazeti client with HTTP/1.1"
            )
            client = httpx.AsyncClient(
                base_url=settings.scrapable_netgazeti_news_endpoint, **client_config
            )
            yield ScrapeNewsNetgazetiClient(client, use_scrape_do=False)
    finally:
        if client:
            logger.debug("Closing Netgazeti client transport")
            await client.aclose()
        else:
            logger.debug("No Netgazeti client was created.")


async def get_scrape_netgazeti_news_dependency():
    async with get_scrape_netgazeti_news_client() as client:
        yield client
