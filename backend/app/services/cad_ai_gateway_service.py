"""CAD AI 统一网关客户端。"""
from __future__ import annotations

import re
from typing import Any

import httpx

from app.config.settings import settings


class CadAiGatewayError(Exception):
    """CAD AI 网关可预期错误。"""

    def __init__(self, message: str, error_code: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.message = message
        self.error_code = error_code
        self.status_code = status_code


class CadAiGatewayService:
    """封装远端同端口 `/cad-ai/*` 能力，供业务后端统一代理。"""

    _safe_path_id_pattern = re.compile(r"^[A-Za-z0-9_.:-]{1,160}$")

    def __init__(
        self,
        *,
        base_url: str | None = None,
        api_key: str | None = None,
        timeout_seconds: float | None = None,
    ) -> None:
        self.base_url = (base_url if base_url is not None else settings.CAD_AI_BASE_URL or "").strip().rstrip("/")
        self.api_key = (api_key if api_key is not None else settings.CAD_AI_API_KEY or "").strip()
        self.timeout_seconds = timeout_seconds if timeout_seconds is not None else settings.CAD_AI_TIMEOUT_SECONDS

    async def health(self) -> dict[str, Any]:
        """读取远端 CAD AI 健康状态。"""
        return await self._request_json("GET", "/cad-ai/health")

    async def auto_generate(self, payload: dict[str, Any]) -> dict[str, Any]:
        """提交自动生成工作流任务。"""
        return await self._request_json("POST", "/cad-ai/workflow/auto-generate", json=payload)

    async def get_task(self, task_id: str) -> dict[str, Any]:
        """查询远端任务状态。"""
        self._validate_path_id(task_id, "任务编号")
        return await self._request_json("GET", f"/cad-ai/tasks/{task_id}")

    def build_asset_download_url(self, asset_id: str) -> str:
        """构建远端资产下载地址。"""
        self._ensure_configured()
        self._validate_path_id(asset_id, "资产编号")
        return f"{self.base_url}/cad-ai/assets/{asset_id}/download"

    def build_auth_headers(self) -> dict[str, str]:
        """构建远端网关请求头。"""
        return self._headers()

    async def _request_json(
        self,
        method: str,
        path: str,
        *,
        json: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        self._ensure_configured()
        url = f"{self.base_url}{path}"
        headers = self._headers()
        try:
            async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
                if method == "GET":
                    response = await client.get(url, headers=headers)
                elif method == "POST":
                    response = await client.post(url, json=json or {}, headers=headers)
                else:
                    raise ValueError(f"Unsupported method: {method}")
                response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            message = self._extract_error_message(exc.response)
            raise CadAiGatewayError(
                message or f"CAD AI 网关返回错误：HTTP {exc.response.status_code}",
                "CAD_AI_GATEWAY_HTTP_ERROR",
                status_code=exc.response.status_code,
            ) from exc
        except httpx.HTTPError as exc:
            raise CadAiGatewayError(
                f"无法连接 CAD AI 网关：{exc}",
                "CAD_AI_GATEWAY_REQUEST_FAILED",
                status_code=502,
            ) from exc

        data = response.json()
        if not isinstance(data, dict):
            raise CadAiGatewayError(
                "CAD AI 网关响应格式不合法",
                "CAD_AI_GATEWAY_RESPONSE_INVALID",
                status_code=502,
            )
        return data

    def _ensure_configured(self) -> None:
        if not self.base_url:
            raise CadAiGatewayError(
                "CAD AI 网关未配置，请设置 CAD_AI_BASE_URL 为服务器 Qwen3 统一端口地址",
                "CAD_AI_GATEWAY_NOT_CONFIGURED",
                status_code=503,
            )

    def _headers(self) -> dict[str, str]:
        if not self.api_key:
            return {}
        return {"Authorization": f"Bearer {self.api_key}"}

    def _validate_path_id(self, value: str, label: str) -> None:
        if not self._safe_path_id_pattern.fullmatch(value):
            raise CadAiGatewayError(
                f"{label}不合法",
                "CAD_AI_ASSET_ID_INVALID",
                status_code=400,
            )

    @staticmethod
    def _extract_error_message(response: httpx.Response) -> str:
        try:
            data = response.json()
        except ValueError:
            return response.text[:300]
        if isinstance(data, dict):
            message = data.get("message") or data.get("detail") or data.get("error")
            return message if isinstance(message, str) else ""
        return ""


cad_ai_gateway_service = CadAiGatewayService()
