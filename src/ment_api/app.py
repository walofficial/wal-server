import logging

import jwt
import socketio
from fastapi import FastAPI, Request
from pydantic import BaseModel
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.middleware.cors import CORSMiddleware
from starlette.responses import JSONResponse

from ment_api.configurations.config import settings
from ment_api.configurations.health_check_config import setup_health_checks
from ment_api.lifespan import lifespan
from ment_api.routes import (
    comments,
    feed,
    feeds,
    friend_routes,
    live_user_actions,
    livekit,
    notifications,
    reactions,
    space,
    upload_user_photos,
    user,
    verify_photos,
    verify_videos,
)
from ment_api.routes.chat import router as chat_router
from ment_api.routes.chat import sio
from ment_api.services.country_service import get_country_for_request

print("Initializing fastapi")
app = FastAPI(lifespan=lifespan)

# GeoIP2 reader is initialized in country_service


class SupabaseAuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        # Allow unauthenticated GET access to specific public endpoints
        path = request.url.path
        method = request.method.upper()

        auth_header = request.headers.get("Authorization")
        request.state.supabase_user_id = request.headers.get("x-user-id") or "unknown"
        request.state.is_guest = False

        # Check for anonymous header with value "true"
        x_anonymous_header = request.headers.get("x-anonymous", "").lower()
        request.state.is_anonymous = x_anonymous_header == "true"

        # Public endpoints allowlist (GET only)
        is_public_get = method == "GET" and (
            path == "/user/get-verification"
            or path.startswith("/user/feed/location-feed/")
            or path.startswith("/live-actions/verification-likes/")
            or path.startswith("/live-actions/get-impressions/")
            or path.startswith("/get-country")
            or path.startswith("/health")
        )

        if is_public_get and (not auth_header or not auth_header.startswith("Bearer ")):
            # Treat as guest for public endpoints
            request.state.is_guest = True
            request.state.supabase_user_id = None
            request.state.is_anonymous = True
            return await call_next(request)

        if not auth_header or not auth_header.startswith("Bearer "):
            request.state.is_guest = (
                "x-is-anonymous" in request.headers or request.state.is_anonymous
            )

            # Try API key auth as fallback
            x_api_key = request.headers.get("x-api-key") or "unknown"
            # Support comma-separated list of API keys in settings.api_secret_key
            allowed_api_keys = [
                k.strip()
                for k in (settings.api_secret_key or "").split(",")
                if k.strip()
            ]
            if x_api_key not in allowed_api_keys:
                return JSONResponse(
                    {"detail": "Invalid or missing authorization token"},
                    status_code=401,
                )
            return await call_next(request)

        try:
            token = auth_header.split(" ")[1]
            decoded_token = jwt.decode(
                token,
                settings.supabase_jwt_secret,
                algorithms=["HS256"],
                audience="authenticated",
            )
            # Add the user ID to the request state
            request.state.supabase_user_id = decoded_token["sub"]
        except jwt.InvalidTokenError:
            # Try API key auth as fallback
            x_api_key = request.headers.get("x-api-key") or "unknown"
            allowed_api_keys = [
                k.strip()
                for k in (settings.api_secret_key or "").split(",")
                if k.strip()
            ]
            if x_api_key not in allowed_api_keys:
                return JSONResponse(
                    {"detail": "Invalid authentication token"},
                    status_code=401,
                )
            return await call_next(request)
        except Exception as e:
            logging.error(f"Auth error: {str(e)[:100]}")
            return JSONResponse(
                {"detail": "Authentication error"},
                status_code=401,
            )

        return await call_next(request)


# Add the middleware to the app
app.add_middleware(SupabaseAuthMiddleware)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(feeds.router)
app.include_router(upload_user_photos.router)
app.include_router(verify_photos.router)
app.include_router(verify_videos.router)
app.include_router(livekit.router)
app.include_router(user.router)
app.include_router(feed.router)
app.include_router(friend_routes.router)
app.include_router(chat_router)
app.include_router(notifications.router)
app.include_router(live_user_actions.router)
app.include_router(space.router)
app.include_router(comments.router)
app.include_router(reactions.router)


class GetCountryResponse(BaseModel):
    country_code: str
    ip_address: str
    detection_method: str


@app.get("/get-country", operation_id="get_country", response_model=GetCountryResponse)
async def get_country(request: Request):
    country_code, ip, method = get_country_for_request(request)
    return GetCountryResponse(
        country_code=country_code,
        ip_address=ip,
        detection_method=method,
    )


def get_ip_detection_method(request: Request, detected_ip: str) -> str:
    # Backward-compat shim: delegate to country_service for consistent naming
    _, _, method = get_country_for_request(request)
    return method


setup_health_checks(app)

app = socketio.ASGIApp(sio, app)
