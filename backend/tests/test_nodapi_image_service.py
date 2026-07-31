from __future__ import annotations

import asyncio

import pytest

from app.services.curl_request import CurlRequestError, CurlResponse
from app.services.nodapi_image_service import NodApiImageService, NodApiImageServiceError


def test_join_result_url_avoids_duplicate_v1_prefix() -> None:
    result = NodApiImageService._join_result_url(
        "http://127.0.0.1:8011/v1",
        "/v1/images/sd3_8f40fcfe_0.png/content",
    )

    assert result == "http://127.0.0.1:8011/v1/images/sd3_8f40fcfe_0.png/content"


def test_join_result_url_keeps_relative_path_without_v1() -> None:
    result = NodApiImageService._join_result_url(
        "http://127.0.0.1:8011/v1",
        "/images/sd3_8f40fcfe_0.png/content",
    )

    assert result == "http://127.0.0.1:8011/v1/images/sd3_8f40fcfe_0.png/content"


def test_join_result_url_keeps_absolute_url_unchanged() -> None:
    result = NodApiImageService._join_result_url(
        "http://127.0.0.1:8011/v1",
        "http://127.0.0.1:8011/v1/images/sd3_8f40fcfe_0.png/content",
    )

    assert result == "http://127.0.0.1:8011/v1/images/sd3_8f40fcfe_0.png/content"


def test_request_json_retries_after_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    service = NodApiImageService()
    service.base_url = "https://www.nodapi.com"
    service.api_key = "test-key"
    service.timeout_seconds = 1
    service.retry_count = 1
    attempts = {"count": 0}

    async def fake_request_json(**_: object) -> CurlResponse:
        attempts["count"] += 1
        if attempts["count"] == 1:
            raise CurlRequestError("curl 请求超时：https://www.nodapi.com/v1/images/generations", status_code=504)
        return CurlResponse(
            status_code=200,
            body='{"id":"task-1","data":[{"url":"https://cdn.example.com/image.png"}]}',
            stderr="",
        )

    monkeypatch.setattr("app.services.nodapi_image_service.request_json", fake_request_json)

    result = asyncio.run(service.generate_design_image(prompt="test prompt"))

    assert attempts["count"] == 2
    assert result["resultUrl"] == "https://cdn.example.com/image.png"


def test_request_json_raises_after_retry_budget_exhausted(monkeypatch: pytest.MonkeyPatch) -> None:
    service = NodApiImageService()
    service.base_url = "https://www.nodapi.com"
    service.api_key = "test-key"
    service.timeout_seconds = 1
    service.retry_count = 1
    attempts = {"count": 0}

    async def fake_request_json(**_: object) -> CurlResponse:
        attempts["count"] += 1
        raise CurlRequestError("curl 请求超时：https://www.nodapi.com/v1/images/generations", status_code=504)

    monkeypatch.setattr("app.services.nodapi_image_service.request_json", fake_request_json)

    with pytest.raises(NodApiImageServiceError) as exc:
        asyncio.run(service.generate_design_image(prompt="test prompt"))

    assert attempts["count"] == 2
    assert "重试" in exc.value.message
