"""NodAPI OpenAI-compatible 对话服务。"""
from __future__ import annotations

from typing import Literal

import json

from app.config.settings import settings
from app.services.curl_request import CurlRequestError, request_json
from app.services.nodapi_midjourney_service import NodApiMidjourneyServiceError

ChatRole = Literal["system", "user", "assistant"]
ChatMessage = dict[str, str]


class NodApiChatService:
    """封装 NodAPI /v1/chat/completions 调用。"""

    def __init__(self) -> None:
        self.base_url = (settings.NODAPI_BASE_URL or "").strip().rstrip("/")
        self.api_key = (settings.NODAPI_API_KEY or "").strip()
        self.default_model = (getattr(settings, "NODAPI_CHAT_MODEL", "") or "gpt-4o-mini").strip()
        self.timeout_seconds = float(getattr(settings, "NODAPI_CHAT_TIMEOUT_SECONDS", 120.0) or 120.0)

    @property
    def configured(self) -> bool:
        return bool(self.base_url and self.api_key)

    async def complete(
        self,
        *,
        model: str,
        messages: list[ChatMessage],
        temperature: float,
        max_tokens: int,
    ) -> dict[str, object]:
        self._ensure_configured()
        payload: dict[str, object] = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        data = await self._request_json("/chat/completions", payload)
        return {
            "model": model,
            "content": self._extract_content(data),
            "raw": data,
        }

    async def _request_json(self, path: str, payload: dict[str, object]) -> dict[str, object]:
        url = self._build_v1_url(path)
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        try:
            response = await request_json(
                method="POST",
                url=url,
                headers=headers,
                json_payload=payload,
                timeout_seconds=self.timeout_seconds,
            )
        except CurlRequestError as exc:
            raise NodApiMidjourneyServiceError(
                f"无法连接 NodAPI 对话接口：{exc.message}",
                "NODAPI_CHAT_REQUEST_FAILED",
                status_code=exc.status_code,
            ) from exc

        if response.status_code >= 400:
            raise NodApiMidjourneyServiceError(
                self._extract_http_error(response.body) or f"NodAPI 对话接口返回错误：HTTP {response.status_code}",
                "NODAPI_CHAT_HTTP_ERROR",
                status_code=response.status_code,
            )

        try:
            data = json.loads(response.body)
        except ValueError as exc:
            raise NodApiMidjourneyServiceError(
                "NodAPI 对话响应格式不合法",
                "NODAPI_CHAT_RESPONSE_INVALID",
                status_code=502,
            ) from exc
        if not isinstance(data, dict):
            raise NodApiMidjourneyServiceError(
                "NodAPI 对话响应格式不合法",
                "NODAPI_CHAT_RESPONSE_INVALID",
                status_code=502,
            )
        return data

    def _build_v1_url(self, path: str) -> str:
        if self.base_url.endswith("/v1"):
            return f"{self.base_url}{path}"
        return f"{self.base_url}/v1{path}"

    def _ensure_configured(self) -> None:
        if not self.configured:
            raise NodApiMidjourneyServiceError(
                "NodAPI 对话服务未配置，请设置 NODAPI_BASE_URL 和 NODAPI_API_KEY",
                "NODAPI_CHAT_NOT_CONFIGURED",
                status_code=503,
            )

    @staticmethod
    def _extract_content(data: dict[str, object]) -> str:
        choices = data.get("choices")
        if isinstance(choices, list) and choices:
            first = choices[0]
            if isinstance(first, dict):
                message = first.get("message")
                if isinstance(message, dict):
                    content = message.get("content")
                    if isinstance(content, str):
                        return content
                text = first.get("text")
                if isinstance(text, str):
                    return text
        return ""

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


nodapi_chat_service = NodApiChatService()
