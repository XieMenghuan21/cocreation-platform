from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from app.services.comfyui_image_service import (
    ComfyUIImageService,
    ComfyUIImageServiceError,
)


def _make_service(monkeypatch: pytest.MonkeyPatch) -> ComfyUIImageService:
    service = ComfyUIImageService()
    service.base_url = "http://127.0.0.1:8188"
    service.timeout_seconds = 10
    service.poll_interval = 0.01
    service.default_size = "1024x1024"
    return service


class FakeAsyncClient:
    """极简 httpx.AsyncClient 桩：按调用顺序返回预设响应。"""

    def __init__(self, responses: list[object]) -> None:
        self.responses = list(responses)
        self.calls: list[tuple[str, str, object]] = []

    async def __aenter__(self) -> FakeAsyncClient:
        return self

    async def __aexit__(self, *_: object) -> None:
        return None

    async def request(self, method: str, url: str, json: object = None) -> object:
        self.calls.append((method, url, json))
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response

    async def get(self, url: str) -> object:
        self.calls.append(("GET", url, None))
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response

    async def post(self, url: str, files: object = None, data: object = None) -> object:
        self.calls.append(("POST", url, {"files": files, "data": data}))
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def _response(status_code: int = 200, body: dict[str, object] | None = None) -> object:
    return SimpleNamespace(status_code=status_code, json=lambda: body or {}, content=b"fake-image-bytes")


def test_configured_requires_base_url() -> None:
    unconfigured = ComfyUIImageService()
    unconfigured.base_url = ""
    assert not unconfigured.configured

    configured = ComfyUIImageService()
    configured.base_url = "http://127.0.0.1:8188/"
    assert configured.configured


def test_parse_size_supports_plain_and_paired() -> None:
    assert ComfyUIImageService._parse_size("512x768") == (512, 768)
    assert ComfyUIImageService._parse_size(" 1024 x 1024 ") == (1024, 1024)
    assert ComfyUIImageService._parse_size("") == (1024, 1024)
    assert ComfyUIImageService._parse_size("oops") == (1024, 1024)


def test_build_workflow_text2img_has_no_load_image(monkeypatch: pytest.MonkeyPatch) -> None:
    service = _make_service(monkeypatch)
    workflow = service._build_workflow(prompt="a chair", input_filename=None, seed=7, steps=4, denoise=1.0, size="1024x1024")

    assert workflow["1"]["class_type"] == "UNETLoader"
    assert workflow["2"]["class_type"] == "DualCLIPLoader"
    assert workflow["3"]["class_type"] == "VAELoader"
    assert workflow["4"]["class_type"] == "EmptyLatentImage"
    assert workflow["4"]["inputs"]["width"] == 1024
    assert workflow["6"]["inputs"]["text"] == "a chair"
    assert workflow["7"]["class_type"] == "KSampler"
    assert workflow["7"]["inputs"]["seed"] == 7
    assert workflow["7"]["inputs"]["denoise"] == 1.0
    assert workflow["8"]["class_type"] == "VAEDecode"
    assert workflow["9"]["class_type"] == "SaveImage"


def test_build_workflow_img2img_uses_load_image_and_vae_encode(monkeypatch: pytest.MonkeyPatch) -> None:
    service = _make_service(monkeypatch)
    workflow = service._build_workflow(prompt="restyle", input_filename="ref.png", seed=3, steps=4, denoise=0.6, size="1024x1024")

    assert workflow["4"]["class_type"] == "LoadImage"
    assert workflow["4"]["inputs"]["image"] == "ref.png"
    assert workflow["5"]["class_type"] == "VAEEncode"
    assert workflow["7"]["inputs"]["latent_image"] == ["5", 0]
    assert workflow["7"]["inputs"]["denoise"] == 0.6


def test_generate_design_image_text2img_success(monkeypatch: pytest.MonkeyPatch) -> None:
    service = _make_service(monkeypatch)
    output_filename = "cocreation_00001_.png"
    async def fake_submit_and_wait(_workflow: dict[str, object]) -> str:
        return output_filename
    monkeypatch.setattr(service, "_submit_and_wait", fake_submit_and_wait)

    result = asyncio.run(service.generate_design_image(prompt="a wooden chair"))

    assert result["status"] == "completed"
    assert result["model"] == "FLUX.1-schnell (ComfyUI)"
    assert result["resultUrl"] == f"http://127.0.0.1:8188/view?filename={output_filename}&subfolder=&type=output"
    assert result["taskId"] == f"comfyui_{output_filename}"


def test_generate_design_image_img2img_uploads_reference(monkeypatch: pytest.MonkeyPatch) -> None:
    service = _make_service(monkeypatch)

    async def fake_upload(_image_url: str) -> str:
        return "ref.png"

    async def fake_submit_and_wait(workflow: dict[str, object]) -> str:
        assert workflow["4"]["class_type"] == "LoadImage"
        return "cocreation_00002_.png"

    monkeypatch.setattr(service, "_upload_image", fake_upload)
    monkeypatch.setattr(service, "_submit_and_wait", fake_submit_and_wait)

    result = asyncio.run(service.generate_design_image(prompt="restyle", images=["http://example.com/a.png"]))

    assert result["status"] == "completed"
    assert "cocreation_00002_.png" in result["resultUrl"]


def test_generate_design_image_unconfigured_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    service = _make_service(monkeypatch)
    service.base_url = ""
    with pytest.raises(ComfyUIImageServiceError) as exc:
        asyncio.run(service.generate_design_image(prompt="a chair"))
    assert exc.value.error_code == "COMFYUI_NOT_CONFIGURED"
    assert exc.value.status_code == 503


def test_generate_design_image_empty_prompt_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    service = _make_service(monkeypatch)
    with pytest.raises(ComfyUIImageServiceError) as exc:
        asyncio.run(service.generate_design_image(prompt="   "))
    assert exc.value.error_code == "COMFYUI_PROMPT_EMPTY"
    assert exc.value.status_code == 400


def test_generate_design_image_upload_failure_fails_before_submit(monkeypatch: pytest.MonkeyPatch) -> None:
    service = _make_service(monkeypatch)

    async def fake_upload(_image_url: str) -> str:
        raise ComfyUIImageServiceError("ComfyUI 图片上传失败", "COMFYUI_UPLOAD_FAILED", status_code=502)

    monkeypatch.setattr(service, "_upload_image", fake_upload)

    with pytest.raises(ComfyUIImageServiceError) as exc:
        asyncio.run(service.generate_design_image(prompt="restyle", images=["http://example.com/a.png"]))
    assert exc.value.error_code == "COMFYUI_UPLOAD_FAILED"


def test_submit_and_wait_polls_until_completed(monkeypatch: pytest.MonkeyPatch) -> None:
    service = _make_service(monkeypatch)
    calls: list[str] = []

    async def fake_request_json(method: str, path: str, payload: dict[str, object] | None = None) -> dict[str, object]:
        calls.append(path)
        if path == "/prompt":
            return {"prompt_id": "abc-123"}
        return {
            "abc-123": {
                "status": {"status_str": "success", "completed": True},
                "outputs": {"9": {"images": [{"type": "output", "filename": "cocreation_00003_.png"}]}},
            }
        }

    monkeypatch.setattr(service, "_request_json", fake_request_json)
    workflow = service._build_workflow(prompt="x", input_filename=None, seed=1, steps=4, denoise=1.0, size="1024x1024")

    filename = asyncio.run(service._submit_and_wait(workflow))

    assert filename == "cocreation_00003_.png"
    assert calls == ["/prompt", "/history/abc-123"]


def test_submit_and_wait_retries_history_until_complete(monkeypatch: pytest.MonkeyPatch) -> None:
    service = _make_service(monkeypatch)
    attempts = {"n": 0}

    async def fake_request_json(method: str, path: str, payload: dict[str, object] | None = None) -> dict[str, object]:
        if path == "/prompt":
            return {"prompt_id": "abc-123"}
        attempts["n"] += 1
        if attempts["n"] == 1:
            return {}  # 尚未入 history
        if attempts["n"] == 2:
            return {"abc-123": {"status": {"status_str": "success", "completed": False}}}
        return {
            "abc-123": {
                "status": {"status_str": "success", "completed": True},
                "outputs": {"9": {"images": [{"type": "output", "filename": "cocreation_00004_.png"}]}},
            }
        }

    monkeypatch.setattr(service, "_request_json", fake_request_json)
    workflow = service._build_workflow(prompt="x", input_filename=None, seed=1, steps=4, denoise=1.0, size="1024x1024")

    filename = asyncio.run(service._submit_and_wait(workflow))

    assert filename == "cocreation_00004_.png"
    assert attempts["n"] == 3


def test_submit_and_wait_raises_on_execution_error(monkeypatch: pytest.MonkeyPatch) -> None:
    service = _make_service(monkeypatch)

    async def fake_request_json(method: str, path: str, payload: dict[str, object] | None = None) -> dict[str, object]:
        if path == "/prompt":
            return {"prompt_id": "abc-123"}
        return {"abc-123": {"status": {"status_str": "error", "completed": True, "messages": ["boom"]}, "outputs": {}}}

    monkeypatch.setattr(service, "_request_json", fake_request_json)
    workflow = service._build_workflow(prompt="x", input_filename=None, seed=1, steps=4, denoise=1.0, size="1024x1024")

    with pytest.raises(ComfyUIImageServiceError) as exc:
        asyncio.run(service._submit_and_wait(workflow))
    assert exc.value.error_code == "COMFYUI_EXECUTION_ERROR"


def test_submit_and_wait_raises_on_node_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    service = _make_service(monkeypatch)

    async def fake_request_json(method: str, path: str, payload: dict[str, object] | None = None) -> dict[str, object]:
        return {"node_errors": {"1": {"errors": ["bad node"]}}}

    monkeypatch.setattr(service, "_request_json", fake_request_json)
    workflow = service._build_workflow(prompt="x", input_filename=None, seed=1, steps=4, denoise=1.0, size="1024x1024")

    with pytest.raises(ComfyUIImageServiceError) as exc:
        asyncio.run(service._submit_and_wait(workflow))
    assert exc.value.error_code == "COMFYUI_WORKFLOW_INVALID"
    assert exc.value.status_code == 502


def test_submit_and_wait_times_out_when_no_completion(monkeypatch: pytest.MonkeyPatch) -> None:
    service = _make_service(monkeypatch)
    service.timeout_seconds = 0.05

    async def fake_request_json(method: str, path: str, payload: dict[str, object] | None = None) -> dict[str, object]:
        if path == "/prompt":
            return {"prompt_id": "abc-123"}
        return {"abc-123": {"status": {"status_str": "success", "completed": False}}}

    monkeypatch.setattr(service, "_request_json", fake_request_json)
    workflow = service._build_workflow(prompt="x", input_filename=None, seed=1, steps=4, denoise=1.0, size="1024x1024")

    with pytest.raises(ComfyUIImageServiceError) as exc:
        asyncio.run(service._submit_and_wait(workflow))
    assert exc.value.error_code == "COMFYUI_GENERATION_TIMEOUT"
    assert exc.value.status_code == 504


def test_request_json_surfaces_http_status_error(monkeypatch: pytest.MonkeyPatch) -> None:
    import httpx

    service = _make_service(monkeypatch)
    monkeypatch.setattr(
        "app.services.comfyui_image_service.httpx.AsyncClient",
        lambda **_: FakeAsyncClient([httpx.HTTPStatusError("bad", request=httpx.Request("GET", "http://x"), response=httpx.Response(404))]),
    )
    with pytest.raises(ComfyUIImageServiceError) as exc:
        asyncio.run(service._request_json("GET", "/history/abc"))
    assert exc.value.error_code == "COMFYUI_HTTP_ERROR"
    assert exc.value.status_code == 404


def test_request_json_surfaces_connect_error(monkeypatch: pytest.MonkeyPatch) -> None:
    import httpx

    service = _make_service(monkeypatch)
    monkeypatch.setattr(
        "app.services.comfyui_image_service.httpx.AsyncClient",
        lambda **_: FakeAsyncClient([httpx.ConnectError("refused")]),
    )
    with pytest.raises(ComfyUIImageServiceError) as exc:
        asyncio.run(service._request_json("GET", "/history/abc"))
    assert exc.value.error_code == "COMFYUI_UNREACHABLE"
    assert exc.value.status_code == 503
