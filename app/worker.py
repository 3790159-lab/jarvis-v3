from __future__ import annotations

import time
from typing import NoReturn

from app.router import get_executor
from app.settings import settings
from app.state_store import StateStore


def process_once(store: StateStore) -> bool:
    task = store.acquire_next_queued_task()
    if not task:
        return False

    try:
        executor = get_executor(task["executor"])
        execution = executor.run(task)
        store.complete_task(
            task_id=task["task_id"],
            result=execution.result,
            error=execution.error if not execution.success else None,
        )
    except Exception as exc:  # noqa: BLE001
        store.complete_task(
            task_id=task["task_id"],
            result={"unhandled_exception": True},
            error=str(exc),
        )
    return True


def run_forever() -> NoReturn:
    store = StateStore()
    print("Worker started.")
    print(f"DB: {store.db_path}")
    print(f"Poll interval: {settings.worker_poll_interval_seconds}s")

    while True:
        processed = process_once(store)
        if not processed:
            time.sleep(settings.worker_poll_interval_seconds)


if __name__ == "__main__":
    run_forever()
