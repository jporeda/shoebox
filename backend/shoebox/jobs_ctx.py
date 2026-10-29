"""Progress/cancel handle passed to job handlers (kept separate to avoid import cycles)."""

from __future__ import annotations

from typing import Callable


class JobCancelled(Exception):
    pass


class JobContext:
    def __init__(
        self,
        job_id: int | None = None,
        report: Callable[[int, int, str], None] | None = None,
        is_cancelled: Callable[[], bool] | None = None,
    ):
        self.job_id = job_id
        self._report = report
        self._is_cancelled = is_cancelled

    def progress(self, done: int, total: int, message: str = "") -> None:
        if self._report:
            self._report(done, total, message)

    def check_cancel(self) -> None:
        if self._is_cancelled and self._is_cancelled():
            raise JobCancelled()


NULL_CTX = JobContext()
