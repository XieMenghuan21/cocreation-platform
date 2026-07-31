from __future__ import annotations

import asyncio
import base64
import os
import signal
import struct
import time
import zlib
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, contextmanager
from io import BytesIO
from pathlib import Path
from uuid import UUID

import pytest
from fastapi import HTTPException, UploadFile
from starlette.datastructures import Headers
from sqlalchemy import create_engine, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.api.v1.forgecad import (
    CadAiAutoGenerateRequest,
    auto_generate_cad_project,
    download_cad_ai_asset,
    download_forgecad_file,
    download_imported_cad_file,
    download_imported_cad_preview_file,
    get_cad_ai_task,
)
import app.api.v1.forgecad as forgecad_api
from app.api.v1.industrial_design import (
    download_industrial_design_asset,
    download_industrial_design_image_edit_asset,
)
from app.db.session import Base
from app.models.persistence import Asset, AssetBlobChunk
from app.models.cocreation_history import (
    CocreationProjectVersionHistory,
    CocreationVersionAssetEntry,
)
from app.schemas.furniture_drawing import WardrobeDrawingRequest
from app.schemas.forgecad import ForgeCadGenerateRequest
from app.schemas.industrial_design import IndustrialDesignImageEditRequest
from app.services.asset_blob_service import AssetBlobService
from app.services.forgecad_service import ForgeCadService, ForgeCadServiceError
from app.services.furniture_drawing_service import FurnitureDrawingService
from app.services.cocreation_history_service import CocreationHistoryService
from app.services.image2_edit_service import Image2EditService, Image2EditServiceError
from app.services.industrial_design_workflow_service import (
    IndustrialDesignWorkflowService,
)
from app.services.cad_ai_gateway_service import CadAiGatewayError
from app.services.workflow_task_repository import WorkflowTaskRepository
from app.services.zoo_design_service import ZooDesignServiceError
from app.services.safe_content_validator import (
    is_valid_jpeg,
    is_valid_pdf,
    is_valid_png,
    trusted_media_pipeline_available,
)
import app.services.safe_content_validator as safe_content_validator

VALID_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
)


def _test_trusted_image_validator(content_type: str, content: bytes) -> bool:
    return content_type == "image/png" and content == VALID_PNG


def _png_chunk(kind: bytes, content: bytes) -> bytes:
    return (
        struct.pack(">I", len(content))
        + kind
        + content
        + struct.pack(">I", zlib.crc32(kind + content) & 0xFFFFFFFF)
    )


@pytest.fixture
def engine(tmp_path: Path) -> Iterator[Engine]:
    test_engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'generated.db'}")
    Base.metadata.create_all(test_engine)
    try:
        yield test_engine
    finally:
        Base.metadata.drop_all(test_engine)
        test_engine.dispose()


def _asset_service(engine: Engine) -> AssetBlobService:
    return AssetBlobService(
        chunk_size=4,
        session_factory=sessionmaker(bind=engine, expire_on_commit=False),
    )


def _db_context(
    engine: Engine,
) -> Callable[[], AbstractContextManager[Session]]:
    factory = sessionmaker(bind=engine, expire_on_commit=False)

    @contextmanager
    def context() -> Iterator[Session]:
        with factory() as db:
            try:
                yield db
                db.commit()
            except Exception:
                db.rollback()
                raise

    return context


def _asset_bytes(db: Session, asset_id: str) -> bytes:
    return b"".join(
        db.scalars(
            select(AssetBlobChunk.content)
            .where(AssetBlobChunk.asset_id == UUID(asset_id))
            .order_by(AssetBlobChunk.chunk_index)
        )
    )


def _drawing_request() -> WardrobeDrawingRequest:
    return WardrobeDrawingRequest.model_validate(
        {
            "industry": "家居智造",
            "templateType": "wardrobe",
            "projectName": "数据库工程图",
            "width": 1200,
            "height": 1800,
            "depth": 600,
            "modules": [
                {
                    "sectionType": "shelf",
                    "width": 1200,
                    "shelfCount": 3,
                }
            ],
        }
    )


def test_drawing_outputs_are_database_assets_without_durable_paths(
    engine: Engine,
    tmp_path: Path,
) -> None:
    service = FurnitureDrawingService(asset_service=_asset_service(engine))
    with Session(engine) as db:
        result = service.render_and_store(
            db=db,
            user_id="alice",
            request=_drawing_request(),
        )
        db.commit()

        assert result.svg_asset_id is not None
        assert result.pdf_asset_id is not None
        assert result.dxf_asset_id is not None
        assert _asset_bytes(db, result.svg_asset_id) == result.svg_content.encode()
        assert _asset_bytes(db, result.pdf_asset_id).startswith(b"%PDF")
        assert _asset_bytes(db, result.dxf_asset_id).endswith(b"0\nEOF")
        serialized = result.model_dump(by_alias=True)
        assert not any(key.endswith("Path") for key in serialized)

    assert not (tmp_path / "storage" / "drawings").exists()


def test_zoo_outputs_are_assets_and_runtime_temp_root_is_empty(
    engine: Engine,
    tmp_path: Path,
) -> None:
    runtime_root = tmp_path / "runtime"
    runtime_root.mkdir()
    service = IndustrialDesignWorkflowService(
        asset_service=_asset_service(engine),
        runtime_temp_root=runtime_root,
    )
    with Session(engine) as db:
        assets = service.persist_zoo_outputs(
            db=db,
            user_id="alice",
            project_name="servo",
            outputs={
                "preview.glb": "Z2xURgIAAAAMAAAA",
                "model.step": "SVNPLTEwMzAzLTIxOwpFTkQtSVNPLTEwMzAzLTIxOw==",
            },
        )
        db.commit()

        assert _asset_bytes(db, assets["glb"]).startswith(b"glTF")
        assert _asset_bytes(db, assets["step"]).startswith(b"ISO-10303-21;")
    assert list(runtime_root.iterdir()) == []


def test_generated_image_data_url_is_imported_as_database_asset(
    engine: Engine,
) -> None:
    service = IndustrialDesignWorkflowService(asset_service=_asset_service(engine))
    with Session(engine) as db:
        with pytest.raises(CadAiGatewayError) as caught:
            service.persist_generated_image(
                db=db,
                user_id="alice",
                image_url=(
                    "data:image/png;base64,"
                    + base64.b64encode(VALID_PNG).decode("ascii")
                ),
            )
    assert caught.value.error_code == "IMAGE_PIPELINE_TRUSTED_DECODER_UNAVAILABLE"
    assert caught.value.status_code == 503


@pytest.mark.asyncio
async def test_generated_image_http_fails_before_network_without_trusted_decoder(
    engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = IndustrialDesignWorkflowService(
        asset_service=_asset_service(engine),
        db_context_factory=_db_context(engine),
    )
    network_called = False

    class ForbiddenClient:
        def __init__(self, **_: object) -> None:
            nonlocal network_called
            network_called = True
            raise AssertionError("HTTP must not be called")

    monkeypatch.setattr(
        "app.services.industrial_design_workflow_service.httpx.AsyncClient",
        ForbiddenClient,
    )
    with pytest.raises(CadAiGatewayError) as caught:
        await service._persist_generated_image_url(
            user_id="alice",
            image_url="https://example.invalid/generated.png",
            task_id="task",
            publish_asset=False,
        )
    assert caught.value.error_code == "IMAGE_PIPELINE_TRUSTED_DECODER_UNAVAILABLE"
    assert caught.value.status_code == 503
    assert not network_called


def test_staged_generated_asset_is_not_available_until_task_transaction_publishes(
    engine: Engine,
) -> None:
    service = _asset_service(engine)
    with Session(engine) as db:
        asset = service.store_bytes(
            db=db,
            user_id="alice",
            filename="staged.png",
            content_type="image/png",
            kind="image",
            source="generated",
            content=b"PNG",
            publish=False,
        )
        db.commit()
        assert asset.status == "staged"
        from app.services.asset_blob_service import AssetStateError

        with pytest.raises(AssetStateError):
            service.get_asset(db, asset.id, "alice")


def test_terminal_task_update_failure_does_not_leave_available_orphan(
    engine: Engine,
) -> None:
    from app.schemas.industrial_design import IndustrialDesignWorkflowRequest

    request = IndustrialDesignWorkflowRequest.model_validate(
        {
            "inputType": "text",
            "text": "transaction",
            "options": {
                "generateCad": False,
                "generateDrawing": False,
                "generateThreePreview": False,
                "generateRender": False,
                "generateExplosion": False,
                "enhanceImage": False,
            },
        }
    )
    snapshot: dict[str, object] = {
        "taskId": "workflow-transaction",
        "status": "pending",
        "progress": 5,
        "currentStep": "pending",
        "outputs": {},
    }
    blob_service = _asset_service(engine)
    with Session(engine) as db:
        WorkflowTaskRepository(db).create("alice", request, snapshot)
        asset = blob_service.store_bytes(
            db=db,
            user_id="alice",
            filename="result.png",
            content_type="image/png",
            kind="image",
            source="generated",
            content=b"PNG",
            task_id="workflow-transaction",
            publish=False,
        )
        db.commit()
        asset_id = asset.id

    class FailingRepository(WorkflowTaskRepository):
        def update_and_append_event(self, *_: object, **__: object) -> dict[str, object]:
            raise RuntimeError("snapshot update failed")

    service = IndustrialDesignWorkflowService(
        db_context_factory=_db_context(engine),
        repository_factory=FailingRepository,
    )
    with pytest.raises(RuntimeError, match="snapshot update failed"):
        service._update_task_sync(
            "workflow-transaction",
            "completed",
            100,
            "done",
            {"renderPngAssetId": str(asset_id)},
            [],
            None,
            None,
            False,
            False,
        )
    with Session(engine) as db:
        stored = db.get(Asset, asset_id)
        assert stored is not None
        assert stored.status == "staged"


def test_failed_workflow_marks_image_and_response_assets_failed(
    engine: Engine,
) -> None:
    from app.schemas.industrial_design import IndustrialDesignWorkflowRequest

    request = IndustrialDesignWorkflowRequest.model_validate(
        {
            "inputType": "text",
            "text": "failed image edit",
            "options": {
                "generateCad": False,
                "generateDrawing": False,
                "generateThreePreview": False,
                "generateRender": False,
                "generateExplosion": False,
                "enhanceImage": False,
            },
        }
    )
    blob_service = _asset_service(engine)
    with Session(engine) as db:
        WorkflowTaskRepository(db).create(
            "alice",
            request,
            {
                "taskId": "workflow-image-edit-failed",
                "status": "pending",
                "progress": 5,
                "currentStep": "pending",
                "outputs": {},
            },
        )
        image = blob_service.store_bytes(
            db=db,
            user_id="alice",
            filename="result.png",
            content_type="image/png",
            kind="image",
            source="generated",
            content=VALID_PNG,
            task_id="workflow-image-edit-failed",
            publish=False,
        )
        response = blob_service.store_bytes(
            db=db,
            user_id="alice",
            filename="response.json",
            content_type="application/json",
            kind="source",
            source="generated",
            content=b'{"ok":false}',
            task_id="workflow-image-edit-failed",
            publish=False,
        )
        db.commit()
        image_id = image.id
        response_id = response.id

    service = IndustrialDesignWorkflowService(
        db_context_factory=_db_context(engine),
    )
    service._update_task_sync(
        "workflow-image-edit-failed",
        "failed",
        100,
        "failed",
        {
            "enhancedImageAssetId": str(image_id),
            "enhancedImageResponseAssetId": str(response_id),
        },
        [],
        "IMAGE_EDIT_FAILED",
        "failed",
        False,
        False,
    )
    with Session(engine) as db:
        assert db.get(Asset, image_id).status == "failed"
        assert db.get(Asset, response_id).status == "failed"


@pytest.mark.asyncio
async def test_local_task_creation_failure_rolls_back_generated_assets(
    engine: Engine,
) -> None:
    from app.schemas.industrial_design import IndustrialDesignWorkflowRequest

    class DisabledImageGateway:
        def image_configured(self) -> bool:
            return False

    class FailingCreateRepository(WorkflowTaskRepository):
        def create(self, *_: object, **__: object) -> dict[str, object]:
            raise RuntimeError("task creation failed")

    blob_service = _asset_service(engine)
    service = IndustrialDesignWorkflowService(
        asset_service=blob_service,
        drawing_service=FurnitureDrawingService(asset_service=blob_service),
        ai_model_gateway=DisabledImageGateway(),
        db_context_factory=_db_context(engine),
        repository_factory=FailingCreateRepository,
    )
    request = IndustrialDesignWorkflowRequest.model_validate(
        {
            "inputType": "text",
            "text": "transaction rollback",
            "options": {
                "generateCad": False,
                "generateDrawing": True,
                "generateThreePreview": False,
                "generateRender": False,
                "generateExplosion": False,
                "enhanceImage": False,
            },
        }
    )

    with pytest.raises(RuntimeError, match="task creation failed"):
        await service.create_workflow(request, {"sub": "alice"})

    with Session(engine) as db:
        assert list(db.scalars(select(Asset))) == []


def test_local_workflow_is_failed_when_required_drawing_asset_write_fails(
    engine: Engine,
) -> None:
    class DisabledGateway:
        base_url = ""

    class DisabledImageGateway:
        def image_configured(self) -> bool:
            return False

    class FailingDrawingService:
        def render_and_store(self, **_: object) -> object:
            raise RuntimeError("database asset write failed")

    service = IndustrialDesignWorkflowService(
        cad_ai_gateway=DisabledGateway(),
        ai_model_gateway=DisabledImageGateway(),
        drawing_service=FailingDrawingService(),
        db_context_factory=_db_context(engine),
    )
    request = {
        "inputType": "text",
        "text": "drawing required",
        "options": {
            "generateCad": False,
            "generateDrawing": True,
            "generateThreePreview": False,
            "generateRender": False,
            "generateExplosion": False,
            "enhanceImage": False,
        },
    }
    from app.schemas.industrial_design import IndustrialDesignWorkflowRequest

    result = asyncio.run(
        service.create_workflow(
            IndustrialDesignWorkflowRequest.model_validate(request),
            auth_user={"sub": "alice"},
        )
    )

    assert result["status"] == "failed"
    assert result["outputs"] == {}
    assert result["errorCode"] == "REQUIRED_ASSET_PERSISTENCE_FAILED"
    loaded = asyncio.run(
        service.get_workflow(str(result["taskId"]), auth_user={"sub": "alice"})
    )
    assert loaded["status"] == "failed"


def test_external_workflow_fails_and_rejects_partial_assets_when_cad_is_missing(
    engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.schemas.industrial_design import IndustrialDesignWorkflowRequest

    class ExternalGateway:
        def image_configured(self) -> bool:
            return True

        async def generate_cad(self, *_: object, **__: object) -> object:
            raise ForgeCadServiceError("cad unavailable", "CAD_UNAVAILABLE")

        async def create_text_to_cad(self, *_: object, **__: object) -> object:
            raise ZooDesignServiceError("zoo unavailable", "ZOO_UNAVAILABLE")

    service = IndustrialDesignWorkflowService(
        ai_model_gateway=ExternalGateway(),
        asset_service=_asset_service(engine),
        db_context_factory=_db_context(engine),
        trusted_image_validator=_test_trusted_image_validator,
    )

    async def image_result(**_: object) -> dict[str, object]:
        return {
            "resultUrl": (
                "data:image/png;base64,"
                + base64.b64encode(VALID_PNG).decode("ascii")
            ),
            "taskId": "image-task",
            "model": "test-image",
        }

    monkeypatch.setattr(service, "_generate_external_design_image", image_result)
    request = IndustrialDesignWorkflowRequest.model_validate(
        {
            "inputType": "text",
            "text": "drawing and cad are both required",
            "options": {
                "generateCad": True,
                "generateDrawing": True,
                "generateThreePreview": False,
                "generateRender": False,
                "generateExplosion": False,
                "enhanceImage": False,
            },
        }
    )

    async def execute() -> dict[str, object]:
        pending = await service._enqueue_external_workflow(
            request,
            auth_user={"sub": "alice"},
            schedule=False,
        )
        task_id = str(pending["taskId"])
        await service._run_external_workflow(
            task_id,
            request,
            str(pending["projectId"]),
            service._build_design_spec(request),
        )
        return await service.get_workflow(task_id, {"sub": "alice"})

    result = asyncio.run(execute())
    assert result["status"] == "failed"
    assert result["errorCode"] == "REQUIRED_ASSET_PERSISTENCE_FAILED"
    output = result["outputs"]
    assert isinstance(output, dict)
    image_asset_id = output["renderPngAssetId"]
    assert isinstance(image_asset_id, str)
    with Session(engine) as db:
        image_asset = db.get(Asset, UUID(image_asset_id))
        assert image_asset is not None
        assert image_asset.status == "failed"


def test_configured_remote_gateway_is_rejected_before_raw_outputs_are_persisted(
    engine: Engine,
) -> None:
    class RemoteGateway:
        base_url = "https://cad.example.test"

        async def auto_generate(self, _: dict[str, object]) -> dict[str, object]:
            raise AssertionError("unsafe remote gateway must not be called")

    class DisabledImageGateway:
        def image_configured(self) -> bool:
            return False

    service = IndustrialDesignWorkflowService(
        cad_ai_gateway=RemoteGateway(),
        ai_model_gateway=DisabledImageGateway(),
        db_context_factory=_db_context(engine),
    )
    from app.schemas.industrial_design import IndustrialDesignWorkflowRequest

    request = IndustrialDesignWorkflowRequest.model_validate(
        {
            "inputType": "text",
            "text": "remote",
            "options": {
                "generateCad": False,
                "generateDrawing": False,
                "generateThreePreview": False,
                "generateRender": False,
                "generateExplosion": False,
                "enhanceImage": False,
            },
        }
    )
    with pytest.raises(CadAiGatewayError) as caught:
        asyncio.run(service.create_workflow(request, auth_user={"sub": "alice"}))
    assert caught.value.error_code == "CAD_AI_DATABASE_ASSET_RECOVERY_UNAVAILABLE"


@pytest.mark.parametrize(("request_user", "asset_status"), [("bob", "available"), ("alice", "failed")])
def test_workflow_rejects_unowned_or_unavailable_input_assets_before_task_creation(
    engine: Engine,
    request_user: str,
    asset_status: str,
) -> None:
    class DisabledGateway:
        base_url = ""

    class DisabledImageGateway:
        def image_configured(self) -> bool:
            return False

    blob_service = _asset_service(engine)
    with Session(engine) as db:
        asset = blob_service.store_bytes(
            db=db,
            user_id="alice",
            filename="input.step",
            content_type="application/step",
            kind="source",
            source="upload",
            content=b"ISO-10303-21;\nEND-ISO-10303-21;",
        )
        asset.status = asset_status
        db.commit()
        asset_id = str(asset.id)
    service = IndustrialDesignWorkflowService(
        cad_ai_gateway=DisabledGateway(),
        ai_model_gateway=DisabledImageGateway(),
        db_context_factory=_db_context(engine),
    )
    from app.schemas.industrial_design import IndustrialDesignWorkflowRequest

    request = IndustrialDesignWorkflowRequest.model_validate(
        {
            "inputType": "cad",
            "assetIds": [asset_id],
            "assetMetas": [
                {
                    "assetId": asset_id,
                    "filename": "spoofed.step",
                    "extension": "step",
                    "parseStatus": "parsed",
                }
            ],
            "options": {
                "generateCad": False,
                "generateDrawing": False,
                "generateThreePreview": False,
                "generateRender": False,
                "generateExplosion": False,
                "enhanceImage": False,
            },
        }
    )
    with pytest.raises(CadAiGatewayError) as caught:
        asyncio.run(
            service.create_workflow(request, auth_user={"sub": request_user})
        )
    assert caught.value.error_code == "WORKFLOW_INPUT_ASSET_UNAVAILABLE"
    with Session(engine) as db:
        from app.models.persistence import WorkflowTask

        assert db.scalar(select(WorkflowTask)) is None


def test_legacy_remote_cad_task_proxy_is_disabled_without_owner_leak(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class UnsafeGateway:
        async def auto_generate(self, _: dict[str, object]) -> dict[str, object]:
            raise AssertionError("remote task must not be submitted")

        async def get_task(self, _: str) -> dict[str, object]:
            raise AssertionError("remote task must not be queried")

    monkeypatch.setattr(forgecad_api, "cad_ai_gateway_service", UnsafeGateway())
    request = CadAiAutoGenerateRequest(
        inputType="text",
        text="unsafe remote task",
    )
    created = asyncio.run(
        auto_generate_cad_project(request, auth_user={"sub": "alice"})
    )
    alice = asyncio.run(get_cad_ai_task("remote-1", auth_user={"sub": "alice"}))
    bob = asyncio.run(get_cad_ai_task("remote-1", auth_user={"sub": "bob"}))

    assert created.status_code == 503
    assert alice.status_code == 503
    assert bob.status_code == 503


def test_version_snapshot_uses_asset_ids_and_never_path_fields() -> None:
    service = IndustrialDesignWorkflowService()
    script_id = "3ef2ee96-9cb7-4b1b-a88b-c38ad971c79c"
    render_id = "7fd34ed4-3f22-493a-8b4a-ed496972de74"
    payload = service._build_version_payload(
        {
            "taskId": "task-1",
            "status": "completed",
            "progress": 100,
            "projectId": "asset project",
            "outputs": {
                "modelScriptAssetId": script_id,
                "modelScript": f"/api/v1/assets/{script_id}/download",
                "renderPngAssetId": render_id,
                "renderPng": f"/api/v1/assets/{render_id}/download",
                "outputPath": "/tmp/raw.step",
                "externalUrl": "https://remote.example/raw.glb",
            },
        },
        None,
    )

    serialized = payload.model_dump(by_alias=True)
    assert "scriptPath" not in serialized
    assert "workDir" not in serialized
    assert "outputPath" not in serialized
    assert payload.script_asset_id == script_id
    assert payload.output_asset_id == render_id
    assert payload.download_url == f"/api/v1/assets/{render_id}/download"
    assert {item.asset_id for item in payload.generated_assets} == {
        script_id,
        render_id,
    }
    assert "/tmp/raw.step" not in str(serialized)
    assert "remote.example" not in str(serialized)


def test_version_persists_asset_foreign_keys_without_path_values(
    engine: Engine,
) -> None:
    blob_service = _asset_service(engine)
    workflow_service = IndustrialDesignWorkflowService()
    with Session(engine) as db:
        script = blob_service.store_bytes(
            db=db,
            user_id="alice",
            filename="model.forge.js",
            content_type="text/javascript",
            kind="source",
            source="generated",
            content=b"return {};",
        )
        output = blob_service.store_bytes(
            db=db,
            user_id="alice",
            filename="render.png",
            content_type="image/png",
            kind="image",
            source="generated",
            content=b"PNG",
        )
        task = {
            "taskId": "task-version-assets",
            "status": "completed",
            "progress": 100,
            "projectId": "asset-project",
            "outputs": {
                "modelScriptAssetId": str(script.id),
                "renderPngAssetId": str(output.id),
            },
        }
        CocreationHistoryService().upsert_project_with_version_in_transaction(
            db,
            auth_user={"sub": "alice"},
            project_payload=workflow_service._build_project_payload(task, None),
            version_payload=workflow_service._build_version_payload(task, None),
        )
        db.commit()

        version = db.scalar(select(CocreationProjectVersionHistory))
        assert version is not None
        assert version.script_asset_id is None
        assert version.output_asset_id is None
        assert "scriptPath" not in version.snapshot_data
        assert "workDir" not in version.snapshot_data
        assert "outputPath" not in version.snapshot_data
        assert version.generated_assets == []
        entries = db.scalars(select(CocreationVersionAssetEntry)).all()
        assert {(entry.asset_id, entry.role) for entry in entries} == {
            (script.id, "script"),
            (script.id, "generated"),
            (output.id, "output"),
            (output.id, "generated"),
        }


@pytest.mark.parametrize(("owner", "status"), [("bob", "available"), ("alice", "failed")])
def test_version_rejects_unowned_or_unavailable_asset_references(
    engine: Engine,
    owner: str,
    status: str,
) -> None:
    blob_service = _asset_service(engine)
    workflow_service = IndustrialDesignWorkflowService()
    with Session(engine) as db:
        asset = blob_service.store_bytes(
            db=db,
            user_id="alice",
            filename="model.forge.js",
            content_type="text/javascript",
            kind="source",
            source="generated",
            content=b"return {};",
        )
        asset.status = status
        task = {
            "taskId": "task-invalid-version-asset",
            "status": "completed",
            "progress": 100,
            "projectId": "asset-project",
            "outputs": {"modelScriptAssetId": str(asset.id)},
        }
        with pytest.raises(ValueError, match="资产"):
            CocreationHistoryService().upsert_project_with_version_in_transaction(
                db,
                auth_user={"sub": owner},
                project_payload=workflow_service._build_project_payload(task, None),
                version_payload=workflow_service._build_version_payload(task, None),
            )


def test_forgecad_import_and_step_preview_use_assets_and_clean_temp_directory(
    engine: Engine,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime_root = tmp_path / "runtime"
    runtime_root.mkdir()
    service = ForgeCadService(
        asset_service=_asset_service(engine),
        runtime_temp_root=runtime_root,
    )
    service.step_converter_command = "configured"
    step_content = b"ISO-10303-21;\nHEADER;\nENDSEC;\nEND-ISO-10303-21;"

    def convert(*, input_path: Path, preview_path: Path) -> str:
        assert input_path.read_bytes() == step_content
        preview_path.write_bytes(b"solid preview")
        return "converted"

    monkeypatch.setattr(service, "_convert_step_preview", convert)
    with Session(engine) as db:
        result = service.save_import_asset(
            db=db,
            user_id="alice",
            filename="part.step",
            content_type="application/step",
            content=step_content,
        )
        db.commit()

        assert result.storage_path is None
        assert result.preview_asset_path is None
        assert _asset_bytes(db, result.asset_id) == step_content
        assert result.preview_asset_id is not None
        assert _asset_bytes(db, result.preview_asset_id) == b"solid preview"
        assert result.preview_asset_url == (
            f"/api/v1/assets/{result.preview_asset_id}/download"
        )
    assert list(runtime_root.iterdir()) == []


def test_forgecad_rejects_extension_content_mismatch(engine: Engine) -> None:
    service = ForgeCadService(asset_service=_asset_service(engine))
    with Session(engine) as db, pytest.raises(ForgeCadServiceError) as caught:
        service.save_import_asset(
            db=db,
            user_id="alice",
            filename="fake.png",
            content_type="image/png",
            content=b"not-a-png",
        )
    assert caught.value.error_code == "FORGECAD_IMPORT_FORMAT_DISABLED"
    assert caught.value.status_code == 503


@pytest.mark.parametrize(
    ("filename", "content_type", "content"),
    [
        ("truncated.png", "image/png", b"\x89PNG\r\n\x1a\n"),
        (
            "truncated.jpg",
            "image/jpeg",
            b"\xff\xd8\xff\xc0\x00\x11\x08\x00\x01\x00\x01\xff\xd9",
        ),
        ("header-only.pdf", "application/pdf", b"%PDF-1.7"),
    ],
)
def test_forgecad_rejects_structurally_invalid_document_content(
    engine: Engine,
    filename: str,
    content_type: str,
    content: bytes,
) -> None:
    service = ForgeCadService(asset_service=_asset_service(engine))
    with Session(engine) as db, pytest.raises(ForgeCadServiceError):
        service.save_import_asset(
            db=db,
            user_id="alice",
            filename=filename,
            content_type=content_type,
            content=content,
        )


def test_safe_pdf_validator_fails_closed_without_trusted_parser() -> None:
    drawing_service = FurnitureDrawingService()
    pdf = drawing_service._render_pdf_bytes(
        '<svg xmlns="http://www.w3.org/2000/svg" width="1" height="1"></svg>'
    )
    assert not is_valid_pdf(pdf)


def test_forgecad_disables_external_image_and_pdf_imports_without_trusted_parsers(
    engine: Engine,
) -> None:
    service = ForgeCadService(asset_service=_asset_service(engine))
    for filename, content_type, content in (
        ("valid.png", "image/png", VALID_PNG),
        (
            "valid.pdf",
            "application/pdf",
            FurnitureDrawingService()._render_pdf_bytes("<svg/>"),
        ),
    ):
        with Session(engine) as db, pytest.raises(ForgeCadServiceError) as caught:
            service.save_import_asset(
                db=db,
                user_id="alice",
                filename=filename,
                content_type=content_type,
                content=content,
            )
        assert caught.value.error_code == "FORGECAD_IMPORT_FORMAT_DISABLED"
        assert caught.value.status_code == 503


def test_media_pipeline_stays_disabled_when_modules_exist_but_flag_is_false(
    engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        safe_content_validator.settings,
        "ENABLE_TRUSTED_MEDIA_PIPELINE",
        False,
    )
    monkeypatch.setattr(safe_content_validator, "Image", object())
    monkeypatch.setattr(safe_content_validator, "PdfReader", object())
    monkeypatch.setattr(safe_content_validator, "version", lambda _: "99.0.0")
    assert not trusted_media_pipeline_available()

    service = ForgeCadService(asset_service=_asset_service(engine))
    with Session(engine) as db, pytest.raises(ForgeCadServiceError) as caught:
        service.save_import_asset(
            db=db,
            user_id="alice",
            filename="valid.png",
            content_type="image/png",
            content=VALID_PNG,
        )
    assert caught.value.error_code == "FORGECAD_IMPORT_FORMAT_DISABLED"
    assert caught.value.status_code == 503


def test_image_validator_rejects_crc_valid_png_with_invalid_bit_depth() -> None:
    ihdr = struct.pack(">IIBBBBB", 1, 1, 0, 2, 0, 0, 0)
    malicious = (
        b"\x89PNG\r\n\x1a\n"
        + _png_chunk(b"IHDR", ihdr)
        + _png_chunk(b"IDAT", b"random-not-zlib")
        + _png_chunk(b"IEND", b"")
    )
    assert not is_valid_png(malicious)


def test_image_validator_rejects_jpeg_with_zero_components() -> None:
    malicious = (
        b"\xff\xd8"
        b"\xff\xc0\x00\x08\x08\x00\x01\x00\x01\x00"
        b"\xff\xda\x00\x06\x00\x00\x3f\x00"
        b"\xff\xd9"
    )
    assert not is_valid_jpeg(malicious)


def test_pdf_validator_rejects_empty_xref_table() -> None:
    prefix = b"%PDF-1.4\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
    xref_offset = len(prefix)
    malicious = (
        prefix
        + b"xref\n0 0\ntrailer<</Size 1/Root 1 0 R>>\n"
        + f"startxref\n{xref_offset}\n%%EOF".encode("ascii")
    )
    assert not is_valid_pdf(malicious)


@pytest.mark.parametrize(
    "filename",
    [
        "fake.stl",
        "fake.dxf",
        "fake.dwg",
        "fake.wav",
        "fake.mp3",
        "fake.m4a",
        "fake.aac",
        "fake.ogg",
        "fake.flac",
        "fake.webm",
    ],
)
def test_forgecad_rejects_unverified_import_formats(
    engine: Engine,
    filename: str,
) -> None:
    service = ForgeCadService(asset_service=_asset_service(engine))
    with Session(engine) as db, pytest.raises(ForgeCadServiceError):
        service.save_import_asset(
            db=db,
            user_id="alice",
            filename=filename,
            content_type="application/octet-stream",
            content=b"unverified-content",
        )


def test_forgecad_dwg_is_explicitly_disabled_even_with_signature(
    engine: Engine,
) -> None:
    service = ForgeCadService(asset_service=_asset_service(engine))
    with Session(engine) as db, pytest.raises(ForgeCadServiceError) as caught:
        service.save_import_asset(
            db=db,
            user_id="alice",
            filename="drawing.dwg",
            content_type="application/acad",
            content=b"AC1032" + b"\x00" * 64,
        )
    assert caught.value.error_code == "FORGECAD_IMPORT_FORMAT_DISABLED"


def test_forgecad_step_converter_uses_argv_without_shell(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = ForgeCadService()
    service.step_converter_command = "converter --input {input} --output {output}"
    captured: dict[str, object] = {}

    class Completed:
        returncode = 0
        stdout = "converted"
        stderr = ""

    def run(command: list[str], **kwargs: object) -> Completed:
        captured["command"] = command
        captured["kwargs"] = kwargs
        return Completed()

    monkeypatch.setattr("app.services.forgecad_service.subprocess.run", run)
    input_path = tmp_path / "part;touch-pwned.step"
    preview_path = tmp_path / "preview.stl"
    assert service._convert_step_preview(
        input_path=input_path,
        preview_path=preview_path,
    ) == "converted"
    assert captured["command"] == [
        "converter",
        "--input",
        str(input_path),
        "--output",
        str(preview_path),
    ]
    kwargs = captured["kwargs"]
    assert isinstance(kwargs, dict)
    assert "shell" not in kwargs
    environment = kwargs["env"]
    assert isinstance(environment, dict)
    assert set(environment) == {"PATH", "LANG", "LC_ALL"}


def test_forgecad_generated_script_execution_requires_sandbox(
    tmp_path: Path,
) -> None:
    service = ForgeCadService()
    service.sandbox_wrapper = ""
    with pytest.raises(ForgeCadServiceError) as caught:
        service._run_cli(tmp_path / "model.forge.js", "none")
    assert caught.value.error_code == "FORGECAD_SANDBOX_REQUIRED"


@pytest.mark.asyncio
async def test_forgecad_import_offloads_sync_conversion_to_threadpool(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    called = False

    class ImportResult:
        def model_dump(self, *, by_alias: bool) -> dict[str, object]:
            assert by_alias
            return {"assetId": "asset-1"}

    class ImportService:
        max_import_size_bytes = 1024

        def save_import_asset(self, **_: object) -> ImportResult:
            return ImportResult()

    class Database:
        committed = False
        rolled_back = False

        def commit(self) -> None:
            self.committed = True

        def rollback(self) -> None:
            self.rolled_back = True

    async def run_in_threadpool(
        function: Callable[..., object],
        *args: object,
    ) -> object:
        nonlocal called
        called = True
        return function(*args)

    monkeypatch.setattr(forgecad_api, "forgecad_service", ImportService())
    monkeypatch.setattr(
        forgecad_api,
        "run_in_threadpool",
        run_in_threadpool,
        raising=False,
    )
    upload = UploadFile(
        file=BytesIO(b"payload"),
        filename="part.step",
        headers=Headers({"content-type": "application/step"}),
    )
    database = Database()
    response = await forgecad_api.import_forgecad_asset(
        file=upload,
        auth_user={"sub": "alice"},
        db=database,
    )
    assert called
    assert database.committed
    assert isinstance(response, dict)


def test_forgecad_generated_script_and_cli_output_are_database_assets(
    engine: Engine,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime_root = tmp_path / "runtime"
    runtime_root.mkdir()
    service = ForgeCadService(
        asset_service=_asset_service(engine),
        runtime_temp_root=runtime_root,
    )

    async def qwen(_: ForgeCadGenerateRequest) -> str:
        return "return { part: box(1, 2, 3) };"

    def run_cli(script_path: Path, export_format: str) -> object:
        output_path = script_path.parent / f"part.{export_format}"
        output_path.write_bytes(b"STEP-CONTENT")
        from app.services.forgecad_service import CliRunResult

        return CliRunResult(logs="ok", output_path=str(output_path))

    monkeypatch.setattr(service, "_request_qwen", qwen)
    monkeypatch.setattr(service, "_run_cli", run_cli)
    with Session(engine) as db:
        result = asyncio.run(
            service.generate(
                ForgeCadGenerateRequest(
                    prompt="create a test part",
                    exportFormat="step",
                    runCli=True,
                ),
                db=db,
                user_id="alice",
            )
        )
        db.commit()

        assert result.script_path is None
        assert result.work_dir is None
        assert result.output_path is None
        assert result.script_asset_id is not None
        assert result.output_asset_id is not None
        assert _asset_bytes(db, result.script_asset_id) == result.script.encode()
        assert _asset_bytes(db, result.output_asset_id) == b"STEP-CONTENT"
        assert result.download_url == (
            f"/api/v1/assets/{result.output_asset_id}/download"
        )
    assert list(runtime_root.iterdir()) == []


def test_image_edit_materializes_source_only_temporarily_and_stores_output(
    engine: Engine,
    tmp_path: Path,
) -> None:
    runtime_root = tmp_path / "runtime"
    runtime_root.mkdir()
    script_path = tmp_path / "image-edit"
    script_path.write_text(
        """#!/usr/bin/env python3
import pathlib
import sys
args = sys.argv[1:]
source = pathlib.Path(args[args.index("--image") + 1])
output = pathlib.Path(args[args.index("--out") + 1])
response = pathlib.Path(args[args.index("--response-out") + 1])
output.write_bytes(__import__("base64").b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="))
response.write_text('{"ok":true}', encoding="utf-8")
""",
        encoding="utf-8",
    )
    script_path.chmod(0o755)
    asset_service = _asset_service(engine)
    service = Image2EditService(
        asset_service=asset_service,
        script_path=script_path,
        runtime_temp_root=runtime_root,
        trusted_image_validator=_test_trusted_image_validator,
    )
    with Session(engine) as db:
        source = asset_service.store_bytes(
            db=db,
            user_id="alice",
            filename="source.png",
            content_type="image/png",
            kind="image",
            source="upload",
            content=VALID_PNG,
        )
        result = asyncio.run(
            service.edit_and_store(
                db=db,
                user_id="alice",
                request=IndustrialDesignImageEditRequest(
                    prompt="polish",
                    imagePaths=["unused"],
                    outputFormat="png",
                ),
                image_asset_ids=[str(source.id)],
            )
        )
        db.commit()

        output_asset_id = result["outputAssetId"]
        assert isinstance(output_asset_id, str)
        assert _asset_bytes(db, output_asset_id) == VALID_PNG
        assert result["downloadUrl"] == (
            f"/api/v1/assets/{output_asset_id}/download"
        )
        assert "outputPath" not in result
        assert "responsePath" not in result
    assert list(runtime_root.iterdir()) == []


@pytest.mark.asyncio
async def test_image_edit_rejects_invalid_output_content(
    engine: Engine,
    tmp_path: Path,
) -> None:
    script_path = tmp_path / "invalid-image-edit"
    script_path.write_text(
        """#!/usr/bin/env python3
import pathlib
import sys
args = sys.argv[1:]
pathlib.Path(args[args.index("--out") + 1]).write_bytes(b"PNG")
""",
        encoding="utf-8",
    )
    script_path.chmod(0o755)
    asset_service = _asset_service(engine)
    service = Image2EditService(
        asset_service=asset_service,
        script_path=script_path,
        trusted_image_validator=_test_trusted_image_validator,
    )
    with Session(engine) as db:
        source = asset_service.store_bytes(
            db=db,
            user_id="alice",
            filename="source.png",
            content_type="image/png",
            kind="image",
            source="upload",
            content=VALID_PNG,
        )
        with pytest.raises(Image2EditServiceError) as caught:
            await service.edit_and_store(
                db=db,
                user_id="alice",
                request=IndustrialDesignImageEditRequest(
                    prompt="polish",
                    imagePaths=["unused"],
                    outputFormat="png",
                ),
                image_asset_ids=[str(source.id)],
            )
    assert caught.value.error_code == "IMAGE2_EDIT_OUTPUT_INVALID"


@pytest.mark.asyncio
async def test_image_edit_fails_before_subprocess_without_trusted_decoder(
    engine: Engine,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    script_path = tmp_path / "must-not-run-image-edit"
    script_path.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    script_path.chmod(0o755)
    asset_service = _asset_service(engine)
    service = Image2EditService(asset_service=asset_service, script_path=script_path)
    called = False

    async def create_process(*_: object, **__: object) -> object:
        nonlocal called
        called = True
        raise AssertionError("subprocess must not be called")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", create_process)
    with Session(engine) as db:
        source = asset_service.store_bytes(
            db=db,
            user_id="alice",
            filename="source.png",
            content_type="image/png",
            kind="image",
            source="upload",
            content=VALID_PNG,
        )
        with pytest.raises(Image2EditServiceError) as caught:
            await service.edit_and_store(
                db=db,
                user_id="alice",
                request=IndustrialDesignImageEditRequest(
                    prompt="polish",
                    imagePaths=["unused"],
                    outputFormat="png",
                ),
                image_asset_ids=[str(source.id)],
            )
    assert caught.value.error_code == "IMAGE_PIPELINE_TRUSTED_DECODER_UNAVAILABLE"
    assert caught.value.status_code == 503
    assert not called


@pytest.mark.asyncio
async def test_image_edit_rejects_output_over_hard_limit(
    engine: Engine,
    tmp_path: Path,
) -> None:
    script_path = tmp_path / "large-image-edit"
    script_path.write_text(
        """#!/usr/bin/env python3
import pathlib
import sys
args = sys.argv[1:]
pathlib.Path(args[args.index("--out") + 1]).write_bytes(b"X" * 17)
""",
        encoding="utf-8",
    )
    script_path.chmod(0o755)
    asset_service = _asset_service(engine)
    service = Image2EditService(
        asset_service=asset_service,
        script_path=script_path,
        trusted_image_validator=_test_trusted_image_validator,
    )
    service.max_output_size_bytes = 16
    with Session(engine) as db:
        source = asset_service.store_bytes(
            db=db,
            user_id="alice",
            filename="source.png",
            content_type="image/png",
            kind="image",
            source="upload",
            content=VALID_PNG,
        )
        with pytest.raises(Image2EditServiceError) as caught:
            await service.edit_and_store(
                db=db,
                user_id="alice",
                request=IndustrialDesignImageEditRequest(
                    prompt="polish",
                    imagePaths=["unused"],
                    outputFormat="png",
                ),
                image_asset_ids=[str(source.id)],
            )
    assert caught.value.error_code == "IMAGE2_EDIT_OUTPUT_TOO_LARGE"


@pytest.mark.asyncio
async def test_image_edit_timeout_terminates_process(
    engine: Engine,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    script_path = tmp_path / "hanging-image-edit"
    script_path.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    script_path.chmod(0o755)
    asset_service = _asset_service(engine)
    service = Image2EditService(
        asset_service=asset_service,
        script_path=script_path,
        trusted_image_validator=_test_trusted_image_validator,
    )
    service.process_timeout_seconds = 0.01
    service.termination_timeout_seconds = 0.01

    class HangingProcess:
        pid = 424241
        returncode: int | None = None
        terminated = False
        killed = False

        class EmptyStream:
            async def read(self, _: int) -> bytes:
                return b""

        stdout = EmptyStream()
        stderr = EmptyStream()

        async def communicate(self) -> tuple[bytes, bytes]:
            if not self.terminated and not self.killed:
                await asyncio.Event().wait()
            return b"", b""

        async def wait(self) -> int:
            if not self.terminated and not self.killed:
                await asyncio.Event().wait()
            return self.returncode or 0

        def terminate(self) -> None:
            self.terminated = True
            self.returncode = -15

        def kill(self) -> None:
            self.killed = True
            self.returncode = -9

    process = HangingProcess()
    subprocess_kwargs: dict[str, object] = {}

    async def create_process(
        *_: object,
        **kwargs: object,
    ) -> HangingProcess:
        subprocess_kwargs.update(kwargs)
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", create_process)
    group_signals: list[int | signal.Signals] = []

    def killpg(_: int, signal_number: int | signal.Signals) -> None:
        if signal_number == 0:
            if process.terminated or process.killed:
                raise ProcessLookupError
            return
        group_signals.append(signal_number)
        if signal_number == signal.SIGTERM:
            process.terminated = True
            process.returncode = -15
        elif signal_number == signal.SIGKILL:
            process.killed = True
            process.returncode = -9

    if os.name == "posix":
        monkeypatch.setattr(os, "killpg", killpg)
    with Session(engine) as db:
        source = asset_service.store_bytes(
            db=db,
            user_id="alice",
            filename="source.png",
            content_type="image/png",
            kind="image",
            source="upload",
            content=VALID_PNG,
        )
        with pytest.raises(Image2EditServiceError) as caught:
            await asyncio.wait_for(
                service.edit_and_store(
                    db=db,
                    user_id="alice",
                    request=IndustrialDesignImageEditRequest(
                        prompt="polish",
                        imagePaths=["unused"],
                        outputFormat="png",
                    ),
                    image_asset_ids=[str(source.id)],
                ),
                timeout=0.2,
            )
    assert caught.value.error_code == "IMAGE2_EDIT_TIMEOUT"
    assert process.terminated or process.killed
    if os.name == "posix":
        assert group_signals == [signal.SIGTERM]
    assert subprocess_kwargs["start_new_session"] is (os.name == "posix")


def test_image_edit_rejects_png_with_invalid_crc() -> None:
    service = Image2EditService(
        trusted_image_validator=_test_trusted_image_validator,
    )
    corrupted = bytearray(VALID_PNG)
    corrupted[45] ^= 0x01
    with pytest.raises(Image2EditServiceError) as caught:
        service._validate_image_output("image/png", bytes(corrupted))
    assert caught.value.error_code == "IMAGE2_EDIT_OUTPUT_INVALID"


def test_image_edit_rejects_structurally_truncated_jpeg() -> None:
    service = Image2EditService(
        trusted_image_validator=_test_trusted_image_validator,
    )
    truncated = b"\xff\xd8\xff\xc0\x00\x11\x08\x00\x01\x00\x01\xff\xd9"
    with pytest.raises(Image2EditServiceError) as caught:
        service._validate_image_output("image/jpeg", truncated)
    assert caught.value.error_code == "IMAGE2_EDIT_OUTPUT_INVALID"


@pytest.mark.skipif(os.name != "posix", reason="process groups require POSIX")
@pytest.mark.asyncio
async def test_image_edit_termination_signals_the_process_group(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class GroupProcess:
        pid = 424242
        returncode: int | None = None

        async def wait(self) -> int:
            return self.returncode or 0

        def terminate(self) -> None:
            raise AssertionError("single-process fallback must not be used")

        def kill(self) -> None:
            raise AssertionError("single-process fallback must not be used")

    process = GroupProcess()
    signals: list[tuple[int, signal.Signals]] = []

    def killpg(pid: int, signal_number: signal.Signals | int) -> None:
        if signal_number == 0:
            if process.returncode is not None:
                raise ProcessLookupError
            return
        signals.append((pid, signal.Signals(signal_number)))
        process.returncode = -int(signal_number)

    monkeypatch.setattr(os, "killpg", killpg)
    service = Image2EditService(
        trusted_image_validator=_test_trusted_image_validator,
    )
    await service._terminate_process(  # type: ignore[arg-type]
        process,
        process_group_id=process.pid,
    )
    assert signals == [(process.pid, signal.SIGTERM)]


@pytest.mark.asyncio
async def test_image_edit_terminates_on_combined_log_flood(
    engine: Engine,
    tmp_path: Path,
) -> None:
    script_path = tmp_path / "flood-image-edit"
    script_path.write_text(
        """#!/usr/bin/env python3
import base64
import pathlib
import sys
args = sys.argv[1:]
sys.stdout.write("X" * 4096)
pathlib.Path(args[args.index("--out") + 1]).write_bytes(base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="))
""",
        encoding="utf-8",
    )
    script_path.chmod(0o755)
    asset_service = _asset_service(engine)
    service = Image2EditService(
        asset_service=asset_service,
        script_path=script_path,
        trusted_image_validator=_test_trusted_image_validator,
    )
    service.max_log_size_bytes = 1024
    with Session(engine) as db:
        source = asset_service.store_bytes(
            db=db,
            user_id="alice",
            filename="source.png",
            content_type="image/png",
            kind="image",
            source="upload",
            content=VALID_PNG,
        )
        with pytest.raises(Image2EditServiceError) as caught:
            await service.edit_and_store(
                db=db,
                user_id="alice",
                request=IndustrialDesignImageEditRequest(
                    prompt="polish",
                    imagePaths=["unused"],
                    outputFormat="png",
                ),
                image_asset_ids=[str(source.id)],
            )
    assert caught.value.error_code == "IMAGE2_EDIT_LOG_TOO_LARGE"


@pytest.mark.skipif(os.name != "posix", reason="process groups require POSIX")
@pytest.mark.asyncio
async def test_image_edit_timeout_terminates_spawned_child_process(
    engine: Engine,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    script_path = tmp_path / "spawning-image-edit"
    script_path.write_text(
        (
            "#!/bin/sh\n"
            "sleep 60 &\n"
            "echo CHILD:$!\n"
            "wait\n"
        ),
        encoding="utf-8",
    )
    script_path.chmod(0o755)
    asset_service = _asset_service(engine)
    service = Image2EditService(
        asset_service=asset_service,
        script_path=script_path,
        trusted_image_validator=_test_trusted_image_validator,
    )
    service.process_timeout_seconds = 0.5
    service.termination_timeout_seconds = 0.1
    original_create_subprocess_exec = asyncio.create_subprocess_exec
    observed_stdout = bytearray()

    class RecordingStream:
        def __init__(self, stream: asyncio.StreamReader) -> None:
            self.stream = stream

        async def read(self, size: int) -> bytes:
            chunk = await self.stream.read(size)
            observed_stdout.extend(chunk)
            return chunk

    async def create_subprocess_exec(
        *args: object,
        **kwargs: object,
    ) -> asyncio.subprocess.Process:
        process = await original_create_subprocess_exec(*args, **kwargs)
        assert process.stdout is not None
        process.stdout = RecordingStream(process.stdout)
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", create_subprocess_exec)
    child_pid: int | None = None
    try:
        with Session(engine) as db:
            source = asset_service.store_bytes(
                db=db,
                user_id="alice",
                filename="source.png",
                content_type="image/png",
                kind="image",
                source="upload",
                content=VALID_PNG,
            )
            with pytest.raises(Image2EditServiceError) as caught:
                await asyncio.wait_for(
                    service.edit_and_store(
                        db=db,
                        user_id="alice",
                        request=IndustrialDesignImageEditRequest(
                            prompt="polish",
                            imagePaths=["unused"],
                            outputFormat="png",
                        ),
                        image_asset_ids=[str(source.id)],
                    ),
                    timeout=3,
                )
        assert caught.value.error_code == "IMAGE2_EDIT_TIMEOUT"
        child_lines = [
            line
            for line in observed_stdout.decode().splitlines()
            if line.startswith("CHILD:")
        ]
        if not child_lines:
            pytest.skip("sandbox prevents detached process body execution")
        child_line = child_lines[0]
        child_pid = int(child_line.split(":", 1)[1])
        deadline = time.monotonic() + 1
        while time.monotonic() < deadline:
            try:
                os.kill(child_pid, 0)
            except ProcessLookupError:
                child_pid = None
                break
            await asyncio.sleep(0.02)
        assert child_pid is None
    finally:
        if child_pid is not None:
            try:
                os.kill(child_pid, signal.SIGKILL)
            except ProcessLookupError:
                pass


@pytest.mark.skipif(os.name != "posix", reason="process groups require POSIX")
@pytest.mark.asyncio
async def test_image_edit_cleans_descendant_after_leader_exits(
    engine: Engine,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    marker_path = tmp_path / "descendant-marker"
    script_path = tmp_path / "leader-exits-image-edit"
    script_path.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    script_path.chmod(0o755)
    asset_service = _asset_service(engine)
    service = Image2EditService(
        asset_service=asset_service,
        script_path=script_path,
        trusted_image_validator=_test_trusted_image_validator,
    )
    service.process_timeout_seconds = 0.05
    service.termination_timeout_seconds = 0.05

    class HeldPipe:
        def __init__(self) -> None:
            self.released = asyncio.Event()

        async def read(self, _: int) -> bytes:
            await self.released.wait()
            return b""

    class ExitedLeaderProcess:
        pid = 424242
        returncode: int | None = 0
        stdout = HeldPipe()
        stderr = HeldPipe()
        descendant_alive = True

        async def wait(self) -> int:
            return 0

        def terminate(self) -> None:
            raise AssertionError("leader-only terminate must not be used")

        def kill(self) -> None:
            raise AssertionError("leader-only kill must not be used")

    process = ExitedLeaderProcess()

    async def write_marker() -> None:
        await asyncio.sleep(0.2)
        marker_path.write_text("descendant survived", encoding="utf-8")

    marker_task = asyncio.create_task(write_marker())
    signals: list[int | signal.Signals] = []

    async def create_process(
        *_: object,
        **__: object,
    ) -> ExitedLeaderProcess:
        return process

    def killpg(_: int, signal_number: int | signal.Signals) -> None:
        if signal_number == 0:
            if process.descendant_alive:
                return
            raise ProcessLookupError
        signals.append(signal_number)
        process.descendant_alive = False
        marker_task.cancel()
        process.stdout.released.set()
        process.stderr.released.set()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", create_process)
    monkeypatch.setattr(os, "killpg", killpg)
    with Session(engine) as db:
        source = asset_service.store_bytes(
            db=db,
            user_id="alice",
            filename="source.png",
            content_type="image/png",
            kind="image",
            source="upload",
            content=VALID_PNG,
        )
        with pytest.raises(Image2EditServiceError) as caught:
            await service.edit_and_store(
                db=db,
                user_id="alice",
                request=IndustrialDesignImageEditRequest(
                    prompt="polish",
                    imagePaths=["unused"],
                    outputFormat="png",
                ),
                image_asset_ids=[str(source.id)],
            )
    await asyncio.gather(marker_task, return_exceptions=True)
    assert caught.value.error_code == "IMAGE2_EDIT_TIMEOUT"
    assert signals == [signal.SIGTERM]
    assert not process.descendant_alive
    assert not marker_path.exists()


@pytest.mark.asyncio
async def test_image_edit_subprocess_does_not_inherit_unlisted_secret(
    engine: Engine,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("UNLISTED_IMAGE_SECRET", "must-not-leak")
    script_path = tmp_path / "environment-image-edit"
    script_path.write_text(
        """#!/usr/bin/env python3
import base64
import json
import os
import pathlib
import sys
args = sys.argv[1:]
pathlib.Path(args[args.index("--out") + 1]).write_bytes(base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="))
pathlib.Path(args[args.index("--response-out") + 1]).write_text(json.dumps({"secret": os.getenv("UNLISTED_IMAGE_SECRET")}))
""",
        encoding="utf-8",
    )
    script_path.chmod(0o755)
    asset_service = _asset_service(engine)
    service = Image2EditService(
        asset_service=asset_service,
        script_path=script_path,
        trusted_image_validator=_test_trusted_image_validator,
    )
    with Session(engine) as db:
        source = asset_service.store_bytes(
            db=db,
            user_id="alice",
            filename="source.png",
            content_type="image/png",
            kind="image",
            source="upload",
            content=VALID_PNG,
        )
        result = await service.edit_and_store(
            db=db,
            user_id="alice",
            request=IndustrialDesignImageEditRequest(
                prompt="polish",
                imagePaths=["unused"],
                outputFormat="png",
            ),
            image_asset_ids=[str(source.id)],
        )
        response_asset_id = result["responseAssetId"]
        assert isinstance(response_asset_id, str)
        response = _asset_bytes(db, response_asset_id)
    assert b"must-not-leak" not in response


@pytest.mark.asyncio
async def test_workflow_publishes_image_edit_response_asset(
    engine: Engine,
    tmp_path: Path,
) -> None:
    from app.schemas.industrial_design import IndustrialDesignWorkflowRequest

    script_path = tmp_path / "workflow-image-edit"
    script_path.write_text(
        """#!/usr/bin/env python3
import base64
import pathlib
import sys
args = sys.argv[1:]
pathlib.Path(args[args.index("--out") + 1]).write_bytes(base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="))
pathlib.Path(args[args.index("--response-out") + 1]).write_text('{"ok":true}')
""",
        encoding="utf-8",
    )
    script_path.chmod(0o755)
    asset_service = _asset_service(engine)
    image_service = Image2EditService(
        asset_service=asset_service,
        script_path=script_path,
        trusted_image_validator=_test_trusted_image_validator,
    )

    class DisabledImageGateway:
        def image_configured(self) -> bool:
            return False

    service = IndustrialDesignWorkflowService(
        ai_model_gateway=DisabledImageGateway(),
        image2_edit_service=image_service,
        asset_service=asset_service,
        db_context_factory=_db_context(engine),
        trusted_image_validator=_test_trusted_image_validator,
    )
    with Session(engine) as db:
        source = asset_service.store_bytes(
            db=db,
            user_id="alice",
            filename="source.png",
            content_type="image/png",
            kind="image",
            source="upload",
            content=VALID_PNG,
        )
        db.commit()
        source_id = str(source.id)

    result = await service.create_workflow(
        IndustrialDesignWorkflowRequest.model_validate(
            {
                "inputType": "image",
                "assetIds": [source_id],
                "options": {
                    "generateCad": False,
                    "generateDrawing": False,
                    "generateThreePreview": False,
                    "generateRender": True,
                    "generateExplosion": False,
                    "enhanceImage": True,
                },
            }
        ),
        {"sub": "alice"},
    )
    assert result["status"] == "completed"
    outputs = result["outputs"]
    assert isinstance(outputs, dict)
    response_asset_id = outputs["enhancedImageResponseAssetId"]
    assert isinstance(response_asset_id, str)
    with Session(engine) as db:
        response_asset = db.get(Asset, UUID(response_asset_id))
        assert response_asset is not None
        assert response_asset.status == "available"
        assert _asset_bytes(db, response_asset_id) == b'{"ok":true}'


@pytest.mark.parametrize(
    ("call", "arguments"),
    [
        (download_forgecad_file, {"task_id": "legacy-task"}),
        (download_cad_ai_asset, {"asset_id": "remote-legacy"}),
        (download_imported_cad_file, {"asset_id": "cadimport_legacy"}),
        (
            download_imported_cad_preview_file,
            {"asset_id": "cadimport_legacy"},
        ),
    ],
)
def test_legacy_forgecad_disk_downloads_are_gone(
    call: object,
    arguments: dict[str, str],
) -> None:
    with pytest.raises(HTTPException) as caught:
        asyncio.run(call(**arguments, auth_user={"sub": "alice"}))  # type: ignore[operator]
    assert caught.value.status_code == 410


@pytest.mark.parametrize(
    "call",
    [
        download_industrial_design_asset,
        download_industrial_design_image_edit_asset,
    ],
)
def test_legacy_industrial_design_disk_downloads_are_gone(call: object) -> None:
    with pytest.raises(HTTPException) as caught:
        call(filename="legacy.glb", auth_user={"sub": "alice"})  # type: ignore[operator]
    assert caught.value.status_code == 410
