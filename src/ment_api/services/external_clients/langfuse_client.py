import logging
import os
from typing import Any
from unittest.mock import MagicMock

from langfuse import Langfuse
from ment_api.configurations.config import settings

logger = logging.getLogger(__name__)


class DisabledLangfuse:
    """No-op Langfuse client that doesn't export any traces."""

    def __getattr__(self, name: str) -> Any:
        """Return a no-op function for any method call."""
        return MagicMock()


# Check if Langfuse tracing is enabled (only in production by default)
langfuse_enabled = os.getenv("LANGFUSE_TRACING_ENABLED", "false").lower() == "true"

if langfuse_enabled:
    langfuse = Langfuse(
        host=settings.langfuse_host,
        public_key=settings.langfuse_public_key,
        secret_key=settings.langfuse_secret_key,
        environment=settings.langfuse_tracing_environment,
    )
    logger.info("Langfuse tracing is ENABLED")
else:
    langfuse = DisabledLangfuse()  # type: ignore
    logger.info("Langfuse tracing is DISABLED (development mode)")
