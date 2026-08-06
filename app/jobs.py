import asyncio
import json
import uuid
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Awaitable, Callable

KEEPALIVE_INTERVAL = 15  # 秒


@dataclass
class Job:
    id: str
    created_by: str
    status: str = "pending"  # pending / running / done / error
    events: list[dict[str, Any]] = field(default_factory=list)
    subscribers: list[asyncio.Queue] = field(default_factory=list)

    def _broadcast(self, event: dict[str, Any]) -> None:
        self.events.append(event)
        for queue in self.subscribers:
            queue.put_nowait(event)

    def emit_progress(self, stage: str, label: str, **extra: Any) -> None:
        self._broadcast({"type": "progress", "stage": stage, "label": label, **extra})

    def emit_warning(self, stage: str, message: str) -> None:
        self._broadcast({"type": "warning", "stage": stage, "message": message})

    def emit_done(self, result: dict[str, Any]) -> None:
        self.status = "done"
        self._broadcast({"type": "done", "result": result})

    def emit_error(self, message: str) -> None:
        self.status = "error"
        self._broadcast({"type": "error", "message": message})

    def subscribe(self) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue()
        for event in self.events:
            queue.put_nowait(event)
        self.subscribers.append(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue) -> None:
        if queue in self.subscribers:
            self.subscribers.remove(queue)


class JobManager:
    def __init__(self) -> None:
        self._jobs: dict[str, Job] = {}

    def create_job(
        self,
        created_by: str,
        runner: Callable[[Job], Awaitable[None]],
    ) -> Job:
        job = Job(id=str(uuid.uuid4()), created_by=created_by, status="running")
        self._jobs[job.id] = job
        asyncio.create_task(self._run(job, runner))
        return job

    async def _run(self, job: Job, runner: Callable[[Job], Awaitable[None]]) -> None:
        try:
            await runner(job)
        except Exception as exc:  # パイプライン側の想定外の例外の最終防波堤
            job.emit_error(str(exc))

    def get(self, job_id: str) -> Job | None:
        return self._jobs.get(job_id)


job_manager = JobManager()


async def sse_stream(job: Job) -> AsyncIterator[str]:
    queue = job.subscribe()
    try:
        while True:
            try:
                event = await asyncio.wait_for(queue.get(), timeout=KEEPALIVE_INTERVAL)
            except asyncio.TimeoutError:
                yield ": keep-alive\n\n"
                continue

            yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
            if event["type"] in ("done", "error"):
                break
    finally:
        job.unsubscribe(queue)
