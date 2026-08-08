"""任务调度 Dispatcher 接口占位。

第一阶段不实现真实调度：现有 DB WorkflowTask + asyncio.create_task + lease/heartbeat
仍是执行路径。本文件只为未来 Redis / GPU Scheduler 留接口边界。

Agent 只声明 capability 与资源提示，不感知具体机器（910A / 5090 IP 等）。
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class TaskResourceHint:
    capability: str
    gpu: bool = False
    preferred_pool: str | None = None


@dataclass
class TaskSubmitResult:
    task_id: str
    accepted: bool = True
    notes: list[str] = field(default_factory=list)


class TaskDispatcher:
    """未来真实调度器的接口。第一阶段不接入，方法保持占位。"""

    async def submit(
        self,
        *,
        task_type: str,
        payload: dict[str, object],
        resource_hint: TaskResourceHint | None = None,
    ) -> TaskSubmitResult:
        raise NotImplementedError


task_dispatcher = TaskDispatcher()