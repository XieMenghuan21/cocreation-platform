"""NodAPI OpenAI-compatible 图片生成服务。"""
from __future__ import annotations

import asyncio
import json
from typing import Any

from app.config.settings import settings
from app.services.curl_request import CurlRequestError, CurlResponse, request_json


class NodApiImageServiceError(Exception):
    """NodAPI 图片生成调用失败。"""

    def __init__(self, message: str, error_code: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.message = message
        self.error_code = error_code
        self.status_code = status_code


class NodApiImageService:
    """封装 NodAPI /v1/images/generations 调用。"""

    def __init__(self) -> None:
        self.base_url = (settings.NODAPI_BASE_URL or "").strip().rstrip("/")
        self.api_key = (settings.NODAPI_API_KEY or "").strip()
        self.timeout_seconds = settings.NODAPI_IMAGE_TIMEOUT_SECONDS
        self.retry_count = max(0, int(getattr(settings, "NODAPI_IMAGE_RETRY_COUNT", 1) or 0))
        self.retry_delay_seconds = float(getattr(settings, "NODAPI_IMAGE_RETRY_DELAY_SECONDS", 3.0) or 0.0)
        self.default_model = settings.NODAPI_IMAGE_MODEL.strip()
        self.default_size = settings.NODAPI_IMAGE_SIZE.strip()

    @property
    def configured(self) -> bool:
        return bool(self.base_url and self.api_key)

    async def generate_design_image(
        self,
        *,
        prompt: str,
        images: list[str] | None = None,
        model: str | None = None,
    ) -> dict[str, Any]:
        image_model = (model or self.default_model).strip()
        if not image_model:
            raise NodApiImageServiceError("NodAPI 图片模型未配置", "NODAPI_IMAGE_MODEL_MISSING", status_code=503)
        payload: dict[str, Any] = {
            "model": image_model,
            "prompt": self._build_prompt(prompt, images),
            "n": 1,
            "size": self.default_size,
        }
        data = await self._request_json("/images/generations", payload, model=image_model)
        result_url = self._extract_result_url(data)
        base_url = self._resolve_base_url(image_model)
        if result_url.startswith("/"):
            result_url = self._join_result_url(base_url, result_url)
        return {
            "taskId": str(data.get("id") or f"nodapi_image_{image_model}"),
            "model": image_model,
            "resultUrl": result_url,
            "status": "completed",
            "raw": data,
        }

    async def _request_json(self, path: str, payload: dict[str, Any], *, model: str | None = None) -> dict[str, Any]:
        url = self._build_v1_url(path, model=model)
        api_key = self._resolve_api_key(model)
        if not api_key:
            raise NodApiImageServiceError("图片服务未配置 API Key", "NODAPI_IMAGE_API_KEY_MISSING", status_code=503)
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        response = await self._request_with_retry(
            url=url,
            headers=headers,
            payload=payload,
        )

        if response.status_code >= 400:
            raise NodApiImageServiceError(
                self._extract_http_error(response.body) or f"NodAPI 图片接口返回错误：HTTP {response.status_code}",
                "NODAPI_IMAGE_HTTP_ERROR",
                status_code=response.status_code,
            )

        try:
            data = json.loads(response.body)
        except ValueError as exc:
            raise NodApiImageServiceError("NodAPI 图片响应格式不合法", "NODAPI_IMAGE_RESPONSE_INVALID", status_code=502) from exc
        if not isinstance(data, dict):
            raise NodApiImageServiceError("NodAPI 图片响应格式不合法", "NODAPI_IMAGE_RESPONSE_INVALID", status_code=502)
        return data

    async def _request_with_retry(
        self,
        *,
        url: str,
        headers: dict[str, str],
        payload: dict[str, Any],
    ) -> CurlResponse:
        attempts = self.retry_count + 1
        last_error: CurlRequestError | None = None

        for attempt in range(1, attempts + 1):
            try:
                return await request_json(
                    method="POST",
                    url=url,
                    headers=headers,
                    json_payload=payload,
                    timeout_seconds=self.timeout_seconds,
                )
            except CurlRequestError as exc:
                last_error = exc
                if not self._should_retry(exc) or attempt >= attempts:
                    break
                if self.retry_delay_seconds > 0:
                    await asyncio.sleep(self.retry_delay_seconds * attempt)

        assert last_error is not None
        retry_hint = f"；已重试 {self.retry_count} 次" if self.retry_count > 0 else ""
        raise NodApiImageServiceError(
            f"无法连接 NodAPI 图片接口：{last_error.message}{retry_hint}",
            "NODAPI_IMAGE_REQUEST_FAILED",
            status_code=last_error.status_code,
        ) from last_error

    def _build_v1_url(self, path: str, *, model: str | None = None) -> str:
        base_url = self._resolve_base_url(model)
        if base_url.endswith("/v1"):
            return f"{base_url}{path}"
        return f"{base_url}/v1{path}"

    def _resolve_base_url(self, model: str | None = None) -> str:
        normalized = str(model or "").strip().lower()
        if self._is_local_wan_model(normalized):
            return str(getattr(settings, "LOCAL_WAN_BASE_URL", "") or self.base_url).strip().rstrip("/")
        return self.base_url

    def _resolve_api_key(self, model: str | None = None) -> str:
        normalized = str(model or "").strip().lower()
        if self._is_local_wan_model(normalized):
            return str(getattr(settings, "LOCAL_WAN_API_KEY", "") or self.api_key).strip()
        return self.api_key

    @staticmethod
    def _join_result_url(base_url: str, result_path: str) -> str:
        normalized_base = base_url.strip().rstrip("/")
        normalized_path = result_path.strip()

        if normalized_path.startswith(("http://", "https://", "data:")):
            return normalized_path

        if normalized_base.endswith("/v1") and normalized_path.startswith("/v1/"):
            return f"{normalized_base}{normalized_path[3:]}"

        return f"{normalized_base}{normalized_path}"

    @staticmethod
    def _is_local_wan_model(model: str) -> bool:
        """判断给定模型是否属于本地 Wan 服务。"""
        if not model:
            return False
        # 检查模型名是否以 wan 开头
        if model.startswith("wan"):
            return True
        # 检查模型名是否与配置的本地 Wan 模型匹配
        configured_model = str(getattr(settings, "LOCAL_WAN_MODEL", "") or "").strip().lower()
        if configured_model and model == configured_model:
            return True
        # 常见 DiT/SD 模型名
        if model in {"sd3", "sdxl", "flux", "dall-e-3"}:
            return True
        return False

    def _ensure_configured(self) -> None:
        if not self.configured:
            raise NodApiImageServiceError(
                "NodAPI 图片服务未配置，请设置 NODAPI_BASE_URL 和 NODAPI_API_KEY",
                "NODAPI_IMAGE_NOT_CONFIGURED",
                status_code=503,
            )

    @staticmethod
    def _build_prompt(prompt: str, images: list[str] | None) -> str:
        clean_prompt = prompt.strip()
        if not images:
            return clean_prompt
        references = "\n".join(f"参考图：{url}" for url in images if str(url).strip())
        return f"{clean_prompt}\n{references}".strip()

    @staticmethod
    def _extract_result_url(data: dict[str, Any]) -> str:
        image_items = data.get("data")
        if not isinstance(image_items, list) or not image_items:
            raise NodApiImageServiceError("NodAPI 图片响应缺少 data", "NODAPI_IMAGE_DATA_MISSING", status_code=502)
        first = image_items[0]
        if not isinstance(first, dict):
            raise NodApiImageServiceError("NodAPI 图片结果格式不合法", "NODAPI_IMAGE_ITEM_INVALID", status_code=502)
        url = first.get("url")
        if isinstance(url, str) and url.strip():
            return url.strip()
        b64_json = first.get("b64_json")
        if isinstance(b64_json, str) and b64_json.strip():
            return f"data:image/png;base64,{b64_json.strip()}"
        raise NodApiImageServiceError("NodAPI 图片生成完成，但未返回图片地址", "NODAPI_IMAGE_URL_MISSING", status_code=502)

    @staticmethod
    def _extract_http_error(body: str) -> str:
        try:
            data = json.loads(body)
        except ValueError:
            return body[:300]
        if isinstance(data, dict):
            error = data.get("error")
            if isinstance(error, dict):
                message = error.get("message")
                if isinstance(message, str) and message.strip():
                    return message.strip()
            for key in ("message", "detail", "description"):
                value = data.get(key)
                if isinstance(value, str) and value.strip():
                    return value.strip()
        return ""

    @staticmethod
    def _should_retry(error: CurlRequestError) -> bool:
        if error.status_code in {408, 429, 500, 502, 503, 504}:
            return True
        message = error.message.lower()
        return "超时" in error.message or "timed out" in message or "temporarily" in message


nodapi_image_service = NodApiImageService()
