import logging

import google.cloud.logging

from ment_api.configurations.config import settings


def setup_logging():
    if settings.env == "prod":
        client = google.cloud.logging.Client()
        client.setup_logging(log_level=logging.INFO)
    else:
        logging.basicConfig(level=logging.INFO)
