from uuid import uuid4

from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from .models import CreateTaskRequest, TaskRecord
from .storage import TaskStore


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
        store: TaskStore = Depends(get_task_store),
    ) -> TaskRecord:
        goal = request.goal.strip()
        if not goal:
            raise HTTPException(status_code=422, detail="Task goal cannot be empty.")
        return store.create_task(str(uuid4()), goal)

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
        return task

    return app


app = create_app()
