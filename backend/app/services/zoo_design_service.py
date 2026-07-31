"""Zoo Text-to-CAD 服务。"""
from __future__ import annotations

import asyncio
import base64
from typing import Any

import httpx

from app.config.settings import settings


class ZooDesignServiceError(Exception):
    """Zoo 设计服务可预期错误。"""

    def __init__(self, message: str, error_code: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.message = message
        self.error_code = error_code
        self.status_code = status_code


class ZooDesignService:
    """封装 Zoo text-to-CAD 与异步轮询。"""

    def __init__(
        self,
        *,
        base_url: str | None = None,
        api_token: str | None = None,
        timeout_seconds: float | None = None,
        output_format: str = "glb",
        poll_interval_seconds: float = 5.0,
        max_attempts: int = 120,
    ) -> None:
        self.base_url = (base_url if base_url is not None else settings.ZOO_API_BASE_URL or "").strip().rstrip("/")
        self.api_token = (api_token if api_token is not None else settings.ZOO_API_TOKEN or "").strip()
        self.timeout_seconds = timeout_seconds if timeout_seconds is not None else settings.ZOO_API_TIMEOUT_SECONDS
        self.output_format = output_format
        self.poll_interval_seconds = poll_interval_seconds
        self.max_attempts = max_attempts

    @property
    def configured(self) -> bool:
        return bool(self.base_url and self.api_token)

    async def create_text_to_cad(
        self,
        *,
        prompt: str,
        project_name: str,
        model_version: str | None = None,
    ) -> dict[str, Any]:
        self._ensure_configured()
        request_body: dict[str, Any] = {
            "prompt": prompt,
            "project_name": project_name,
        }
        if model_version:
            request_body["model_version"] = model_version

        result = await self._request_json(
            "POST",
            f"/ai/text-to-cad/{self.output_format}",
            params={"kcl": "false"},
            json=request_body,
        )
        operation_id = self._extract_operation_id(result)
        task_data = result
        if self._normalize_status(task_data.get("status")) not in {"completed", "failed"}:
            task_data = await self._get_async_operation(operation_id)

        status = self._normalize_status(task_data.get("status"))
        if status == "failed":
            raise ZooDesignServiceError(
                str(task_data.get("error") or "Zoo text-to-CAD 生成失败"),
                "ZOO_TEXT_TO_CAD_FAILED",
                status_code=502,
            )

        outputs = task_data.get("outputs")
        if not isinstance(outputs, dict) or not outputs:
            raise ZooDesignServiceError(
                "Zoo 未返回模型输出",
                "ZOO_OUTPUTS_MISSING",
                status_code=502,
            )

        return {
            "taskId": operation_id,
            "status": status,
            "outputFormat": task_data.get("output_format", self.output_format),
            "outputs": outputs,
            "raw": task_data,
        }

    async def _get_async_operation(self, operation_id: str) -> dict[str, Any]:
        for attempt in range(self.max_attempts):
            data = await self._request_json("GET", f"/async/operations/{operation_id}")
            status = self._normalize_status(data.get("status"))
            if status in {"completed", "failed"}:
                return data
            if attempt < self.max_attempts - 1:
                await asyncio.sleep(self.poll_interval_seconds)
        raise ZooDesignServiceError(
            "Zoo text-to-CAD 生成超时，请稍后重试",
            "ZOO_TEXT_TO_CAD_TIMEOUT",
            status_code=504,
        )

    async def _request_json(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        self._ensure_configured()
        url = f"{self.base_url}{path}"
        headers = {
            "Authorization": f"Bearer {self.api_token}",
            "Content-Type": "application/json",
        }
        try:
            async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
                if method == "GET":
                    response = await client.get(url, headers=headers, params=params)
                elif method == "POST":
                    response = await client.post(url, headers=headers, params=params, json=json or {})
                else:
                    raise ValueError(f"Unsupported method: {method}")
                response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise ZooDesignServiceError(
                self._extract_error_message(exc.response) or f"Zoo 返回错误：HTTP {exc.response.status_code}",
                "ZOO_HTTP_ERROR",
                status_code=exc.response.status_code,
            ) from exc
        except httpx.HTTPError as exc:
            raise ZooDesignServiceError(
                f"无法连接 Zoo 服务：{exc}",
                "ZOO_REQUEST_FAILED",
                status_code=502,
            ) from exc

        data = response.json()
        if not isinstance(data, dict):
            raise ZooDesignServiceError(
                "Zoo 响应格式不合法",
                "ZOO_RESPONSE_INVALID",
                status_code=502,
            )
        return data

    def _ensure_configured(self) -> None:
        if not self.base_url or not self.api_token:
            raise ZooDesignServiceError(
                "Zoo 服务未配置，请设置 ZOO_API_BASE_URL 和 ZOO_API_TOKEN",
                "ZOO_NOT_CONFIGURED",
                status_code=503,
            )

    @staticmethod
    def decode_output_bytes(encoded: str) -> bytes:
        return base64.b64decode(encoded)

    @staticmethod
    def _normalize_status(value: Any) -> str:
        if isinstance(value, str) and value.strip():
            return value.strip().lower()
        return "unknown"

    @staticmethod
    def _extract_operation_id(data: dict[str, Any]) -> str:
        value = data.get("id")
        if isinstance(value, str) and value.strip():
            return value.strip()
        raise ZooDesignServiceError(
            "Zoo 未返回任务编号",
            "ZOO_OPERATION_ID_MISSING",
            status_code=502,
        )

    @staticmethod
    def _extract_error_message(response: httpx.Response) -> str:
        try:
            data = response.json()
        except ValueError:
            return response.text[:300]
        if isinstance(data, dict):
            message = data.get("message") or data.get("error")
            if isinstance(message, str):
                return message.strip()
        return ""


zoo_design_service = ZooDesignService()
