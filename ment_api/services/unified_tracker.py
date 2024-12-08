from collections import defaultdict
from datetime import datetime, timezone
import time
from typing import Dict, List


class UnifiedTracker:
    def __init__(self):
        self.requests: Dict[str, Dict] = {}
        self.active_tasks: Dict[str, Dict] = {}
        self.completed_tasks: Dict[str, Dict] = defaultdict(list)
        self.function_metrics: Dict[str, Dict] = defaultdict(
            lambda: {
                "count": 0,
                "total_time": 0,
                "max_time": 0,
                "min_time": float("inf"),
            }
        )

    def start_request(self, request_id: str, path: str, method: str) -> None:
        self.requests[request_id] = {
            "start_time": time.perf_counter(),
            "path": path,
            "method": method,
            "functions": [],
            "background_tasks": [],
            "status": "running",
        }

    def complete_request(self, request_id: str, status_code: int) -> float:
        if request_id in self.requests:
            req_info = self.requests[request_id]
            execution_time = time.perf_counter() - req_info["start_time"]

            req_info.update(
                {
                    "execution_time": execution_time,
                    "completion_time": datetime.now(timezone.utc),
                    "status": "completed",
                    "status_code": status_code,
                }
            )

            # Update metrics for this endpoint
            path_key = f"{req_info['method']}:{req_info['path']}"
            metrics = self.function_metrics[path_key]
            metrics["count"] += 1
            metrics["total_time"] += execution_time
            metrics["max_time"] = max(metrics["max_time"], execution_time)
            metrics["min_time"] = min(metrics["min_time"], execution_time)

            return execution_time
        return 0.0

    def start_background_task(
        self, task_id: str, request_id: str, task_name: str
    ) -> None:
        self.active_tasks[task_id] = {
            "start_time": time.perf_counter(),
            "request_id": request_id,
            "task_name": task_name,
            "status": "running",
        }

        if request_id in self.requests:
            self.requests[request_id]["background_tasks"].append(task_id)

    def complete_background_task(self, task_id: str, success: bool = True) -> None:
        if task_id in self.active_tasks:
            task_info = self.active_tasks[task_id]
            execution_time = time.perf_counter() - task_info["start_time"]

            task_info.update(
                {
                    "execution_time": execution_time,
                    "completion_time": datetime.now(timezone.utc),
                    "status": "completed" if success else "failed",
                }
            )

            request_id = task_info["request_id"]
            if request_id in self.requests:
                self.completed_tasks[request_id].append(task_info)

            self.active_tasks.pop(task_id)

    def record_function_execution(
        self, request_id: str, function_name: str, execution_time: float
    ) -> None:
        if request_id in self.requests:
            self.requests[request_id]["functions"].append(
                {
                    "name": function_name,
                    "execution_time": execution_time,
                    "timestamp": datetime.now(timezone.utc),
                }
            )

    def get_request_info(self, request_id: str) -> Dict:
        if request_id not in self.requests:
            return {}

        info = self.requests[request_id].copy()
        info["active_background_tasks"] = [
            task
            for task in self.active_tasks.values()
            if task["request_id"] == request_id
        ]
        info["completed_background_tasks"] = self.completed_tasks[request_id]
        return info

    def get_request_info_list(self) -> List[Dict]:
        return self.requests

    def get_metrics(self) -> Dict:
        metrics = {}
        for path_key, data in self.function_metrics.items():
            if data["count"] > 0:
                metrics[path_key] = {
                    **data,
                    "avg_time": data["total_time"] / data["count"],
                }
        return metrics


tracker = UnifiedTracker()
