"""NodAPI 模型目录服务。"""
from __future__ import annotations

import json
from typing import Any

from app.config.settings import settings
from app.services.curl_request import CurlRequestError, request_json


class NodApiCatalogServiceError(Exception):
    """NodAPI 目录服务调用失败。"""

    def __init__(self, message: str, error_code: str = "NODAPI_CATALOG_ERROR", status_code: int = 400) -> None:
        super().__init__(message)
        self.message = message
        self.error_code = error_code
        self.status_code = status_code


class NodApiCatalogService:
    """封装 NodAPI 模型目录查询。"""

    def __init__(self) -> None:
        self.base_url = (settings.NODAPI_BASE_URL or "").strip().rstrip("/")
        self.api_key = (settings.NODAPI_API_KEY or "").strip()
        self.timeout_seconds = 30.0

    @property
    def configured(self) -> bool:
        return bool(self.base_url and self.api_key)

    async def get_snapshot(self) -> dict[str, Any]:
        """获取模型目录快照。"""
        if not self.configured:
            raise NodApiCatalogServiceError(
                "NodAPI 目录服务未配置，请设置 NODAPI_BASE_URL 和 NODAPI_API_KEY",
                "NODAPI_CATALOG_NOT_CONFIGURED",
                status_code=503,
            )

        url = self._build_url("/models")
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        try:
            response = await request_json(
                method="GET",
                url=url,
                headers=headers,
                json_payload=None,
                timeout_seconds=self.timeout_seconds,
            )
        except CurlRequestError as exc:
            raise NodApiCatalogServiceError(
                f"无法连接 NodAPI 目录接口：{exc.message}",
                "NODAPI_CATALOG_REQUEST_FAILED",
                status_code=exc.status_code,
            ) from exc

        if response.status_code >= 400:
            raise NodApiCatalogServiceError(
                f"NodAPI 目录接口返回错误：HTTP {response.status_code}",
                "NODAPI_CATALOG_HTTP_ERROR",
                status_code=response.status_code,
            )

        try:
            data = json.loads(response.body)
        except ValueError as exc:
            raise NodApiCatalogServiceError(
                "NodAPI 目录响应格式不合法",
                "NODAPI_CATALOG_RESPONSE_INVALID",
                status_code=502,
            ) from exc
        if not isinstance(data, dict):
            raise NodApiCatalogServiceError(
                "NodAPI 目录响应格式不合法",
                "NODAPI_CATALOG_RESPONSE_INVALID",
                status_code=502,
            )

        return {
            "provider": "nodapi",
            "configured": True,
            "models": data.get("data", []),
            "rawData": data,
        }

    def _build_url(self, path: str) -> str:
        """构建完整的 API URL。"""
        if self.base_url.endswith("/v1"):
            return f"{self.base_url}{path}"
        return f"{self.base_url}/v1{path}"


nodapi_catalog_service = NodApiCatalogService()
