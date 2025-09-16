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
    RawGenericRSSResponse,
)
from ment_api.services.external_clients.scrape_news_base_client import (
    ScrapeNewsBaseClient,
)

logger = logging.getLogger(__name__)


class ScrapeNewsRadioTavisulebaClient(ScrapeNewsBaseClient):
    def __init__(
        self,
        client: httpx.AsyncClient,
        use_scrape_do: bool = False,
        target_base_url: Optional[str] = None,
    ):
        super().__init__(client)
        self.use_scrape_do = use_scrape_do
        self.target_base_url = target_base_url

    def _parse_rss_date(self, date_str: str) -> datetime:
        """Parse RSS date format to datetime object."""
        try:
            # RSS date format: "Fri, 18 Jul 2025 14:47:35 +0000"
            # Remove timezone info for now since we're using naive datetime
            date_part = date_str.split(" +")[0]  # Remove timezone
            return datetime.strptime(date_part, "%a, %d %b %Y %H:%M:%S")
        except ValueError as e:
            logger.warning(
                "Could not parse date",
                extra={
                    "json_fields": {
                        "date_str": date_str,
                        "error": str(e),
                        "operation": "parse_rss_date",
                    },
                    "labels": {"component": "radiotavisupleba_client"},
                },
            )
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
                target_url = (
                    f"{self.target_base_url}/{endpoint}"
                    if endpoint
                    else self.target_base_url
                )
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
                        "HTTP request failed via scrape.do",
                        extra={
                            "json_fields": {
                                "status_code": response.status_code,
                                "response_preview": response.text[:100],
                                "params": scrape_do_params,
                                "operation": "http_request_scrape_do",
                            },
                            "labels": {"component": "radiotavisupleba_client"},
                        },
                    )
            else:
                # For direct connection, use the endpoint with the base URL configured in client
                response = await self.client.get(endpoint, params=params)
                if response.status_code != 200:
                    logger.error(
                        "HTTP request failed direct connection",
                        extra={
                            "json_fields": {
                                "status_code": response.status_code,
                                "response_preview": response.text[:100],
                                "endpoint": endpoint,
                                "operation": "http_request_direct",
                            },
                            "labels": {"component": "radiotavisupleba_client"},
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
                        "operation": "http_request_error",
                    },
                    "labels": {"component": "radiotavisupleba_client"},
                },
            )
            raise
        except httpx.HTTPStatusError as exc:
            logger.error(
                "HTTP status error",
                extra={
                    "json_fields": {
                        "status_code": exc.response.status_code,
                        "url": str(exc.request.url),
                        "operation": "http_status_error",
                    },
                    "labels": {"component": "radiotavisupleba_client"},
                },
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
            logger.info(
                "XML response parsed",
                extra={
                    "json_fields": {"result": result.model_dump_json()},
                    "labels": {"component": "radiotavisupleba_client"},
                },
            )
            return result
        except httpx.HTTPStatusError as e:
            logger.error(
                "HTTP status error during XML parsing",
                extra={
                    "json_fields": {
                        "status_code": e.response.status_code,
                        "host": e.request.url.host,
                        "response_preview": response.text[:100],
                        "operation": "xml_parse_http_error",
                    },
                    "labels": {"component": "radiotavisupleba_client"},
                },
            )
        except httpx.RequestError as e:
            logger.error(
                "Request error during XML parsing",
                extra={
                    "json_fields": {
                        "host": e.request.url.host,
                        "error": str(e),
                        "operation": "xml_parse_request_error",
                    },
                    "labels": {"component": "radiotavisupleba_client"},
                },
            )
        except Exception as e:
            logger.error(
                "Failed to parse XML response",
                extra={
                    "json_fields": {
                        "response_preview": response.text[:100],
                        "error": str(e),
                        "operation": "xml_parse_error",
                    },
                    "labels": {"component": "radiotavisupleba_client"},
                },
            )
        return None

    async def scrape_news(self, news_quantity: int = 20) -> Optional[NewsResponse]:
        try:
            endpoint = "api/zpvpil-vomx-tpe_qyp"

            raw_rss_response: Optional[RawGenericRSSResponse] = None

            if self.use_scrape_do:
                target_url = f"{self.target_base_url}/{endpoint}"
                logger.debug(
                    "Using scrape.do for RadioTavisupleba RSS feed",
                    extra={
                        "json_fields": {
                            "target_url": target_url,
                            "operation": "scrape_news_scrape_do",
                        },
                        "labels": {"component": "radiotavisupleba_client"},
                    },
                )
                response = await self._make_http_request(endpoint)
                raw_rss_response = await self._handle_xml_response(
                    response, RawGenericRSSResponse
                )
            else:
                logger.debug(
                    "Using direct connection for RadioTavisupleba RSS feed",
                    extra={
                        "json_fields": {
                            "endpoint": endpoint,
                            "operation": "scrape_news_direct",
                        },
                        "labels": {"component": "radiotavisupleba_client"},
                    },
                )
                response = await self._make_http_request(endpoint)
                raw_rss_response = await self._handle_xml_response(
                    response, RawGenericRSSResponse
                )

            if not raw_rss_response or not raw_rss_response.channel:
                logger.error(
                    "Failed to scrape news or parse RSS response",
                    extra={
                        "json_fields": {"operation": "scrape_news_failed"},
                        "labels": {"component": "radiotavisupleba_client"},
                    },
                )
                return NewsResponse(news_items=[])

            news_items = []
            # Limit to requested quantity
            items_to_process = raw_rss_response.channel.items[:news_quantity]

            logger.info(
                "Items to process",
                extra={
                    "json_fields": {
                        "items_to_process": len(raw_rss_response.channel.items)
                    },
                    "labels": {"component": "radiotavisupleba_client"},
                },
            )

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

                    image_url = item.enclosure.url if item.enclosure else ""

                    news_items.append(
                        NewsItem(
                            external_id=str(external_id),
                            title=item.title,
                            content=content,
                            details_url=item.link,
                            small_image_url=image_url,
                            medium_image_url=image_url,
                            big_image_url=image_url,
                            created_at=self._parse_rss_date(item.pub_date),
                            category=NewsCategory.POLITICS,
                            source=NewsSource.RADIOTAVISUPLEBA,
                        )
                    )
                except Exception as e:
                    logger.warning(
                        "Could not process RadioTavisupleba RSS item",
                        extra={
                            "json_fields": {
                                "title": item.title,
                                "error": str(e),
                                "operation": "process_rss_item",
                            },
                            "labels": {"component": "radiotavisupleba_client"},
                        },
                    )
                    continue

            logger.info(
                "Successfully scraped RadioTavisupleba news",
                extra={
                    "json_fields": {
                        "items_count": len(news_items),
                        "operation": "scrape_news_success",
                    },
                    "labels": {"component": "radiotavisupleba_client"},
                },
            )

            return NewsResponse(news_items=news_items)
        except httpx.HTTPError as e:
            logger.error(
                "HTTP error scraping RadioTavisupleba news",
                extra={
                    "json_fields": {
                        "error": str(e),
                        "operation": "scrape_news_http_error",
                    },
                    "labels": {"component": "radiotavisupleba_client"},
                },
            )
            return NewsResponse(news_items=[])
        except Exception as e:
            logger.error(
                "Error scraping RadioTavisupleba news",
                extra={
                    "json_fields": {"error": str(e), "operation": "scrape_news_error"},
                    "labels": {"component": "radiotavisupleba_client"},
                },
            )
            return NewsResponse(news_items=[])


@asynccontextmanager
async def get_scrape_radiotavisupleba_news_client():
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
                "Production: Configuring RadioTavisupleba client via Scrape.do with HTTP/1.1",
                extra={
                    "json_fields": {"operation": "client_init_prod"},
                    "labels": {
                        "component": "radiotavisupleba_client",
                        "environment": "production",
                    },
                },
            )
            client = httpx.AsyncClient(
                base_url=settings.scrape_do_base_url, **client_config
            )
            yield ScrapeNewsRadioTavisulebaClient(
                client,
                use_scrape_do=True,
                target_base_url=settings.scrapable_radiotavisupleba_news_endpoint,
            )
        else:
            logger.debug(
                "Development: Configuring direct RadioTavisupleba client with HTTP/1.1",
                extra={
                    "json_fields": {"operation": "client_init_dev"},
                    "labels": {
                        "component": "radiotavisupleba_client",
                        "environment": "development",
                    },
                },
            )
            client = httpx.AsyncClient(
                base_url=settings.scrapable_radiotavisupleba_news_endpoint,
                **client_config,
            )
            yield ScrapeNewsRadioTavisulebaClient(client, use_scrape_do=False)
    finally:
        if client:
            logger.debug(
                "Closing RadioTavisupleba client transport",
                extra={
                    "json_fields": {"operation": "client_close"},
                    "labels": {"component": "radiotavisupleba_client"},
                },
            )
            await client.aclose()
        else:
            logger.debug(
                "No RadioTavisupleba client was created",
                extra={
                    "json_fields": {"operation": "client_close_no_client"},
                    "labels": {"component": "radiotavisupleba_client"},
                },
            )


async def get_scrape_radiotavisupleba_news_dependency():
    async with get_scrape_radiotavisupleba_news_client() as client:
        yield client
