from __future__ import annotations

import asyncio
import importlib

from app.main import app, lifespan


def test_lifespan_recovers_workflows_without_runtime_schema_creation(
    monkeypatch,
) -> None:
    calls: list[str] = []

    async def recover() -> None:
        calls.append("recover")

    async def shutdown() -> None:
        calls.append("shutdown")

    main_module = importlib.import_module("app.main")
    recovery_service = type(
        "RecoveryService",
        (),
        {
            "recover_pending_workflows": staticmethod(recover),
            "shutdown": staticmethod(shutdown),
        },
    )()
    monkeypatch.setattr(main_module, "industrial_design_workflow_service", recovery_service, raising=False)
    async def run_lifespan() -> None:
        async with lifespan(app):
            calls.append("yield")

    asyncio.run(run_lifespan())

    assert calls == ["recover", "yield", "shutdown"]
