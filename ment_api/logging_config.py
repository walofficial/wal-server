import logging

import google.cloud.logging

from ment_api.config import settings


def setup_logging():
    if settings.env == "prod1":
        client = google.cloud.logging.Client()
        client.setup_logging(log_level=logging.INFO)
    else:
        logging.basicConfig(level=logging.INFO)
