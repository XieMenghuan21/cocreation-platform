"""Gemini 图片生成服务：调用 Google Gemini API 生成图片。"""
from __future__ import annotations

import base64
from typing import Any

import httpx

from app.config.settings import settings


class GeminiImageServiceError(Exception):
    """Gemini 图片生成调用失败。"""

    def __init__(self, message: str, error_code: str = "GEMINI_IMAGE_ERROR", status_code: int = 400) -> None:
        super().__init__(message)
        self.message = message
        self.error_code = error_code
        self.status_code = status_code


class GeminiImageService:
    """封装 Google Gemini generateContent 图片生成调用。"""

    def __init__(self) -> None:
        self.api_key = (settings.GEMINI_API_KEY or "").strip()
        self.base_url = (settings.GEMINI_BASE_URL or "https://generativelanguage.googleapis.com/v1").rstrip("/")
        self.model = (getattr(settings, "GEMINI_IMAGE_MODEL", None) or "gemini-2.0-flash-exp").strip()
        self.timeout_seconds = float(getattr(settings, "GEMINI_IMAGE_TIMEOUT_SECONDS", 120))

    @property
    def configured(self) -> bool:
        return bool(self.api_key)

    async def generate_design_image(
        self,
        *,
        prompt: str,
        images: list[str] | None = None,
        model: str | None = None,
    ) -> dict[str, Any]:
        if not self.configured:
            raise GeminiImageServiceError(
                "Gemini 图片服务未配置，请设置 GEMINI_API_KEY",
                "GEMINI_IMAGE_NOT_CONFIGURED",
                status_code=503,
            )
        image_model = (model or self.model).strip()
        clean_prompt = prompt.strip()
        if images:
            references = "\n".join(f"参考图：{url}" for url in images if str(url).strip())
            clean_prompt = f"{clean_prompt}\n{references}".strip()

        url = f"{self.base_url}/models/{image_model}:generateContent?key={self.api_key}"
        payload: dict[str, Any] = {
            "contents": [{"parts": [{"text": f"Generate an image: {clean_prompt}"}]}],
            "generationConfig": {"responseModalities": ["TEXT", "IMAGE"]},
        }
        try:
            async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
                response = await client.post(url, json=payload, headers={"Content-Type": "application/json"})
                response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise GeminiImageServiceError(
                self._extract_error(exc.response) or f"Gemini 图片接口返回错误：HTTP {exc.response.status_code}",
                "GEMINI_IMAGE_HTTP_ERROR",
                status_code=exc.response.status_code,
            ) from exc
        except httpx.HTTPError as exc:
            raise GeminiImageServiceError(
                f"无法连接 Gemini 图片接口：{exc}",
                "GEMINI_IMAGE_REQUEST_FAILED",
                status_code=502,
            ) from exc

        data = response.json()
        if not isinstance(data, dict):
            raise GeminiImageServiceError("Gemini 响应格式不合法", "GEMINI_IMAGE_RESPONSE_INVALID", status_code=502)

        result_url = self._extract_image_url(data)
        return {
            "taskId": str(data.get("responseId") or f"gemini_image_{image_model}"),
            "model": image_model,
            "resultUrl": result_url,
            "status": "completed",
            "raw": data,
        }

    @staticmethod
    def _extract_image_url(data: dict[str, Any]) -> str:
        candidates = data.get("candidates")
        if not isinstance(candidates, list) or not candidates:
            raise GeminiImageServiceError("Gemini 图片响应缺少 candidates", "GEMINI_IMAGE_DATA_MISSING", status_code=502)
        first = candidates[0]
        if not isinstance(first, dict):
            raise GeminiImageServiceError("Gemini 图片结果格式不合法", "GEMINI_IMAGE_ITEM_INVALID", status_code=502)
        content = first.get("content")
        if not isinstance(content, dict):
            raise GeminiImageServiceError("Gemini 图片响应缺少 content", "GEMINI_IMAGE_CONTENT_MISSING", status_code=502)
        parts = content.get("parts")
        if not isinstance(parts, list):
            raise GeminiImageServiceError("Gemini 图片响应缺少 parts", "GEMINI_IMAGE_PARTS_MISSING", status_code=502)
        for part in parts:
            if not isinstance(part, dict):
                continue
            inline_data = part.get("inlineData") or part.get("inline_data")
            if isinstance(inline_data, dict):
                mime_type = inline_data.get("mimeType") or inline_data.get("mime_type") or "image/png"
                b64 = inline_data.get("data")
                if isinstance(b64, str) and b64.strip():
                    return f"data:{mime_type};base64,{b64.strip()}"
        raise GeminiImageServiceError("Gemini 图片生成完成，但未返回图片数据", "GEMINI_IMAGE_URL_MISSING", status_code=502)

    @staticmethod
    def _extract_error(response: httpx.Response) -> str:
        try:
            data = response.json()
        except ValueError:
            return response.text[:300]
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


gemini_image_service = GeminiImageService()
