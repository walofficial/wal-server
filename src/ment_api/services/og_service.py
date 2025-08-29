from typing import Optional, List
from urllib.parse import urljoin
import logging

from bs4 import BeautifulSoup

from ment_api.services.external_clients.scrape_do_client import get_scrape_do_client
from ment_api.models.link_preview_data import LinkPreviewData


def _get_meta_tag_content(soup: BeautifulSoup, property: str) -> Optional[str]:
    """Helper to find meta tag content by property or name."""
    tag = soup.find("meta", property=property) or soup.find(
        "meta", attrs={"name": property}
    )
    return tag.get("content") if tag else None


def _get_og_images(soup: BeautifulSoup, base_url: str) -> List[str]:
    """Extract og:image URLs."""
    images = []
    og_image_tags = soup.find_all("meta", property="og:image")
    for tag in og_image_tags:
        if content := tag.get("content"):
            # Convert relative URLs to absolute
            full_url = urljoin(base_url, content)
            images.append(full_url)

    return images


async def get_og_preview(
    url: str, platform: Optional[str] = None
) -> Optional[LinkPreviewData]:
    """
    Fetches and parses basic Open Graph metadata from a URL.
    Returns og:image URLs as-is without downloading/uploading.
    """
    try:
        async with get_scrape_do_client() as scrape_do_client:
            logging.info(f"Fetching OG preview for {url}")
            html_content = await scrape_do_client.scrape_raw_html(url)

            if not html_content:
                logging.warning(f"No content returned for {url}")
                return None

        soup = BeautifulSoup(html_content, "html.parser")

        # Extract basic OG metadata
        title = _get_meta_tag_content(soup, "og:title") or (
            soup.find("title").string if soup.find("title") else None
        )
        description = _get_meta_tag_content(
            soup, "og:description"
        ) or _get_meta_tag_content(soup, "description")
        site_name = _get_meta_tag_content(soup, "og:site_name")

        # Get the canonical URL from og:url or use the original URL
        og_url_tag = soup.find("meta", property="og:url")
        canonical_url = og_url_tag.get("content") if og_url_tag else url

        # Get og:image URLs (no downloading/uploading)
        og_images = _get_og_images(soup, canonical_url)

        preview_data = LinkPreviewData(
            url=canonical_url,
            title=title.strip() if title else None,
            description=description.strip() if description else None,
            images=og_images if og_images else None,
            site_name=site_name.strip() if site_name else None,
            platform=platform,
        )

        return preview_data

    except Exception as e:
        logging.error(f"Error processing OG data for {url}: {e}")
        return None
