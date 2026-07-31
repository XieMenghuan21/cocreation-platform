from __future__ import annotations

import asyncio
from collections.abc import Iterator
from contextlib import contextmanager

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

import app.api.v1.industrial_design as industrial_design_api
from app.api.deps import require_auth
from app.api.v1.router import router
from app.config.settings import settings
from app.db.session import Base, get_db
from app.models.persistence import WorkflowTask
from app.schemas.industrial_design import IndustrialDesignWorkflowRequest
from app.services.industrial_design_workflow_service import IndustrialDesignWorkflowService


class DisabledGateway:
    base_url = ""


class DisabledAiGateway:
    def image_configured(self) -> bool:
        return False


def test_api_get_reads_task_after_service_restart(tmp_path, monkeypatch) -> None:
    engine = create_engine(
        f"sqlite:///{tmp_path / 'workflow-api.db'}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)

    @contextmanager
    def db_context() -> Iterator[Session]:
        session = factory()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    request = IndustrialDesignWorkflowRequest.model_validate(
        {
            "inputType": "text",
            "text": "restart persistence",
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
    first = IndustrialDesignWorkflowService(
        cad_ai_gateway=DisabledGateway(),
        ai_model_gateway=DisabledAiGateway(),
        db_context_factory=db_context,
    )
    created = asyncio.run(first.create_workflow(request, auth_user={"sub": "api-user"}))
    rebuilt = IndustrialDesignWorkflowService(
        cad_ai_gateway=DisabledGateway(),
        ai_model_gateway=DisabledAiGateway(),
        db_context_factory=db_context,
    )
    monkeypatch.setattr(
        industrial_design_api,
        "industrial_design_workflow_service",
        rebuilt,
    )
    app = FastAPI()
    app.include_router(router, prefix="/api/v1")
    current_user = {"sub": "api-user"}
    app.dependency_overrides[require_auth] = lambda: current_user

    def override_db() -> Iterator[Session]:
        with factory() as session:
            yield session

    app.dependency_overrides[get_db] = override_db

    with TestClient(app) as client:
        owner_response = client.get(
            f"/api/v1/industrial-design/workflows/{created['taskId']}"
        )
        current_user["sub"] = "other-user"
        other_response = client.get(
            f"/api/v1/industrial-design/workflows/{created['taskId']}"
        )

    assert owner_response.status_code == 200
    payload = owner_response.json()
    assert payload["data"]["taskId"] == created["taskId"]
    assert payload["data"]["status"] == "completed"
    assert other_response.status_code == 404
    assert other_response.json()["errorCode"] == "INDUSTRIAL_DESIGN_TASK_NOT_FOUND"
    engine.dispose()


def test_legacy_file_download_returns_gone() -> None:
    expected_url = f"{settings.API_V1_PREFIX}/industrial-design/assets/owned.glb"
    current_user = {"sub": "alice"}
    app = FastAPI()
    app.include_router(router, prefix="/api/v1")
    app.dependency_overrides[require_auth] = lambda: current_user
    with TestClient(app) as client:
        response = client.get(expected_url)

    assert response.status_code == 410
