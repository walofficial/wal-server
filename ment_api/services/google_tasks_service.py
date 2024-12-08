import json
from typing import Dict, Optional

from google.cloud import tasks_v2

from ment_api.config import settings

client = tasks_v2.CloudTasksClient()


def create_http_task(
        url: str,
        json_payload: Dict
) -> tasks_v2.Task:
    # Construct the task.
    task = tasks_v2.Task(
        http_request=tasks_v2.HttpRequest(
            http_method=tasks_v2.HttpMethod.POST,
            url=url,
            headers={"Content-type": "application/json"},
            body=json.dumps(json_payload).encode(),
        ))

    return client.create_task(
        tasks_v2.CreateTaskRequest(
            parent=client.queue_path(settings.gcp_project_id, settings.gcp_tasks_location, settings.gcp_tasks_verification_queue),
            task=task,
        )
    )
