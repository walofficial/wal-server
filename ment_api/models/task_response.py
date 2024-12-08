from typing import List, Optional
from pydantic import BaseModel
from ment_api.models.task import Task
from ment_api.models.task_location_mapping import Location


class TaskWithLocation(BaseModel):
    task: Task
    nearest_location: Optional[Location] = None


class DailyTasksResponse(BaseModel):
    tasks_at_location: List[Task]
    nearest_tasks: List[TaskWithLocation]
