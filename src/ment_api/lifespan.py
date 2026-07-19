import asyncio
import aiohttp
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from livekit.api.agent_dispatch_service import AgentDispatchService
from ment_api.services.external_clients.langfuse_client import langfuse

from ment_api.configurations.config import settings
from ment_api.persistence.mongo import initialize_db
from ment_api.persistence.mongo_client import (
    close_mongo_client,
    initialize_mongo_client,
)
from ment_api.services.pub_sub_service import SubscriberSupervisor
from ment_api.services.redis_service import get_redis_service
from ment_api.workers.message_state_worker import (
    cleanup_message_state_task,
    init_message_state_task,
)
from ment_api.workers.subscriber_registry import build_subscriber_specs

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(local_app: FastAPI):
    await asyncio.gather(
        initialize_mongo_client(),
        initialize_db(),
    )

    # Initialize LiveKit clients within the event loop context
    session = aiohttp.ClientSession(connector=aiohttp.TCPConnector(ssl=False))
    try:
        from livekit.api.room_service import RoomService
        from livekit.api.ingress_service import IngressService
        from ment_api.configurations.config import settings as _settings

        local_app.state.livekit_session = session
        local_app.state.livekit_room_service = RoomService(
            session=session,
            url=_settings.livekit_url,
            api_key=_settings.livekit_api_key,
            api_secret=_settings.livekit_api_secret,
        )
        local_app.state.livekit_agent_dispatch_service = AgentDispatchService(
            session=session,
            url=_settings.livekit_url,
            api_key=_settings.livekit_api_key,
            api_secret=_settings.livekit_api_secret,
        )
        local_app.state.livekit_ingress_service = IngressService(
            session=session,
            url=_settings.livekit_url,
            api_key=_settings.livekit_api_key,
            api_secret=_settings.livekit_api_secret,
        )
    except Exception:
        # If LiveKit init fails, ensure session is closed and re-raise
        await session.close()
        raise

    message_state_task = init_message_state_task()

    # Start Pub/Sub subscribers via the supervisor. Each subscription runs with a
    # dedicated bounded scheduler, so enabling all of them cannot starve the app.
    supervisor = SubscriberSupervisor(
        settings.gcp_project_id, asyncio.get_running_loop()
    )
    local_app.state.pubsub_supervisor = supervisor

    specs = [spec for spec in build_subscriber_specs() if spec.enabled]
    # A single subscriber failing to start (e.g. missing IAM in an environment
    # whose service account lacks pubsub permissions) must not crash the whole
    # app; isolate failures so the remaining subscribers still come up.
    start_results = await asyncio.gather(
        *(supervisor.start(spec) for spec in specs),
        return_exceptions=True,
    )
    started = [
        spec.name
        for spec, result in zip(specs, start_results)
        if not isinstance(result, BaseException)
    ]
    for spec, result in zip(specs, start_results):
        if isinstance(result, BaseException):
            logger.error(
                "Pub/Sub subscriber failed to start; continuing without it",
                extra={
                    "json_fields": {
                        "operation": "pubsub_startup_error",
                        "subscriber": spec.name,
                        "subscription_id": spec.subscription_id,
                        "error": str(result),
                        "error_type": type(result).__name__,
                    },
                    "labels": {"component": "pubsub", "severity": "high"},
                },
            )

    logger.info(
        "Pub/Sub subscribers started",
        extra={
            "json_fields": {
                "operation": "pubsub_startup",
                "subscriber_count": len(started),
                "subscribers": started,
            },
            "labels": {"component": "pubsub"},
        },
    )

    yield

    # Shutdown code
    logger.info("Shutting down application")
    redis_service = get_redis_service()

    # Cancel + drain subscribers first, then tear down the rest.
    await supervisor.stop()

    await asyncio.gather(
        cleanup_message_state_task(message_state_task),
        close_mongo_client(),
        redis_service.aclose(),
        session.close(),
        return_exceptions=True,
    )

    langfuse.shutdown()
    logger.info("Application shutdown complete")
