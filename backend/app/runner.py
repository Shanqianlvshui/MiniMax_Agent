from .llm import LLMClient
from .models import TaskStatus
from .storage import TaskStore


class TaskRunner:
    def __init__(self, store: TaskStore, llm: LLMClient):
        self.store = store
        self.llm = llm

    async def run_planner_task(self, task_id: str) -> None:
        task = self.store.get_task(task_id)
        if task is None:
            return

        self.store.append_event(task_id, "task.started", {"status": task.status.value})
        self.store.set_current_agent(task_id, "planner")
        self.store.append_event(task_id, "agent.started", {"agent": "planner"})

        output = []
        try:
            async for token in self.llm.stream_planner(task.goal):
                output.append(token)
                self.store.append_event(
                    task_id,
                    "agent.token",
                    {"agent": "planner", "token": token},
                )
            planner_output = "".join(output)
            self.store.append_event(
                task_id,
                "agent.completed",
                {
                    "agent": "planner",
                    "summary": planner_output,
                },
            )
            self.store.complete_task(task_id)
            self.store.append_event(task_id, "task.completed", {"status": "completed"})
        except Exception as exc:
            self.store.fail_task(task_id, str(exc))
            self.store.append_event(
                task_id,
                "agent.failed",
                {"agent": "planner", "error": str(exc)},
            )
            self.store.append_event(task_id, "task.failed", {"error": str(exc)})
        finally:
            latest = self.store.get_task(task_id)
            if latest and latest.status in {
                TaskStatus.COMPLETED,
                TaskStatus.FAILED,
                TaskStatus.CANCEL_REQUESTED,
            }:
                self.store.set_current_agent(task_id, None)
