import asyncio
import json
import time
from uuid import uuid4

from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse

from .models import CreateTaskRequest, TaskRecord
from .models import TaskStatus
from .config import load_backend_env
from .llm import create_llm_client
from .runner import TaskRunner
from .storage import TaskStore


load_backend_env()


def get_task_store() -> TaskStore:
    return TaskStore()


def create_app() -> FastAPI:
    app = FastAPI(title="MiniMax Agent Workflow")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            "http://localhost:3000",
            "http://127.0.0.1:3000",
        ],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.post("/tasks", response_model=TaskRecord, status_code=201)
    def create_task(
        request: CreateTaskRequest,
        background_tasks: BackgroundTasks,
        store: TaskStore = Depends(get_task_store),
    ) -> TaskRecord:
        goal = request.goal.strip()
        if not goal:
            raise HTTPException(status_code=422, detail="Task goal cannot be empty.")
        task = store.create_task(str(uuid4()), goal)
        background_tasks.add_task(run_planner_background, task.id)
        return task

    @app.get("/tasks/{task_id}", response_model=TaskRecord)
    def get_task(
        task_id: str,
        store: TaskStore = Depends(get_task_store),
    ) -> TaskRecord:
        task = store.get_task(task_id)
        if task is None:
            raise HTTPException(status_code=404, detail="Task not found.")
        return task

    @app.post("/tasks/{task_id}/cancel", response_model=TaskRecord)
    def cancel_task(
        task_id: str,
        store: TaskStore = Depends(get_task_store),
    ) -> TaskRecord:
        task = store.request_cancel(task_id)
        if task is None:
            raise HTTPException(status_code=404, detail="Task not found.")
        if task.status != TaskStatus.CANCEL_REQUESTED:
            raise HTTPException(status_code=409, detail="Task cannot be cancelled.")
        return task

    @app.get("/tasks/{task_id}/events")
    def stream_task_events(
        task_id: str,
        store: TaskStore = Depends(get_task_store),
    ) -> StreamingResponse:
        if store.get_task(task_id) is None:
            raise HTTPException(status_code=404, detail="Task not found.")

        def event_stream():
            last_seq = 0
            terminal_events = {"task.completed", "task.failed", "task.cancelled"}
            while True:
                events = [
                    event
                    for event in store.list_events(task_id)
                    if event.seq > last_seq
                ]
                for event in events:
                    last_seq = event.seq
                    payload = json.dumps(
                        {
                            "seq": event.seq,
                            "type": event.type,
                            "payload": event.payload,
                            "created_at": event.created_at.isoformat(),
                        }
                    )
                    yield f"id: {event.seq}\n"
                    yield f"event: {event.type}\n"
                    yield f"data: {payload}\n\n"
                    if event.type in terminal_events:
                        return
                time.sleep(0.1)

        return StreamingResponse(event_stream(), media_type="text/event-stream")

    return app


def run_planner_background(task_id: str) -> None:
    runner = TaskRunner(TaskStore(), create_llm_client())
    asyncio.run(runner.run_planner_task(task_id))


app = create_app()
