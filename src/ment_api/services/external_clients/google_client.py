import base64
import datetime
import json

from google.cloud import tasks_v2
from google.protobuf import timestamp_pb2
from loguru import logger

from ment_api.configurations.config import settings


class TaskCreator:
    def __init__(self, project, queue, location, url):
        self.client = tasks_v2.CloudTasksClient()
        self.parent = self.client.queue_path(project, location, queue)
        self.url = url

    # INSERT_YOUR_CODE
    def delete_task(self, task_name):
        """
        Deletes a task from the queue.

        Args:
            task_name (str): The name of the task to delete.
        """
        try:
            task_path = self.client.task_path(
                settings.gcp_project_id,
                self.parent.split("/")[3],
                self.parent.split("/")[5],
                task_name,
            )
            self.client.delete_task(request={"name": task_path})
            logger.info(f"Task {task_name} deleted successfully.")
        except Exception as e:
            logger.error(f"Failed to delete task {task_name}: {e}")

    def create_task(
        self,
        in_seconds,
        path,
        payload=None,
        basicToken=None,
        basicAuth=None,
        method="POST",
        url=None,
        task_name=None,
    ):
        # Construct the request body.
        http_method = tasks_v2.HttpMethod.POST

        if method == "PATCH":
            http_method = tasks_v2.HttpMethod.PATCH

        service_url = self.url

        if url:
            service_url = url

        if path:
            service_url += path

        task = {
            "http_request": {  # Specify the type of request.
                "http_method": http_method,
                # The full url path that the task will be sent to.
                "url": service_url,
            }
        }

        # specify http content-type to application/json
        task["http_request"]["headers"] = {"Content-type": "application/json"}
        task["http_request"]["headers"] = {"x-referrer": "recorder"}
        task["http_request"]["headers"] = {"x-api-key": settings.api_secret_key}
        if basicToken:
            task["http_request"]["headers"]["Authorization"] = f"Basic {basicToken}"
        elif basicAuth:
            # Add Basic Auth string to headers
            # encode to base64
            usrPass = f"{basicAuth['username']}:{basicAuth['password']}"
            b64Val = base64.b64encode(usrPass.encode())
            ecoded_str = b64Val.decode("utf-8")

            task["http_request"]["headers"]["Authorization"] = f"Basic {ecoded_str}"

        else:
            pass

        if payload:
            # Convert dict to JSON string
            payload_str = json.dumps(payload)
            # The API expects a payload of type bytes.
            converted_payload = payload_str.encode()

            # Add the payload to the request.
            task["http_request"]["body"] = converted_payload

        if in_seconds is not None:
            # Convert "seconds from now" into an rfc3339 datetime string.
            d = datetime.datetime.utcnow() + datetime.timedelta(seconds=in_seconds)

            # Create Timestamp protobuf.
            timestamp = timestamp_pb2.Timestamp()
            timestamp.FromDatetime(d)

            # Add the timestamp to the tasks.
            task["schedule_time"] = timestamp

        if task_name:
            task["name"] = self.client.task_path(
                settings.gcp_project_id,
                self.parent.split("/")[3],
                self.parent.split("/")[5],
                task_name,
            )

        # Use the client to build and send the task.
        self.client.create_task(request={"parent": self.parent, "task": task})
        logger.debug(f"[create_task]: for {path}")


task_expire = TaskCreator(
    project=settings.gcp_project_id,
    queue="automatic-tasks-expirer",
    location="us-west1",
    url=settings.api_url,
)
