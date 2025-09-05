import logging
import sys

import google.cloud.logging
import structlog

from ment_api.configurations.config import settings


def setup_logging():
    if settings.env == "prod":
        client = google.cloud.logging.Client(project=settings.gcp_project_id)
        client.setup_logging(log_level=logging.INFO)
    else:
        formatter = structlog.stdlib.ProcessorFormatter(
            foreign_pre_chain=[structlog.stdlib.ExtraAdder()],
            processors=[
                structlog.stdlib.ProcessorFormatter.remove_processors_meta,
                structlog.contextvars.merge_contextvars,
                structlog.processors.add_log_level,
                structlog.processors.StackInfoRenderer(),
                structlog.dev.set_exc_info,
                structlog.processors.TimeStamper(fmt="iso"),
                structlog.dev.ConsoleRenderer(colors=True, pad_level=False),
            ],
        )

        root_logger = logging.getLogger()
        root_logger.handlers.clear()

        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(formatter)

        root_logger.addHandler(handler)
        root_logger.setLevel(logging.INFO)
