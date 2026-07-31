from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.config.settings import settings
from app.core.middleware import setup_middleware
from app.schemas.cocreation_history import VersionSnapshotPayload
from app.schemas.industrial_design import IndustrialDesignWorkflowRequest


@pytest.mark.parametrize(
    "path",
    [
        "/api/v1/industrial-design/workflows",
        "/api/v1/cocreation-history/projects/upsert-with-version",
    ],
)
def test_workflow_and_history_json_have_independent_body_limit(
    monkeypatch: pytest.MonkeyPatch,
    path: str,
) -> None:
    monkeypatch.setattr(settings, "JSON_REQUEST_MAX_BYTES", 64)
    app = FastAPI()
    setup_middleware(app)

    with TestClient(app) as client:
        response = client.post(
            path,
            content=b'{"value":"' + (b"x" * 100) + b'"}',
                headers={
                    "content-type": "application/json",
                    "origin": "http://localhost:5174",
                },
        )
    assert response.status_code == 413


def test_recursive_json_shape_rejects_deep_and_excessive_metadata() -> None:
    nested: dict[str, object] = {}
    cursor = nested
    for _ in range(10):
        child: dict[str, object] = {}
        cursor["child"] = child
        cursor = child
    with pytest.raises(ValidationError):
        IndustrialDesignWorkflowRequest.model_validate(
            {"inputType": "text", "context": nested}
        )

    with pytest.raises(ValidationError):
        VersionSnapshotPayload.model_validate(
            {
                "id": "v1",
                "label": "v1",
                "status": "done",
                "note": "",
                "diagnostics": [{"index": index} for index in range(201)],
            }
        )


def test_recursive_json_shape_accepts_boundary_payload() -> None:
    request = IndustrialDesignWorkflowRequest.model_validate(
        {
            "inputType": "text",
            "context": {"a": {"b": {"c": "ok"}}},
            "assetIds": ["asset-1"],
        }
    )
    assert request.context["a"] == {"b": {"c": "ok"}}
