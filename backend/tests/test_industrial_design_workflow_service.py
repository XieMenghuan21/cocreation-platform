from __future__ import annotations

import asyncio
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Coroutine

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.session import Base
from app.core.identity import AuthIdentityError
from app.models.persistence import WorkflowTask
from app.schemas.industrial_design import IndustrialDesignWorkflowRequest
from app.services.industrial_design_workflow_service import IndustrialDesignWorkflowService


class FakeAiModelGateway:
    def image_configured(self) -> bool:
        return True

    def local_wan_image_configured(self) -> bool:
        return True

    def dashscope_image_configured(self) -> bool:
        return False

    def gemini_image_configured(self) -> bool:
        return False

    def image_provider_label(self) -> str:
        return "57 本地 Wan"

    async def generate_design_image(self, **_: object) -> dict[str, object]:
        return {
            "taskId": "image-task-1",
            "model": "sd3",
            "resultUrl": "",
            "status": "completed",
        }

    async def generate_cad(self, *_: object, **__: object) -> object:
        raise AssertionError("design-only workflow should not invoke CAD generation")

    async def create_text_to_cad(self, *_: object, **__: object) -> object:
        raise AssertionError("design-only workflow should not invoke Zoo CAD generation")


class FakeDrawingService:
    def render_wardrobe_drawing(self, *_: object, **__: object) -> object:
        raise AssertionError("design-only workflow should not fall back to local drawing")


class FakeHistoryService:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []
        self.fail = False

    @contextmanager
    def transaction_lock(
        self,
        db: object,
        *,
        user_id: str,
        project_id: str,
    ) -> Iterator[None]:
        del db, user_id, project_id
        yield

    def upsert_project_with_version_in_transaction(
        self,
        db: object,
        *,
        auth_user: dict[str, object],
        project_payload: object,
        version_payload: object,
    ) -> dict[str, object]:
        if self.fail:
            raise RuntimeError("history write failed")
        self.calls.append(
            {
                "db": db,
                "auth_user": auth_user,
                "project_payload": project_payload,
                "version_payload": version_payload,
            }
        )
        return {"projectId": getattr(project_payload, "id", ""), "versionId": getattr(version_payload, "id", "")}


def test_no_output_workflow_does_not_require_media_pipeline() -> None:
    _, db_context_factory = _session_context_factory()
    service = IndustrialDesignWorkflowService(
        ai_model_gateway=FakeAiModelGateway(),
        drawing_service=FakeDrawingService(),
        db_context_factory=db_context_factory,
    )
    request = IndustrialDesignWorkflowRequest.model_validate(
        {
            "inputType": "text",
            "text": "仅保存需求",
            "options": {
                "generateCad": False,
                "generateDrawing": False,
                "generateThreePreview": False,
                "generateRender": False,
                "generateExplosion": False,
            },
        }
    )

    result = asyncio.run(service.create_workflow(request, {"sub": "alice"}))

    assert request.options.enhance_image is True
    assert result["status"] == "completed"
    assert result["outputs"] == {}


def test_three_preview_flag_does_not_invoke_real_3d_chain() -> None:
    _, db_context_factory = _session_context_factory()
    service = IndustrialDesignWorkflowService(
        ai_model_gateway=FakeAiModelGateway(),
        drawing_service=FakeDrawingService(),
        db_context_factory=db_context_factory,
    )
    request = IndustrialDesignWorkflowRequest.model_validate(
        {
            "inputType": "text",
            "text": "生成一个二维仿3D效果图",
            "options": {
                "generateCad": False,
                "generateDrawing": False,
                "generateThreePreview": True,
                "generateRender": False,
                "generateExplosion": False,
                "enhanceImage": False,
            },
        }
    )

    result = asyncio.run(service.create_workflow(request, {"sub": "alice"}))

    assert result["status"] == "completed"
    assert result["outputs"] == {}


def test_enhance_image_prompt_is_detailed_for_phone_stand_poster() -> None:
    _, db_context_factory = _session_context_factory()
    service = IndustrialDesignWorkflowService(
        ai_model_gateway=FakeAiModelGateway(),
        drawing_service=FakeDrawingService(),
        db_context_factory=db_context_factory,
    )
    request = IndustrialDesignWorkflowRequest.model_validate(
        {
            "inputType": "text",
            "text": "兔子手机支架宣传海报，手机放在支架上",
            "projectName": "兔子手机支架",
            "industry": "消费电子",
            "options": {
                "generateCad": False,
                "generateDrawing": False,
                "generateThreePreview": False,
                "generateRender": True,
                "generateExplosion": False,
                "enhanceImage": True,
            },
        }
    )

    prompt = service._build_enhance_image_prompt("兔子手机支架", request, service._build_design_spec(request))

    assert "SUBJECT LOCK" in prompt
    assert "REFERENCE IMAGE USAGE" in prompt
    assert "PHYSICAL PLAUSIBILITY" in prompt
    assert "CAMERA AND LENS" in prompt
    assert "phone must rest on the cradle" in prompt
    assert "must not intersect" in prompt
    assert len(prompt) >= 2000


def test_image_prompt_propaganda_stage_is_detailed_for_phone_stand() -> None:
    """_build_image_prompt 宣发阶段对手机支架生成超详细分段式提示词。"""
    _, db_context_factory = _session_context_factory()
    service = IndustrialDesignWorkflowService(
        ai_model_gateway=FakeAiModelGateway(),
        drawing_service=FakeDrawingService(),
        db_context_factory=db_context_factory,
    )
    request = IndustrialDesignWorkflowRequest.model_validate(
        {
            "inputType": "text",
            "text": "兔子手机支架，手机放在支架上",
            "projectName": "兔子手机支架",
            "industry": "消费电子",
            "options": {
                "generateCad": False,
                "generateDrawing": False,
                "generateThreePreview": False,
                "generateRender": True,
                "generateExplosion": False,
                "enhanceImage": False,
            },
        }
    )

    prompt = service._build_image_prompt("兔子手机支架", request, service._build_design_spec(request))

    assert "DETAILED IMAGE GENERATION BRIEF" in prompt
    assert "INDUSTRY CONTEXT" in prompt
    assert "SUBJECT DESCRIPTION" in prompt
    assert "PRODUCT STRUCTURE" in prompt
    assert "PHYSICAL PLAUSIBILITY" in prompt
    assert "POSTER COMPOSITION" in prompt
    assert "SCENE DESIGN" in prompt
    assert "MATERIAL AND SURFACE DETAIL" in prompt
    assert "LIGHTING" in prompt
    assert "CAMERA AND LENS" in prompt
    assert "GRAPHIC AND LAYOUT RULES" in prompt
    assert "QUALITY TARGET" in prompt
    assert "NEGATIVE PROMPT" in prompt
    assert "phone must rest on the cradle" in prompt
    assert "must not intersect" in prompt
    assert len(prompt) >= 2000


def test_image_prompt_propaganda_stage_furniture_is_detailed() -> None:
    """_build_image_prompt 宣发阶段对家具场景生成超详细分段式提示词。"""
    _, db_context_factory = _session_context_factory()
    service = IndustrialDesignWorkflowService(
        ai_model_gateway=FakeAiModelGateway(),
        drawing_service=FakeDrawingService(),
        db_context_factory=db_context_factory,
    )
    request = IndustrialDesignWorkflowRequest.model_validate(
        {
            "inputType": "text",
            "text": "新中式实木茶几宣传图",
            "projectName": "新中式实木茶几",
            "industry": "家居智造",
            "options": {
                "generateCad": False,
                "generateDrawing": False,
                "generateThreePreview": False,
                "generateRender": True,
                "generateExplosion": False,
                "enhanceImage": False,
            },
        }
    )

    prompt = service._build_image_prompt("新中式实木茶几", request, service._build_design_spec(request))

    assert "DETAILED IMAGE GENERATION BRIEF" in prompt
    assert "INDUSTRY CONTEXT" in prompt
    assert "SUBJECT DESCRIPTION" in prompt
    assert "PRODUCT STRUCTURE" in prompt
    assert "PHYSICAL PLAUSIBILITY" in prompt
    assert "POSTER COMPOSITION" in prompt
    assert "SCENE DESIGN" in prompt
    assert "MATERIAL AND SURFACE DETAIL" in prompt
    assert "LIGHTING" in prompt
    assert "CAMERA AND LENS" in prompt
    assert "GRAPHIC AND LAYOUT RULES" in prompt
    assert "QUALITY TARGET" in prompt
    assert "NEGATIVE PROMPT" in prompt
    assert "wood grain" in prompt
    assert "fabric" in prompt
    assert len(prompt) >= 2000


def test_workflow_prompts_apply_common_sense_to_non_phone_products() -> None:
    _, db_context_factory = _session_context_factory()
    service = IndustrialDesignWorkflowService(
        ai_model_gateway=FakeAiModelGateway(),
        drawing_service=FakeDrawingService(),
        db_context_factory=db_context_factory,
    )
    request = IndustrialDesignWorkflowRequest.model_validate(
        {
            "inputType": "text",
            "text": "户外储能电源宣传海报，带屏幕、插座、提手和散热孔",
            "projectName": "户外储能电源",
            "industry": "装备制造",
            "options": {
                "generateCad": False,
                "generateDrawing": False,
                "generateThreePreview": False,
                "generateRender": True,
                "generateExplosion": False,
                "enhanceImage": True,
            },
        }
    )

    image_prompt = service._build_image_prompt("户外储能电源", request, service._build_design_spec(request))
    edit_prompt = service._build_enhance_image_prompt("户外储能电源", request, service._build_design_spec(request))

    for prompt in (image_prompt, edit_prompt):
        assert "PRODUCT COMMON SENSE" in prompt
        assert "ports aligned on the front panel" in prompt
        assert "screen and buttons flush with the housing" in prompt
        assert "vents follow a consistent grid" in prompt
        assert "cables plug into ports and never pass through the shell" in prompt


def test_image_edit_prompt_modes_lock_reference_identity() -> None:
    _, db_context_factory = _session_context_factory()
    service = IndustrialDesignWorkflowService(
        ai_model_gateway=FakeAiModelGateway(),
        drawing_service=FakeDrawingService(),
        db_context_factory=db_context_factory,
    )
    base = {
        "inputType": "text",
        "text": "三层冰箱，白色面板，透明抽屉，圆角箱体",
        "projectName": "三层冰箱",
        "industry": "家电",
        "options": {
            "generateCad": False,
            "generateDrawing": False,
            "generateThreePreview": False,
            "generateRender": True,
            "generateExplosion": False,
            "enhanceImage": True,
        },
    }

    for mode, expected in (
        ("plan_2d", "2D ORTHOGRAPHIC ENGINEERING DRAWING BRIEF"),
        ("scene_fusion", "SCENE FUSION IMAGE EDITING BRIEF"),
        ("exploded", "EXPLODED ASSEMBLY IMAGE EDITING BRIEF"),
        ("fake_3d", "FAUX 3D IMAGE EDITING BRIEF"),
    ):
        request = IndustrialDesignWorkflowRequest.model_validate(
            {**base, "context": {"imageEditMode": mode}}
        )
        prompt = service._build_enhance_image_prompt(
            "三层冰箱",
            request,
            service._build_design_spec(request),
        )

        assert expected in prompt
        assert "SUBJECT LOCK" in prompt
        assert "reference image is the sole identity source" in prompt
        assert "Do NOT redesign the product" in prompt
        assert "三层冰箱" in prompt
        if mode == "plan_2d":
            assert "front view, rear view, left side view, right side view, top view, and bottom view" in prompt
            assert "No photorealistic shadows, no lifestyle scene" in prompt


def test_apply_image_edit_result_can_write_explosion_output() -> None:
    outputs: dict[str, object] = {}

    IndustrialDesignWorkflowService._apply_image_edit_result(
        outputs,
        {"assetId": "asset-1", "url": "/api/v1/assets/asset-1/download"},
        output_kind="explosion",
    )

    assert outputs["explosionPngAssetId"] == "asset-1"
    assert outputs["explosionPng"] == "/api/v1/assets/asset-1/download"
    assert outputs["imageProvider"] == "image2-edit"
    assert "renderPng" not in outputs


class FakeDbContext:
    def __enter__(self) -> object:
        return object()

    def __exit__(self, exc_type, exc, tb) -> bool:
        return False


def _session_context_factory() -> tuple[
    sessionmaker[Session],
    object,
]:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)

    @contextmanager
    def context() -> Iterator[Session]:
        session = factory()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    return factory, context


def _minimal_request(*, external: bool = False) -> IndustrialDesignWorkflowRequest:
    return IndustrialDesignWorkflowRequest.model_validate(
        {
            "inputType": "text",
            "text": "设计一个伺服联动底座结构方案",
            "projectName": "伺服联动底座结构设计",
            "options": {
                "generateCad": external,
                "generateDrawing": False,
                "generateThreePreview": False,
                "generateRender": False,
                "generateExplosion": False,
                "enhanceImage": False,
                "generateTrellisAsset": False,
            },
        }
    )


def test_service_instance_rebuild_reads_persisted_task() -> None:
    _, db_context_factory = _session_context_factory()
    first = IndustrialDesignWorkflowService(
        ai_model_gateway=FakeAiModelGateway(),
        drawing_service=FakeDrawingService(),
        db_context_factory=db_context_factory,
    )
    result = asyncio.run(
        first.create_workflow(_minimal_request(), auth_user={"sub": "user-a"})
    )
    rebuilt = IndustrialDesignWorkflowService(
        ai_model_gateway=FakeAiModelGateway(),
        drawing_service=FakeDrawingService(),
        db_context_factory=db_context_factory,
    )

    loaded = asyncio.run(
        rebuilt.get_workflow(str(result["taskId"]), auth_user={"sub": "user-a"})
    )

    assert loaded["taskId"] == result["taskId"]
    assert loaded["status"] == "completed"


def test_workflow_rejects_missing_stable_sub() -> None:
    _, db_context_factory = _session_context_factory()
    service = IndustrialDesignWorkflowService(
        ai_model_gateway=FakeAiModelGateway(),
        drawing_service=FakeDrawingService(),
        db_context_factory=db_context_factory,
    )
    with pytest.raises(AuthIdentityError):
        asyncio.run(
            service.create_workflow(
                _minimal_request(),
                auth_user={"username": "display-only"},
            )
        )


def test_create_commit_failure_does_not_schedule_pending_workflow() -> None:
    scheduled: list[Coroutine[object, object, None]] = []

    @contextmanager
    def failing_context() -> Iterator[Session]:
        engine = create_engine("sqlite+pysqlite:///:memory:")
        Base.metadata.create_all(engine)
        session = Session(engine)
        try:
            yield session
            raise RuntimeError("commit failed")
        finally:
            session.rollback()
            session.close()

    service = IndustrialDesignWorkflowService(
        ai_model_gateway=FakeAiModelGateway(),
        drawing_service=FakeDrawingService(),
        db_context_factory=failing_context,
        task_scheduler=scheduled.append,
    )

    with pytest.raises(RuntimeError, match="commit failed"):
        asyncio.run(
            service.create_workflow(
                _minimal_request(external=True),
                auth_user={"sub": "user-a"},
            )
        )

    assert scheduled == []


def test_recover_pending_rebuilds_valid_request_and_schedules_once() -> None:
    factory, db_context_factory = _session_context_factory()
    scheduled_requests: list[dict[str, object]] = []

    def capture_schedule(coroutine: Coroutine[object, object, None]) -> None:
        frame = coroutine.cr_frame
        assert frame is not None
        request = frame.f_locals["request"]
        assert isinstance(request, IndustrialDesignWorkflowRequest)
        scheduled_requests.append(request.model_dump(by_alias=True))
        coroutine.close()

    service = IndustrialDesignWorkflowService(
        ai_model_gateway=FakeAiModelGateway(),
        drawing_service=FakeDrawingService(),
        db_context_factory=db_context_factory,
        task_scheduler=capture_schedule,
    )
    pending = asyncio.run(
        service._enqueue_external_workflow(
            _minimal_request(external=True),
            auth_user={"sub": "user-a"},
            schedule=False,
        )
    )

    asyncio.run(service.recover_pending_workflows())

    assert scheduled_requests == [_minimal_request(external=True).model_dump(by_alias=True)]
    with factory() as session:
        persisted = session.get(WorkflowTask, str(pending["taskId"]))
        assert persisted is not None
        assert persisted.status == "pending"


def test_recover_pending_marks_bad_payload_failed_without_scheduling() -> None:
    factory, db_context_factory = _session_context_factory()
    scheduled: list[Coroutine[object, object, None]] = []
    service = IndustrialDesignWorkflowService(
        ai_model_gateway=FakeAiModelGateway(),
        drawing_service=FakeDrawingService(),
        db_context_factory=db_context_factory,
        task_scheduler=scheduled.append,
    )
    pending = asyncio.run(
        service._enqueue_external_workflow(
            _minimal_request(external=True),
            auth_user={"sub": "user-a"},
            schedule=False,
        )
    )
    with factory() as session:
        broken = session.get(WorkflowTask, str(pending["taskId"]))
        assert broken is not None
        broken.input_payload = {"inputType": "invalid"}
        session.commit()

    asyncio.run(service.recover_pending_workflows())

    assert scheduled == []
    with factory() as session:
        broken = session.get(WorkflowTask, str(pending["taskId"]))
        assert broken is not None
        assert broken.status == "failed"
        assert broken.error_code == "WORKFLOW_RECOVERY_PAYLOAD_INVALID"


def test_heartbeat_keeps_lease_across_long_await_and_leaves_no_tasks() -> None:
    factory, db_context_factory = _session_context_factory()
    service = IndustrialDesignWorkflowService(
        ai_model_gateway=FakeAiModelGateway(),
        drawing_service=FakeDrawingService(),
        db_context_factory=db_context_factory,
        lease_seconds=1.2,
    )
    request = _minimal_request(external=True)
    pending = asyncio.run(
        service._enqueue_external_workflow(
            request,
            auth_user={"sub": "user-a"},
            schedule=False,
        )
    )
    task_id = str(pending["taskId"])

    async def long_execution(
        workflow_id: str,
        request_value: IndustrialDesignWorkflowRequest,
        project_name: str,
        design_spec: dict[str, object],
        outputs: dict[str, object],
        diagnostics: list[dict[str, str]],
    ) -> None:
        del request_value, project_name, design_spec, outputs, diagnostics
        await asyncio.sleep(2.6)
        await service._update_task(
            workflow_id,
            status="completed",
            progress=100,
            current_step="completed",
        )

    service._execute_external_workflow = long_execution  # type: ignore[method-assign]

    async def run() -> None:
        worker = asyncio.create_task(
            service._run_external_workflow(
                task_id,
                request,
                "project",
                {},
            )
        )
        await asyncio.sleep(1.5)
        with db_context_factory() as session:
            contender = WorkflowTaskRepository(session).acquire_lease(
                task_id,
                "worker-b",
                datetime.now(timezone.utc),
                1,
            )
        assert contender is None
        await asyncio.wait_for(worker, timeout=3)
        remaining = [
            task
            for task in asyncio.all_tasks()
            if task is not asyncio.current_task() and not task.done()
        ]
        assert remaining == []

    from app.services.workflow_task_repository import WorkflowTaskRepository
    from datetime import datetime, timezone

    asyncio.run(run())
    with factory() as session:
        task = session.get(WorkflowTask, task_id)
        assert task is not None
        assert task.status == "completed"


def test_heartbeat_loss_cancels_execution_without_overwriting_new_owner() -> None:
    factory, db_context_factory = _session_context_factory()
    service = IndustrialDesignWorkflowService(
        ai_model_gateway=FakeAiModelGateway(),
        drawing_service=FakeDrawingService(),
        db_context_factory=db_context_factory,
        lease_seconds=1.2,
    )
    request = _minimal_request(external=True)
    pending = asyncio.run(
        service._enqueue_external_workflow(
            request,
            auth_user={"sub": "user-a"},
            schedule=False,
        )
    )
    task_id = str(pending["taskId"])

    async def long_execution(
        workflow_id: str,
        request_value: IndustrialDesignWorkflowRequest,
        project_name: str,
        design_spec: dict[str, object],
        outputs: dict[str, object],
        diagnostics: list[dict[str, str]],
    ) -> None:
        del request_value, project_name, design_spec, outputs, diagnostics
        await asyncio.sleep(5)
        await service._update_task(
            workflow_id,
            status="completed",
            progress=100,
            current_step="stale completion",
        )

    service._execute_external_workflow = long_execution  # type: ignore[method-assign]

    async def run() -> None:
        worker = asyncio.create_task(
            service._run_external_workflow(task_id, request, "project", {})
        )
        await asyncio.sleep(0.4)
        with db_context_factory() as session:
            task = session.get(WorkflowTask, task_id)
            assert task is not None
            task.lease_owner = "worker-b"
            task.status = "running"
            task.current_step = "new owner"
        await asyncio.wait_for(worker, timeout=2)

    asyncio.run(run())
    with factory() as session:
        task = session.get(WorkflowTask, task_id)
        assert task is not None
        assert task.lease_owner == "worker-b"
        assert task.status == "running"
        assert task.current_step == "new owner"


def test_design_workflow_fails_when_image_result_is_empty() -> None:
    _, db_context_factory = _session_context_factory()
    service = IndustrialDesignWorkflowService(
        ai_model_gateway=FakeAiModelGateway(),
        drawing_service=FakeDrawingService(),
        db_context_factory=db_context_factory,
    )
    request = IndustrialDesignWorkflowRequest.model_validate(
        {
            "inputType": "text",
            "text": "设计一个伺服联动底座结构方案",
            "projectName": "伺服联动底座结构设计",
            "industry": "装备制造",
            "mode": "create",
            "options": {
                "generateCad": False,
                "generateDrawing": True,
                "generateThreePreview": False,
                "generateRender": False,
                "generateExplosion": False,
                "enhanceImage": False,
                "generateTrellisAsset": False,
                "optimizePrompt": True,
            },
        }
    )

    pending = asyncio.run(
        service._enqueue_external_workflow(
            request,
            auth_user={"sub": "user-a"},
            schedule=False,
        )
    )
    task_id = str(pending["taskId"])
    asyncio.run(service._run_external_workflow(task_id, request, "伺服联动底座结构设计", service._build_design_spec(request)))
    result = asyncio.run(
        service.get_workflow(task_id, auth_user={"sub": "user-a"})
    )

    assert result["status"] == "failed"
    assert result["outputs"] == {}
    assert result["currentStep"] == "设计图生成失败：设计图生成失败，请稍后重试。"
    assert result["diagnostics"][0]["title"] == "设计图生成失败"


def test_local_workflow_persists_history_when_created_directly() -> None:
    history_service = FakeHistoryService()
    _, db_context_factory = _session_context_factory()
    service = IndustrialDesignWorkflowService(
        ai_model_gateway=FakeAiModelGateway(),
        drawing_service=FakeDrawingService(),
        history_service=history_service,
        db_context_factory=db_context_factory,
    )
    request = IndustrialDesignWorkflowRequest.model_validate(
        {
            "inputType": "text",
            "text": "设计一个伺服联动底座结构方案",
            "projectName": "伺服联动底座结构设计",
            "industry": "装备制造",
            "mode": "create",
            "options": {
                "generateCad": False,
                "generateDrawing": False,
                "generateThreePreview": False,
                "generateRender": False,
                "generateExplosion": False,
                "enhanceImage": False,
                "generateTrellisAsset": False,
                "optimizePrompt": True,
            },
        }
    )

    result = asyncio.run(
        service.create_workflow(
            request,
            auth_user={"username": "admin", "sub": "admin", "displayName": "管理员"},
        )
    )

    assert result["status"] == "completed"
    assert len(history_service.calls) == 1
    assert history_service.calls[0]["auth_user"] == {"username": "admin", "sub": "admin", "displayName": "管理员"}


def test_external_workflow_persists_history_after_completion() -> None:
    history_service = FakeHistoryService()
    _, db_context_factory = _session_context_factory()
    service = IndustrialDesignWorkflowService(
        ai_model_gateway=FakeAiModelGateway(),
        drawing_service=FakeDrawingService(),
        history_service=history_service,
        db_context_factory=db_context_factory,
        task_scheduler=lambda coroutine: coroutine.close(),
        trusted_image_validator=lambda _content_type, _content: True,
    )
    request = IndustrialDesignWorkflowRequest.model_validate(
        {
            "inputType": "text",
            "text": "设计一个伺服联动底座结构方案",
            "projectName": "伺服联动底座结构设计",
            "industry": "装备制造",
            "mode": "create",
            "options": {
                "generateCad": False,
                "generateDrawing": True,
                "generateThreePreview": False,
                "generateRender": False,
                "generateExplosion": False,
                "enhanceImage": False,
                "generateTrellisAsset": False,
                "optimizePrompt": True,
            },
        }
    )

    pending = asyncio.run(
        service.create_workflow(
            request,
            auth_user={"username": "admin", "sub": "admin", "displayName": "管理员"},
        )
    )
    task_id = str(pending["taskId"])
    asyncio.run(service._run_external_workflow(task_id, request, "伺服联动底座结构设计", service._build_design_spec(request)))

    assert len(history_service.calls) == 1
    assert getattr(history_service.calls[0]["version_payload"], "task_id", "") == task_id


def test_history_failure_rolls_back_flag_and_retry_is_exactly_once() -> None:
    history_service = FakeHistoryService()
    history_service.fail = True
    factory, db_context_factory = _session_context_factory()
    service = IndustrialDesignWorkflowService(
        ai_model_gateway=FakeAiModelGateway(),
        drawing_service=FakeDrawingService(),
        history_service=history_service,
        db_context_factory=db_context_factory,
    )
    result = asyncio.run(
        service.create_workflow(
            _minimal_request(),
            auth_user={"sub": "user-a"},
        )
    )
    task_id = str(result["taskId"])
    with factory() as session:
        task = session.get(WorkflowTask, task_id)
        assert task is not None
        assert task.history_persisted is False

    history_service.fail = False
    asyncio.run(service.recover_pending_workflows())
    asyncio.run(service.recover_pending_workflows())

    assert len(history_service.calls) == 1
    assert history_service.calls[0]["auth_user"] == {
        "sub": "user-a",
        "username": "user-a",
        "displayName": "user-a",
    }
    with factory() as session:
        task = session.get(WorkflowTask, task_id)
        assert task is not None
        assert task.history_persisted is True


def test_concurrent_history_compensation_invokes_business_write_once() -> None:
    history_service = FakeHistoryService()
    history_service.fail = True
    _, db_context_factory = _session_context_factory()
    service = IndustrialDesignWorkflowService(
        ai_model_gateway=FakeAiModelGateway(),
        drawing_service=FakeDrawingService(),
        history_service=history_service,
        db_context_factory=db_context_factory,
    )
    result = asyncio.run(
        service.create_workflow(_minimal_request(), auth_user={"sub": "user-a"})
    )
    history_service.fail = False

    async def compensate() -> None:
        await asyncio.gather(
            service._persist_terminal_history_if_needed(result, _minimal_request(), None),
            service._persist_terminal_history_if_needed(result, _minimal_request(), None),
        )

    asyncio.run(compensate())

    assert len(history_service.calls) == 1


def test_runner_swallows_lease_acquire_failure_without_background_exception() -> None:
    _, db_context_factory = _session_context_factory()
    service = IndustrialDesignWorkflowService(
        db_context_factory=db_context_factory,
    )

    def fail_acquire(task_id: str) -> dict[str, object] | None:
        del task_id
        raise RuntimeError("db unavailable")

    service._acquire_lease_sync = fail_acquire  # type: ignore[method-assign]
    asyncio.run(
        service._run_external_workflow(
            "missing",
            _minimal_request(external=True),
            "project",
            {},
        )
    )


def test_shutdown_cancels_and_drains_registered_background_tasks() -> None:
    _, db_context_factory = _session_context_factory()
    service = IndustrialDesignWorkflowService(db_context_factory=db_context_factory)

    async def run() -> None:
        async def pending() -> None:
            await asyncio.sleep(30)

        service._schedule(pending())
        assert len(service._background_tasks) == 1
        await service.shutdown()
        assert service._background_tasks == set()

    asyncio.run(run())
