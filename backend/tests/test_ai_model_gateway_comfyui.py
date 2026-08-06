from __future__ import annotations

import asyncio

import pytest

from app.services.ai_model_gateway_service import AIModelGatewayService
from app.services.comfyui_image_service import ComfyUIImageServiceError

FAKE_COMFYUI_RESULT = {
    "taskId": "comfyui_x.png",
    "model": "FLUX.1-schnell (ComfyUI)",
    "resultUrl": "http://127.0.0.1:8188/view?filename=x.png&type=output",
    "status": "completed",
    "raw": {"filename": "x.png"},
}


class FakeComfyUI:
    configured = True

    async def generate_design_image(self, **_: object) -> dict[str, object]:
        return dict(FAKE_COMFYUI_RESULT)


class UnconfiguredComfyUI:
    configured = False


def _make_gateway(comfyui_service: object) -> AIModelGatewayService:
    return AIModelGatewayService(comfyui_image_service=comfyui_service)


def test_comfyui_configured_reflected_in_health() -> None:
    gateway = _make_gateway(FakeComfyUI())
    image_health = gateway.health()["image"]
    assert image_health["comfyuiConfigured"] is True

    gateway2 = _make_gateway(UnconfiguredComfyUI())
    assert gateway2.health()["image"]["comfyuiConfigured"] is False


def test_image_configured_returns_true_when_only_comfyui() -> None:
    gateway = _make_gateway(FakeComfyUI())
    assert gateway.image_configured() is True
    assert gateway.comfyui_image_configured() is True


def test_auto_image_provider_prefers_comfyui() -> None:
    gateway = _make_gateway(FakeComfyUI())
    provider, model = gateway._resolve_auto_image_provider()
    assert provider == "comfyui"
    assert model is None


def test_auto_image_provider_without_comfyui_uses_next_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    gateway = _make_gateway(UnconfiguredComfyUI())
    provider, _model = gateway._resolve_auto_image_provider()
    assert provider != "comfyui"


def test_normalize_provider_maps_comfyui_aliases() -> None:
    for alias in ("comfyui", "comfy", "flux", "sd", "stable-diffusion"):
        assert AIModelGatewayService._normalize_provider(alias) == "comfyui"


def test_generate_design_image_explicit_provider_comfyui() -> None:
    gateway = _make_gateway(FakeComfyUI())
    result = asyncio.run(
        gateway.generate_design_image(prompt="a chair", provider="comfyui", optimize_prompt=False)
    )
    assert result["provider"] == "comfyui"
    assert str(result["resultUrl"]).startswith("http://127.0.0.1:8188")


def test_generate_design_image_auto_retry_falls_back_after_comfyui_error(monkeypatch: pytest.MonkeyPatch) -> None:
    class FailingComfyUI(FakeComfyUI):
        async def generate_design_image(self, **_: object) -> dict[str, object]:
            raise ComfyUIImageServiceError("ComfyUI 服务不可用", "COMFYUI_UNREACHABLE", status_code=503)

    class BackupNodapi:
        configured = True

        async def generate_design_image(self, **_: object) -> dict[str, object]:
            return {"taskId": "n-1", "resultUrl": "http://nodapi/x.png", "status": "completed"}

    gateway = AIModelGatewayService(
        comfyui_image_service=FailingComfyUI(),
        nodapi_image_service=BackupNodapi(),
        dashscope_image_service=UnconfiguredComfyUI(),
    )
    result = asyncio.run(
        gateway.generate_design_image(prompt="a chair", optimize_prompt=False)
    )
    assert result["provider"] == "nodapi"
    assert str(result["resultUrl"]) == "http://nodapi/x.png"


def test_generate_design_image_explicit_comfyui_error_surfaces() -> None:
    class FailingComfyUI(FakeComfyUI):
        async def generate_design_image(self, **_: object) -> dict[str, object]:
            raise ComfyUIImageServiceError("ComfyUI 未配置", "COMFYUI_NOT_CONFIGURED", status_code=503)

    gateway = _make_gateway(FailingComfyUI())
    with pytest.raises(ComfyUIImageServiceError) as exc:
        asyncio.run(
            gateway.generate_design_image(prompt="a chair", provider="comfyui", optimize_prompt=False)
        )
    assert exc.value.error_code == "COMFYUI_NOT_CONFIGURED"
    assert "ComfyUI" in exc.value.message