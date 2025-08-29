from typing import Tuple

import geoip2.database
from fastapi import Request


# Initialize GeoIP2 reader once per process
_geoip_reader = geoip2.database.Reader("GeoLite2-Country.mmdb")


def _detect_client_ip(request: Request) -> str:
    """Best-effort extraction of the original client IP from common proxy headers."""
    # Method 1: X-Forwarded-For header (most common for Cloud Run and load balancers)
    x_forwarded_for = request.headers.get("X-Forwarded-For")
    if x_forwarded_for:
        # X-Forwarded-For can contain multiple IPs, first one is the original client
        return x_forwarded_for.split(",")[0].strip()

    # Method 2: HTTP_X_FORWARDED_FOR (alternative header name)
    x_forwarded_for_alt = request.headers.get("HTTP_X_FORWARDED_FOR")
    if x_forwarded_for_alt:
        return x_forwarded_for_alt.split(",")[0].strip()

    # Method 3: X-Real-IP header (used by some proxies)
    x_real_ip = request.headers.get("X-Real-IP")
    if x_real_ip:
        return x_real_ip

    # Method 4: X-Appengine-User-Ip (for Google App Engine)
    x_appengine_user_ip = request.headers.get("X-Appengine-User-Ip")
    if x_appengine_user_ip:
        return x_appengine_user_ip

    # Method 5: Forwarded header (RFC 7239 standard)
    forwarded = request.headers.get("Forwarded")
    if forwarded:
        import re

        match = re.search(r'for="?([^";,]+)"?', forwarded)
        if match:
            return match.group(1)

    # Fallback to request.client.host
    return request.client.host


def _get_ip_detection_method(request: Request, detected_ip: str) -> str:
    if request.headers.get("X-Forwarded-For"):
        return "X-Forwarded-For"
    elif request.headers.get("HTTP_X_FORWARDED_FOR"):
        return "HTTP_X_FORWARDED_FOR"
    elif request.headers.get("X-Real-IP"):
        return "X-Real-IP"
    elif request.headers.get("X-Appengine-User-Ip"):
        return "X-Appengine-User-Ip"
    elif request.headers.get("Forwarded"):
        return "Forwarded"
    elif detected_ip in ["8.8.8.8"]:
        return "localhost_override"
    else:
        return "request.client.host"


def get_country_for_request(request: Request) -> Tuple[str, str, str]:
    """
    Returns a tuple of (country_code, ip_address, detection_method).
    Falls back to ("GE", ip, method) in case of errors or missing IP data.
    """
    ip = _detect_client_ip(request)

    # For local development override
    if ip in ["127.0.0.1", "::1", None]:
        ip = "8.8.8.8"

    try:
        response = _geoip_reader.country(ip)
        country_code = response.country.iso_code or "GE"
    except Exception:
        country_code = "GE"

    return country_code, ip, _get_ip_detection_method(request, ip)
