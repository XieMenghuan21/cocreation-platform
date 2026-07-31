"""NodAPI Midjourney 图片生成服务。"""
from __future__ import annotations

import asyncio
import json
from typing import Any

from app.config.settings import settings
from app.services.curl_request import CurlRequestError, request_json


class NodApiMidjourneyServiceError(Exception):
    """NodAPI Midjourney 调用失败。"""

    def __init__(self, message: str, error_code: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.message = message
        self.error_code = error_code
        self.status_code = status_code


class NodApiMidjourneyService:
    """封装 NodAPI 的 NewAPI/MJ Proxy 兼容接口。"""

    def __init__(
        self,
        *,
        base_url: str | None = None,
        api_key: str | None = None,
        prompt_suffix: str | None = None,
        timeout_seconds: float | None = None,
        poll_interval_seconds: float | None = None,
    ) -> None:
        self.base_url = (base_url if base_url is not None else settings.NODAPI_BASE_URL).strip().rstrip("/")
        self.api_key = (api_key if api_key is not None else settings.NODAPI_API_KEY or "").strip()
        self.prompt_suffix = (
            prompt_suffix if prompt_suffix is not None else settings.NODAPI_MIDJOURNEY_PROMPT_SUFFIX
        ).strip()
        self.timeout_seconds = (
            timeout_seconds
            if timeout_seconds is not None
            else settings.NODAPI_MIDJOURNEY_TIMEOUT_SECONDS
        )
        self.poll_interval_seconds = (
            poll_interval_seconds
            if poll_interval_seconds is not None
            else settings.NODAPI_MIDJOURNEY_POLL_INTERVAL_SECONDS
        )

    @property
    def configured(self) -> bool:
        return bool(self.base_url and self.api_key)

    async def generate_design_image(self, *, prompt: str, images: list[str] | None = None) -> dict[str, Any]:
        """提交 Midjourney imagine 任务并等待最终图片地址。"""
        self._ensure_configured()
        final_prompt = self._build_prompt(prompt)
        submit_payload: dict[str, Any] = {"prompt": final_prompt}
        if images:
            submit_payload["base64Array"] = images

        submit_data = await self._request_json("POST", "/mj/submit/imagine", json=submit_payload)
        task_id = self._extract_task_id(submit_data)
        task_data = await self._poll_task(task_id)
        result_url = self._extract_result_url(task_data)
        return {
            "taskId": task_id,
            "model": "midjourney",
            "resultUrl": result_url,
            "status": str(task_data.get("status") or "SUCCESS"),
            "raw": task_data,
        }

    async def _poll_task(self, task_id: str) -> dict[str, Any]:
        max_attempts = max(1, int(self.timeout_seconds // max(self.poll_interval_seconds, 1)))
        last_data: dict[str, Any] = {}
        for attempt in range(max_attempts):
            data = await self._request_json("GET", f"/mj/task/{task_id}/fetch")
            task_data = data.get("data", data) if isinstance(data.get("data"), dict) else data
            if not isinstance(task_data, dict):
                raise NodApiMidjourneyServiceError(
                    "NodAPI Midjourney 任务响应格式不合法",
                    "NODAPI_MJ_TASK_INVALID",
                    status_code=502,
                )
            last_data = task_data
            status = str(task_data.get("status") or "").upper()
            if status in {"SUCCESS", "FINISHED", "COMPLETED"}:
                return task_data
            if status in {"FAILURE", "FAILED"}:
                raise NodApiMidjourneyServiceError(
                    self._extract_task_error(task_data),
                    "NODAPI_MJ_TASK_FAILED",
                    status_code=502,
                )
            if attempt < max_attempts - 1:
                await asyncio.sleep(self.poll_interval_seconds)

        raise NodApiMidjourneyServiceError(
            f"NodAPI Midjourney 任务超时：{last_data.get('progress') or '无进度'}",
            "NODAPI_MJ_TASK_TIMEOUT",
            status_code=504,
        )

    async def _request_json(
        self,
        method: str,
        path: str,
        *,
        json: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        self._ensure_configured()
        url = f"{self.base_url}{path}"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        try:
            response = await request_json(
                method=method,
                url=url,
                headers=headers,
                json_payload=json or None,
                timeout_seconds=self.timeout_seconds,
            )
        except CurlRequestError as exc:
            raise NodApiMidjourneyServiceError(
                f"无法连接 NodAPI Midjourney 服务：{exc.message}",
                "NODAPI_MJ_REQUEST_FAILED",
                status_code=exc.status_code,
            ) from exc

        if response.status_code >= 400:
            raise NodApiMidjourneyServiceError(
                self._extract_http_error(response.body) or f"NodAPI 返回错误：HTTP {response.status_code}",
                "NODAPI_MJ_HTTP_ERROR",
                status_code=response.status_code,
            )

        try:
            data = json.loads(response.body)
        except ValueError as exc:
            raise NodApiMidjourneyServiceError(
                "NodAPI Midjourney 响应格式不合法",
                "NODAPI_MJ_RESPONSE_INVALID",
                status_code=502,
            ) from exc
        if not isinstance(data, dict):
            raise NodApiMidjourneyServiceError(
                "NodAPI Midjourney 响应格式不合法",
                "NODAPI_MJ_RESPONSE_INVALID",
                status_code=502,
            )
        return data

    def _build_prompt(self, prompt: str) -> str:
        clean_prompt = prompt.strip()
        if self.prompt_suffix and self.prompt_suffix not in clean_prompt:
            return f"{clean_prompt} {self.prompt_suffix}".strip()
        return clean_prompt

    def _ensure_configured(self) -> None:
        if not self.base_url or not self.api_key:
            raise NodApiMidjourneyServiceError(
                "NodAPI Midjourney 未配置，请设置 NODAPI_BASE_URL 与 NODAPI_API_KEY",
                "NODAPI_MJ_NOT_CONFIGURED",
                status_code=503,
            )

    @staticmethod
    def _extract_task_id(data: dict[str, Any]) -> str:
        candidates = [
            data.get("result"),
            data.get("task_id"),
            data.get("taskId"),
            data.get("id"),
            data.get("data", {}).get("result") if isinstance(data.get("data"), dict) else None,
            data.get("data", {}).get("task_id") if isinstance(data.get("data"), dict) else None,
            data.get("data", {}).get("taskId") if isinstance(data.get("data"), dict) else None,
        ]
        for candidate in candidates:
            value = str(candidate or "").strip()
            if value:
                return value
        raise NodApiMidjourneyServiceError(
            "NodAPI Midjourney 未返回任务编号",
            "NODAPI_MJ_TASK_ID_MISSING",
            status_code=502,
        )

    @staticmethod
    def _extract_result_url(task_data: dict[str, Any]) -> str:
        candidates = [
            task_data.get("imageUrl"),
            task_data.get("image_url"),
            task_data.get("resultUrl"),
            task_data.get("result_url"),
            task_data.get("url"),
        ]
        for candidate in candidates:
            value = str(candidate or "").strip()
            if value:
                return value
        raise NodApiMidjourneyServiceError(
            "NodAPI Midjourney 任务已完成，但未返回图片地址",
            "NODAPI_MJ_RESULT_URL_MISSING",
            status_code=502,
        )

    @staticmethod
    def _extract_task_error(task_data: dict[str, Any]) -> str:
        for key in ("failReason", "fail_reason", "error", "message", "description"):
            value = task_data.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        return "NodAPI Midjourney 任务失败"

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


nodapi_midjourney_service = NodApiMidjourneyService()
