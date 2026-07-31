"""AI Chat 网关服务：统一本地 Qwen 模型调用。"""
from __future__ import annotations

import httpx

from app.config.settings import settings
from app.schemas.ai_chat import AIChatCompletionRequest


class AIChatGatewayService:
    """封装本地部署 Qwen 模型的 OpenAI-compatible 接口调用。"""

    def __init__(self) -> None:
        self.base_url = (settings.LOCAL_QWEN_BASE_URL or "").strip().rstrip("/")
        self.api_key = (settings.LOCAL_QWEN_API_KEY or "").strip()
        self.model = (settings.LOCAL_QWEN_MODEL or "").strip()
        self.timeout_seconds = 120.0

    @property
    def configured(self) -> bool:
        return bool(self.base_url and self.model)

    async def is_available(self) -> bool:
        """检测本地 Qwen 服务是否可用。"""
        if not self.configured:
            return False
        try:
            url = f"{self.base_url}/v1/models" if "/v1" not in self.base_url else f"{self.base_url}/models"
            headers = {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}
            async with httpx.AsyncClient(timeout=5.0) as client:
                response = await client.get(url, headers=headers)
                return response.status_code == 200
        except Exception:
            return False

    async def complete(self, request: AIChatCompletionRequest, user: dict[str, object]) -> dict[str, object]:
        """完成一次对话请求。"""
        if not self.configured:
            raise RuntimeError("本地 Qwen 服务未配置，请设置 LOCAL_QWEN_BASE_URL 和 LOCAL_QWEN_MODEL")

        payload = {
            "model": self.model,
            "messages": [msg.model_dump() for msg in request.messages],
            "temperature": request.temperature,
            "max_tokens": request.max_tokens,
        }

        data = await self._request_json("/chat/completions", payload)
        return data

    async def stream_complete(self, request: AIChatCompletionRequest, user: dict[str, object]):
        """流式完成对话请求。"""
        if not self.configured:
            raise RuntimeError("本地 Qwen 服务未配置，请设置 LOCAL_QWEN_BASE_URL 和 LOCAL_QWEN_MODEL")

        payload = {
            "model": self.model,
            "messages": [msg.model_dump() for msg in request.messages],
            "temperature": request.temperature,
            "max_tokens": request.max_tokens,
            "stream": True,
        }

        async for chunk in self._request_stream("/chat/completions", payload):
            yield chunk

    async def _request_json(self, path: str, payload: dict[str, object]) -> dict[str, object]:
        """发送 JSON 请求并返回响应数据。"""
        url = self._build_url(path)
        headers = {
            "Authorization": f"Bearer {self.api_key}" if self.api_key else "",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        try:
            async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
                response = await client.post(url, headers=headers, json=payload)
                response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise RuntimeError(f"本地 Qwen 服务返回错误：HTTP {exc.response.status_code}") from exc
        except httpx.HTTPError as exc:
            raise RuntimeError(f"无法连接本地 Qwen 服务：{exc}") from exc

        data = response.json()
        if not isinstance(data, dict):
            raise RuntimeError("本地 Qwen 响应格式不合法")
        return data

    async def _request_stream(self, path: str, payload: dict[str, object]):
        """发送流式请求并逐块返回响应。"""
        url = self._build_url(path)
        headers = {
            "Authorization": f"Bearer {self.api_key}" if self.api_key else "",
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
        }
        try:
            async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
                async with client.stream("POST", url, headers=headers, json=payload) as response:
                    response.raise_for_status()
                    async for line in response.aiter_lines():
                        if line.startswith("data: "):
                            data = line[6:]
                            if data.strip() == "[DONE]":
                                break
                            yield data
        except httpx.HTTPStatusError as exc:
            raise RuntimeError(f"本地 Qwen 流式服务返回错误：HTTP {exc.response.status_code}") from exc
        except httpx.HTTPError as exc:
            raise RuntimeError(f"无法连接本地 Qwen 流式服务：{exc}") from exc

    def _build_url(self, path: str) -> str:
        """构建完整的 API URL。"""
        if self.base_url.endswith("/v1"):
            return f"{self.base_url}{path}"
        return f"{self.base_url}/v1{path}"

    def health(self) -> dict[str, object]:
        """健康检查信息。"""
        return {
            "configured": self.configured,
            "baseUrl": self.base_url,
            "model": self.model,
        }


ai_chat_gateway_service = AIChatGatewayService()
