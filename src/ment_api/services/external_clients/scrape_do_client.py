from contextlib import asynccontextmanager
import logging
from typing import Optional, Dict, Any
import httpx
from ment_api.configurations.config import settings
import json
import base64
from tenacity import (
    retry,
    wait_random_exponential,
    before_sleep_log,
    RetryError,
    stop_after_attempt,
)

logger = logging.getLogger(__name__)


class ScrapeDoClient:
    def __init__(self, client: httpx.AsyncClient):
        self.client = client

    async def _handle_response(self, response: httpx.Response) -> Optional[str]:
        try:
            response.raise_for_status()
            return response.text
        except httpx.HTTPStatusError as e:
            error_detail = {}
            try:
                error_detail = response.json()
            except ValueError:
                error_detail = {"detail": response.text}

            logger.error(f"HTTP error {e.response.status_code}: {error_detail}")
        return None

    async def scrape(
        self, scrape_url: str, render: bool = False, wait_until: str = None
    ) -> Optional[str]:
        query_params = [
            ("token", settings.scrape_do_token),
            ("url", scrape_url),
            ("geoCode", "GE"),
            ("super", "true"),
            ("output", "markdown"),
        ]

        # Only add customHeaders for non-Facebook URLs
        if "facebook.com" not in scrape_url:
            query_params.append(("customHeaders", "false"))

        if render:
            query_params.append(("render", "true"))

        if wait_until:
            query_params.append(("waitUntil", wait_until))

        response = await self.client.get("", params=query_params)
        return await self._handle_response(response)

    async def scrape_raw_html(
        self,
        scrape_url: str,
        use_render: bool = False,
        wait_until: str = None,
        custom_wait: int = None,
    ) -> Optional[str]:
        """
        Scrapes the raw HTML content of a URL with optional rendering support.
        Useful for parsing OG tags or other metadata directly from the HTML.

        Args:
            scrape_url: The URL to scrape.
            use_render: Whether to use headless browser rendering (needed for JS-heavy sites like YouTube).
            wait_until: When to consider navigation complete ('domcontentloaded', 'load', 'networkidle0', 'networkidle2').
            custom_wait: Additional wait time in milliseconds after page load.

        Returns:
            The raw HTML content as a string, or None if an error occurs.
        """
        logger.info(f"Scraping raw HTML for URL: {scrape_url} (render: {use_render})")
        query_params = [
            ("token", settings.scrape_do_token),
            ("url", scrape_url),
            ("geoCode", "GE"),
            ("super", "true"),
        ]

        # Add rendering parameters if requested
        if use_render:
            query_params.append(("render", "true"))
            query_params.append(
                ("blockResources", "false")
            )  # Don't block resources for OG tag scraping

            if wait_until:
                query_params.append(("waitUntil", wait_until))
            else:
                query_params.append(
                    ("waitUntil", "load")
                )  # Wait for all resources by default

            if custom_wait:
                query_params.append(("customWait", str(custom_wait)))

        # Only add customHeaders for non-Facebook URLs
        if "facebook.com" not in scrape_url:
            query_params.append(("customHeaders", "false"))

        response = await self.client.get("", params=query_params)
        return await self._handle_response(response)

    async def scrape_og_tags(self, scrape_url: str) -> Optional[str]:
        """
        Specialized method for scraping OG tags from JavaScript-heavy sites.
        Automatically determines if rendering is needed based on the URL.

        Args:
            scrape_url: The URL to scrape.

        Returns:
            The raw HTML content as a string, or None if an error occurs.
        """
        # Sites that typically require JavaScript rendering for OG tags
        js_heavy_domains = [
            "youtube.com",
            "youtu.be",
            "twitter.com",
            "x.com",
            "instagram.com",
            "tiktok.com",
            "facebook.com",
            "linkedin.com",
        ]

        # Check if the URL contains any JS-heavy domains
        use_render = any(domain in scrape_url.lower() for domain in js_heavy_domains)

        if use_render:
            logger.info(f"Using rendering for OG tag scraping: {scrape_url}")
            # For JS-heavy sites, use longer wait times
            return await self.scrape_raw_html(
                scrape_url=scrape_url,
                use_render=True,
                wait_until="load",
                custom_wait=3000,  # Wait 3 seconds after load for dynamic content
            )
        else:
            logger.info(
                f"Using non-rendered scraping for OG tag scraping: {scrape_url}"
            )
            return await self.scrape_raw_html(scrape_url=scrape_url, use_render=False)

    async def scrape_youtube_video(self, scrape_url: str) -> Optional[str]:
        """
        Specialized method for scraping YouTube video pages with optimal parameters.
        Uses headless browser rendering with appropriate wait times for YouTube's dynamic content.

        Args:
            scrape_url: The YouTube video URL to scrape.

        Returns:
            The raw HTML content as a string, or None if an error occurs.
        """
        logger.info(f"Scraping YouTube video for URL: {scrape_url}")
        query_params = [
            ("token", settings.scrape_do_token),
            ("url", scrape_url),
            ("geoCode", "US"),  # Use US for better YouTube compatibility
            ("super", "true"),
            ("render", "true"),
            ("waitUntil", "networkidle2"),  # Wait until network is mostly idle
            ("customWait", "5000"),  # Wait 5 seconds for dynamic content to load
            (
                "blockResources",
                "false",
            ),  # Don't block resources to ensure meta tags load
            ("device", "desktop"),  # Use desktop device for consistent results
            ("customHeaders", "false"),
        ]

        response = await self.client.get("", params=query_params)
        return await self._handle_response(response)

    # Error usually is concurrency issue, so we retry a few times
    @retry(
        wait=wait_random_exponential(multiplier=1, max=3),
        before_sleep=before_sleep_log(logger, logging.WARNING),
        reraise=True,  # Reraise the original exception after retries are exhausted
        stop=stop_after_attempt(4),
    )
    async def scrape_with_screenshot(
        self,
        scrape_url: str,
        full_page: bool = True,
        wait_until: str = None,
        width: Optional[int] = None,
        height: Optional[int] = None,
        particularScreenShot: str = None,
    ) -> Optional[Dict[str, Any]]:
        """
        Make a single call to scrape.do to get both content and screenshot

        Args:
            scrape_url: The URL to scrape
            full_page: Whether to capture a full page screenshot (default: True)
            wait_until: Optional parameter to specify when to consider navigation complete

        Returns:
            Optional dictionary containing content, screenshot data, and other metadata
        """

        logger.info(f"Scraping URL with screenshot: {scrape_url}")

        query_params = [
            ("token", settings.scrape_do_token),
            ("url", scrape_url),
            ("geoCode", "GE"),
            ("super", "true"),
            ("render", "true"),
            ("returnJSON", "true"),
            ("output", "markdown"),
            ("device", "tablet"),
        ]

        # Add fullScreenshot parameter if full_page is True
        if full_page:
            query_params.append(("fullScreenShot", "true"))

        # Add playWithBrowser parameter to remove the specified element
        play_with_browser_script = [
            # Page is not fully loaded still, this is necessary for now
            {"Action": "Wait", "Timeout": 5000},
            # Remove the ƒacebook login modal when the page loads as it obstructs actual post.
            {
                "Action": "Execute",
                "Execute": """
                document.querySelectorAll('.__fb-light-mode').forEach(el => {
                    if (el.tagName.toLowerCase() === 'html') return;
                    if (el.querySelector('.__fb-light-mode')) {
                        el.remove();
                    }
                });
                """,
            },
        ]
        query_params.append(("playWithBrowser", json.dumps(play_with_browser_script)))

        # Only add customHeaders for non-Facebook URLs
        if "facebook.com" not in scrape_url:
            query_params.append(("customHeaders", "false"))

        if wait_until:
            query_params.append(("waitUntil", wait_until))

        if width:
            query_params.append(("width", str(width)))

        if height:
            query_params.append(("height", str(height)))

        if particularScreenShot:
            query_params.append(("particularScreenShot", particularScreenShot))

        try:
            response = await self.client.get("", params=query_params)
            if response.status_code == 200:
                json_response = json.loads(response.text)

                result = {
                    "content": json_response.get("content", ""),
                    "screenshot_data": None,
                    "raw_response": json_response,
                }
                # Extract screenshot if available
                if (
                    "screenShots" in json_response
                    and len(json_response["screenShots"]) > 0
                ):
                    image_b64 = json_response["screenShots"][0]["image"]
                    result["screenshot_data"] = base64.b64decode(image_b64)

                return result
            else:
                logger.error(
                    f"Scrape with screenshot failed with status: {response.status_code}"
                )
                raise httpx.HTTPStatusError(
                    f"HTTP error {response.status_code}",
                    request=response.request,
                    response=response,
                )
        except httpx.HTTPStatusError:
            # Let retry handle this specific exception
            raise
        except RetryError as e:
            logger.error(
                f"Failed to scrape with screenshot after multiple retries: {e}"
            )
            return None
        except Exception as e:
            logger.error(f"Error scraping with screenshot: {e}")
            raise  # Re-raise for retry

    def _is_facebook_login_page(self, html_content: str) -> bool:
        """
        Detects if the scraped Facebook content is the generic login page
        instead of the actual post/page content.

        Args:
            html_content: The HTML content to check

        Returns:
            True if this appears to be a Facebook login page, False otherwise
        """
        if not html_content:
            return False

        login_indicators = [
            "Log into Facebook",
            "Log in to Facebook",
            "Facebook - log in or sign up",
            "start sharing and connecting with your friends",
            "Connect with friends and the world around you on Facebook",
            "facebook.com/login",
            "Create an account or log into Facebook",
        ]

        html_lower = html_content.lower()
        return any(indicator.lower() in html_lower for indicator in login_indicators)

    def process_facebook_metadata(
        self, title: str, description: str, html_content: str = None
    ) -> dict:
        """
        Processes Facebook metadata and returns empty values if the content
        appears to be from a login page instead of the actual post.

        Args:
            title: The extracted title
            description: The extracted description
            html_content: Optional HTML content to check for login page indicators

        Returns:
            Dict with processed title and description
        """
        # Check if title indicates login page
        login_title_indicators = [
            "log into facebook",
            "log in to facebook",
            "facebook - log in or sign up",
        ]

        # Check if description indicates login page
        login_desc_indicators = [
            "log into facebook to start sharing and connecting",
            "connect with friends and the world around you on facebook",
            "log in to facebook to start sharing",
        ]

        title_lower = title.lower() if title else ""
        desc_lower = description.lower() if description else ""

        # Check if title or description indicates login page
        is_login_title = any(
            indicator in title_lower for indicator in login_title_indicators
        )
        is_login_desc = any(
            indicator in desc_lower for indicator in login_desc_indicators
        )

        # Also check HTML content if provided
        is_login_html = (
            self._is_facebook_login_page(html_content) if html_content else False
        )

        if is_login_title or is_login_desc or is_login_html:
            logger.warning(
                "Detected Facebook login page instead of actual content, returning empty metadata"
            )
            return {"title": "", "description": ""}

        return {"title": title, "description": description}


@asynccontextmanager
async def get_scrape_do_client():
    logger.debug("Created new ScrapeDo client for request")
    try:
        client = httpx.AsyncClient(
            base_url=settings.scrape_do_base_url,
            timeout=120.0,
        )
        yield ScrapeDoClient(client)
    finally:
        logger.debug("Closing ScrapeDo client after request")
        await client.aclose()


async def get_scrape_do_dependency():
    async with get_scrape_do_client() as client:
        yield client
