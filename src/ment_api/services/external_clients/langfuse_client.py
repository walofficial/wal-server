import logging

from langfuse import Langfuse
from ment_api.configurations.config import settings

logger = logging.getLogger(__name__)
langfuse = Langfuse(
    host=settings.langfuse_host,
    public_key=settings.langfuse_public_key,
    secret_key=settings.langfuse_secret_key,
    environment=settings.langfuse_tracing_environment,
)
