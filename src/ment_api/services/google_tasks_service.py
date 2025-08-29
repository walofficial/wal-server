import json
from typing import Dict, Optional
from datetime import datetime
from google.cloud import tasks_v2
from google.api_core import client_options as client_options_lib
from google.auth import credentials as ga_credentials
from ment_api.configurations.config import settings


client: tasks_v2.CloudTasksClient

if settings.env != "local":
    client = tasks_v2.CloudTasksClient()
else:
    client = tasks_v2.CloudTasksClient(
        client_options=client_options_lib.ClientOptions(
            api_endpoint=settings.mock_gcp_tasks_endpoint,
        ),
        credentials=type(
            "MockCredentials",
            (ga_credentials.Credentials,),
            {"refresh": lambda self, _: setattr(self, "token", "mock-token")},
        )(),
    )


def create_http_task(
    url: str,
    json_payload: Dict,
    schedule_time: Optional[datetime] = None,
    queue_name: Optional[str] = None,
) -> tasks_v2.Task:
    """Create an HTTP task for the specified queue"""
    # Use default verification queue if no queue specified
    if queue_name is None:
        queue_name = settings.gcp_tasks_verification_queue

    # Construct the task.
    task = tasks_v2.Task(
        http_request=tasks_v2.HttpRequest(
            http_method=tasks_v2.HttpMethod.POST,
            url=url,
            headers={
                "Content-type": "application/json",
                "x-api-key": settings.api_secret_key,
            },
            body=json.dumps(json_payload).encode(),
        )
    )

    if schedule_time:
        task.schedule_time = schedule_time

    return client.create_task(
        tasks_v2.CreateTaskRequest(
            parent=client.queue_path(
                settings.gcp_project_id,
                settings.gcp_tasks_location,
                queue_name,
            ),
            task=task,
        )
    )


def create_notification_task(
    url: str, json_payload: Dict, schedule_time: Optional[datetime] = None
) -> tasks_v2.Task:
    """Create a notification task for the notification queue"""
    return create_http_task(
        url=url,
        json_payload=json_payload,
        schedule_time=schedule_time,
        queue_name=settings.gcp_tasks_notification_queue,
    )
