from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from threading import BoundedSemaphore, Lock
from typing import Callable


class QueueCapacityError(RuntimeError):
    """Raised when the in-process job queue has reached its configured limit."""


class JobCoordinator:
    """Run a bounded number of DocFlow jobs without blocking the HTTP request."""

    def __init__(self, max_workers: int = 2, max_pending: int = 20):
        if max_workers < 1 or max_pending < max_workers:
            raise ValueError("max_pending must be greater than or equal to max_workers")
        self.max_workers = max_workers
        self.max_pending = max_pending
        self._executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="docflow-job")
        self._capacity = BoundedSemaphore(max_pending)
        self._lock = Lock()
        self._jobs: dict[int, dict] = {}

    def submit(self, task_id: int, callback: Callable[[], object]) -> None:
        if not self._capacity.acquire(blocking=False):
            raise QueueCapacityError("DocFlow 任务队列已满，请稍后重试。")
        with self._lock:
            self._jobs[task_id] = {"state": "queued", "future": None}

        def run() -> None:
            with self._lock:
                if task_id in self._jobs:
                    self._jobs[task_id]["state"] = "running"
            try:
                callback()
            except Exception:
                # The runtime persists the concrete failure on the task/run record.
                pass
            finally:
                with self._lock:
                    self._jobs.pop(task_id, None)
                self._capacity.release()

        try:
            future = self._executor.submit(run)
        except Exception:
            with self._lock:
                self._jobs.pop(task_id, None)
            self._capacity.release()
            raise
        with self._lock:
            if task_id in self._jobs:
                self._jobs[task_id]["future"] = future

    def snapshot(self, task_id: int | None = None) -> dict:
        with self._lock:
            states = [job["state"] for job in self._jobs.values()]
            task_state = self._jobs.get(task_id, {}).get("state") if task_id is not None else None
        running = states.count("running")
        queued = states.count("queued")
        return {
            "task_state": task_state,
            "running": running,
            "queued": queued,
            "active": running + queued,
            "max_workers": self.max_workers,
            "max_pending": self.max_pending,
            "available": max(0, self.max_pending - running - queued),
        }

    def shutdown(self, wait: bool = True) -> None:
        self._executor.shutdown(wait=wait, cancel_futures=False)
