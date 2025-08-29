import yt_dlp
import logging
import datetime
import os
import http.cookiejar  # For cookie jar type checking
import asyncio
from typing import Dict, Optional

# Configure basic logging (optional, but helpful for debugging)
# Consider configuring logging more centrally in your application
logging.basicConfig(level=logging.ERROR)  # Only show errors from yt_dlp


def format_duration(seconds):
    """Formats duration in seconds to HH:MM:SS or MM:SS."""
    if seconds is None:
        return "N/A"
    try:
        # Create a timedelta object and format it (handles days correctly if needed)
        delta = datetime.timedelta(
            seconds=int(seconds)
        )  # Use int to avoid microseconds
        return str(delta)
    except TypeError:
        return "N/A"  # Handle cases where duration might not be numeric


def check_cookies_loaded(cookies_path=None, test_url=None):
    """
    Checks if YouTube cookies are properly loaded from the specified file.

    Args:
        cookies_path (str, optional): Path to the cookies file.
        test_url (str, optional): A URL to test cookie usage with info extraction.

    Returns:
        bool: True if cookies were successfully loaded, False otherwise.
    """
    print(f"Checking cookies loaded for {cookies_path}")
    ydl_opts = {
        # "quiet": True,
        # "no_warnings": True,
        "logger": logging.getLogger("yt_dlp_cookie_checker"),
        "verbose": True,
    }

    cookies_loaded_successfully = False
    ydl = None

    # Add cookiefile option if path provided
    if cookies_path:
        if os.path.exists(cookies_path):
            ydl_opts["cookiefile"] = cookies_path
            logging.info(f"Attempting to load cookies from: {cookies_path}")
        else:
            logging.error(f"Cookie file specified but not found: {cookies_path}")
            return False
    else:
        logging.info("No cookie file specified.")
        return False

    try:
        # Initialize YoutubeDL with options
        ydl = yt_dlp.YoutubeDL(ydl_opts)

        # Check the cookiejar
        if hasattr(ydl, "cookiejar") and isinstance(
            ydl.cookiejar, http.cookiejar.CookieJar
        ):
            cookie_count = len(ydl.cookiejar)
            if cookie_count > 0:
                logging.info(f"Cookie jar initialized with {cookie_count} cookie(s).")
                cookies_loaded_successfully = True
            else:
                logging.warning(
                    f"Cookie jar is empty after loading from {cookies_path}."
                )
                cookies_loaded_successfully = False
        else:
            logging.error("YoutubeDL object does not have a valid cookiejar attribute.")
            return False

        # Optionally test the cookies with a real request
        if test_url and cookies_loaded_successfully:
            logging.info(f"Testing cookies with URL: {test_url}")
            ydl.extract_info(test_url, download=False, process=False)
            logging.info("Info extraction completed without cookie errors.")

    except yt_dlp.utils.CookieLoadError as e:
        logging.error(f"Failed to load or parse cookies: {e}")
        return False
    except Exception as e:
        logging.error(f"An unexpected error occurred while checking cookies: {e}")
        return False
    finally:
        # Clean up resources
        if ydl:
            ydl.close()

    return cookies_loaded_successfully


async def get_youtube_info_async(url: str) -> Dict[str, Optional[str]]:
    cookies_path = "cookies.txt"

    """
    Async function to extract both YouTube ID and duration in a single call.

    Args:
        url (str): The YouTube URL.
        cookies_path (str, optional): Path to a cookies file for authenticated requests.

    Returns:
        Dict[str, Optional[str]]: Dictionary containing 'id', 'duration_seconds', and 'duration_formatted'.
                                 Returns None values if extraction fails.
    """
    result = {"id": None, "duration_seconds": None, "duration_formatted": None}

    ydl_opts = {
        "verbose": True,
        "logger": logging.getLogger("yt_dlp_info_extractor"),
    }

    # Add cookies file if provided and exists
    if cookies_path and os.path.exists(cookies_path):
        ydl_opts["cookiefile"] = cookies_path
        logging.info(f"Using cookies from: {cookies_path} for YouTube info extraction")
    elif cookies_path:
        logging.warning(f"Cookie file specified but not found: {cookies_path}")

    try:
        # Run the blocking yt-dlp operation in a thread pool to make it async-compatible
        def extract_info():
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                return ydl.extract_info(url, download=False, process=True)

        # Execute in thread pool to avoid blocking the event loop
        info_dict = await asyncio.get_event_loop().run_in_executor(None, extract_info)

        if info_dict:
            # Extract ID
            result["id"] = info_dict.get("id")

            # Extract duration for videos only
            if info_dict.get("_type", "video") == "video":
                duration = info_dict.get("duration")
                if duration is not None:
                    result["duration_seconds"] = float(duration)
                    result["duration_formatted"] = format_duration(duration)
                    result["duration_seconds"] = int(duration)
                else:
                    if info_dict.get("is_live"):
                        logging.warning(
                            f"URL is a live stream, duration not applicable: {url}"
                        )
                    else:
                        logging.warning(
                            f"Duration information not found in metadata for: {url}"
                        )
            else:
                logging.warning(
                    f"URL is not a single video ({info_dict.get('_type')}), cannot get single duration: {url}"
                )
        else:
            logging.warning(f"Could not extract youtube info for URL: {url}")

    except yt_dlp.utils.DownloadError as e:
        logging.error(f"Error extracting YouTube info for {url}: {e}")
    except Exception as e:
        logging.error(
            f"An unexpected error occurred while extracting YouTube info for {url}: {e}"
        )

    return result
