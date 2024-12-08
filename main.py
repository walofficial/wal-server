import logging
import time
from typing import Callable

import socketio
from fastapi import Request, FastAPI
from starlette.middleware.cors import CORSMiddleware
from starlette.responses import JSONResponse
from ment_api.health_check_config import setup_health_checks
from ment_api.logging_config import setup_logging
from starlette.middleware.base import BaseHTTPMiddleware

setup_logging()

from ment_api.lifespan import lifespan

from ment_api.routes import (
    generate_interests,
    upload_photos,
    verify_photos,
    user,
    feed,
    tasks,
    verify_videos,
    verification_example_media,
    friend_routes,
    monitoring,
    generate_user_georgian,
    notifications,
    live_user_actions,
)
from ment_api.routes import pinned_verifications
from ment_api.config import settings
from ment_api.routes.chat import sio, router as chat_router

app = FastAPI(lifespan=lifespan)


class APIKeyMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        # Skip API key validation for health check endpoint
        if request.url.path == "/health":
            return await call_next(request)

        x_api_key = request.headers.get("x-api-key") or "unknown"
        if x_api_key not in settings.API_SECRET_KEY:
            return JSONResponse(
                {"detail": "Invalid or missing x-api-key"},
                status_code=401,
            )
        return await call_next(request)


# Add the middleware to the app
app.add_middleware(APIKeyMiddleware)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(generate_interests.router)
app.include_router(tasks.router)
app.include_router(upload_photos.router)
app.include_router(verify_photos.router)
app.include_router(verify_videos.router)
app.include_router(user.router)
app.include_router(feed.router)
app.include_router(verification_example_media.router)
app.include_router(friend_routes.router)
app.include_router(chat_router)
app.include_router(monitoring.router)
app.include_router(generate_user_georgian.router)
app.include_router(notifications.router)
app.include_router(live_user_actions.router)
app.include_router(pinned_verifications.router)

setup_health_checks(app)


@app.middleware("http")
async def log_request_middleware(request: Request, call_next: Callable):
    paths_to_log = ["/verify-videos"]

    if any(request.url.path.startswith(path) for path in paths_to_log):
        start_time = time.time()

        response = await call_next(request)

        process_time = time.time() - start_time
        logging.info(
            f"Response status: {response.status_code} - Processed in {process_time:.4f} seconds"
        )

        return response
    else:
        return await call_next(request)


app = socketio.ASGIApp(sio, app)
