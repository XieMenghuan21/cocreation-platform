from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from app.services.comfyui_service import (
    build_placeholder_svg,
    build_render_payload,
    download_image,
    is_comfyui_configured,
    poll_render,
    submit_render,
)


async def _noop_sleep(_seconds: float) -> None:
    return None


class FakeAsyncClient:
    """httpx.AsyncClient 极简桩。"""

    def __init__(self, *, post_handler: object = None, get_handler: object = None) -> None:
        self._post_handler = post_handler
        self._get_handler = get_handler

    async def __aenter__(self) -> FakeAsyncClient:
        return self

    async def __aexit__(self, *_: object) -> None:
        return None

    async def post(self, url: str, json: object = None) -> object:
        if self._post_handler is None:
            raise AssertionError("unexpected post")
        return await self._post_handler(url, json=json)  # type: ignore[operator]

    async def get(self, url: str, params: object = None) -> object:
        if self._get_handler is None:
            raise AssertionError("unexpected get")
        return await self._get_handler(url, params=params)  # type: ignore[operator]


def _ok_response(body: object, content: bytes | None = None) -> object:
    return SimpleNamespace(raise_for_status=lambda: None, json=lambda: body, content=content)


def _patch_client(monkeypatch: pytest.MonkeyPatch, client: FakeAsyncClient) -> None:
    monkeypatch.setattr("app.services.comfyui_service.httpx.AsyncClient", lambda **_: client)


# ---------- build_render_payload / is_comfyui_configured ----------


def test_build_payload_uses_degree_quick() -> None:
    payload = build_render_payload("QUICK", {"title": "北欧餐椅", "prompt": "x"})
    assert payload["steps"] == 20
    assert payload["width"] == 768
    assert payload["height"] == 768
    assert payload["seed"] == 0
    assert "negative_prompt" in payload


def test_build_payload_uses_degree_standard() -> None:
    payload = build_render_payload("STANDARD", {"title": "北欧餐椅", "prompt": "x"})
    assert payload["steps"] == 30
    assert payload["width"] == 1024
    assert payload["height"] == 1024


def test_build_payload_uses_degree_deep() -> None:
    payload = build_render_payload("DEEP", {"title": "北欧餐椅", "prompt": "x"})
    assert payload["steps"] == 50
    assert payload["width"] == 1344
    assert payload["height"] == 1344


def test_build_payload_unknown_degree_falls_back_to_standard() -> None:
    payload = build_render_payload("EXTREME", {"title": "x", "prompt": "y"})
    assert payload["steps"] == 30
    assert payload["width"] == 1024


def test_build_payload_prompt_falls_back_to_title() -> None:
    payload = build_render_payload("QUICK", {"title": "北欧餐椅"})
    assert payload["prompt"] == "北欧餐椅效果图"


def test_not_configured_returns_false(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.config.settings.settings.COMFYUI_BASE_URL", "")
    assert is_comfyui_configured() is False


def test_configured_returns_true(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.config.settings.settings.COMFYUI_BASE_URL", "http://127.0.0.1:8188")
    assert is_comfyui_configured() is True


def test_build_placeholder_svg_contains_title_and_marker() -> None:
    text = build_placeholder_svg("北欧餐椅").decode()
    assert "北欧餐椅" in text
    assert "本地占位" in text


# ---------- submit_render ----------


def test_submit_render_returns_prompt_id(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.config.settings.settings.COMFYUI_BASE_URL", "http://127.0.0.1:8188")

    async def fake_post(url: str, json: object = None) -> object:
        assert "/prompt" in url
        return _ok_response({"prompt_id": "abc-123"})

    _patch_client(monkeypatch, FakeAsyncClient(post_handler=fake_post))

    prompt_id = asyncio.run(submit_render({"prompt": "x"}))
    assert prompt_id == "abc-123"


def test_submit_render_raises_on_http_error(monkeypatch: pytest.MonkeyPatch) -> None:
    import httpx

    monkeypatch.setattr("app.config.settings.settings.COMFYUI_BASE_URL", "http://127.0.0.1:8188")

    async def fake_post(url: str, json: object = None) -> object:
        raise httpx.HTTPStatusError(
            "bad",
            request=httpx.Request("POST", "http://127.0.0.1:8188/prompt"),
            response=httpx.Response(502),
        )

    _patch_client(monkeypatch, FakeAsyncClient(post_handler=fake_post))

    with pytest.raises(httpx.HTTPStatusError):
        asyncio.run(submit_render({"prompt": "x"}))


# ---------- poll_render ----------


def test_poll_render_returns_images(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.config.settings.settings.COMFYUI_BASE_URL", "http://127.0.0.1:8188")

    async def fake_get(url: str, params: object = None) -> object:
        return _ok_response(
            {"abc-123": {"outputs": {"9": {"images": [{"filename": "out.png", "subfolder": "", "type": "output"}]}}}}
        )

    _patch_client(monkeypatch, FakeAsyncClient(get_handler=fake_get))

    result = asyncio.run(poll_render("abc-123", timeout_seconds=5))
    assert result["images"] == [{"filename": "out.png", "subfolder": "", "type": "output"}]


def test_poll_render_waits_until_images_appear(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.config.settings.settings.COMFYUI_BASE_URL", "http://127.0.0.1:8188")
    monkeypatch.setattr("app.services.comfyui_service.asyncio.sleep", _noop_sleep)
    attempts = {"n": 0}

    async def fake_get(url: str, params: object = None) -> object:
        attempts["n"] += 1
        if attempts["n"] == 1:
            return _ok_response({})
        return _ok_response({"abc-123": {"outputs": {"9": {"images": [{"filename": "out.png"}]}}}})

    _patch_client(monkeypatch, FakeAsyncClient(get_handler=fake_get))

    result = asyncio.run(poll_render("abc-123", timeout_seconds=5))
    assert result["images"][0]["filename"] == "out.png"
    assert attempts["n"] == 2


def test_poll_render_times_out(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.config.settings.settings.COMFYUI_BASE_URL", "http://127.0.0.1:8188")
    monkeypatch.setattr("app.services.comfyui_service.asyncio.sleep", _noop_sleep)

    async def fake_get(url: str, params: object = None) -> object:
        return _ok_response({})

    _patch_client(monkeypatch, FakeAsyncClient(get_handler=fake_get))

    with pytest.raises(TimeoutError):
        asyncio.run(poll_render("abc-123", timeout_seconds=0.1))


# ---------- download_image ----------


def test_download_image_returns_bytes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.config.settings.settings.COMFYUI_BASE_URL", "http://127.0.0.1:8188")

    async def fake_get(url: str, params: object = None) -> object:
        assert "/view" in url
        assert params == {"filename": "out.png", "subfolder": "", "type": "output"}
        return _ok_response({}, content=b"PNG-BYTES")

    _patch_client(monkeypatch, FakeAsyncClient(get_handler=fake_get))

    data = asyncio.run(download_image({"filename": "out.png", "subfolder": "", "type": "output"}))
    assert data == b"PNG-BYTES"
