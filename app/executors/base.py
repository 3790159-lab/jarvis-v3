from __future__ import annotations

from abc import ABC, abstractmethod

from app.models import TaskExecutionResult


class BaseExecutor(ABC):
    @abstractmethod
    def run(self, task: dict) -> TaskExecutionResult:
        raise NotImplementedError
