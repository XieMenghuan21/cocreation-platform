"""官方直连 AI 聊天服务：统一管理多个官方 API Key 的聊天调用。"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx

from app.config.settings import settings


@dataclass
class OfficialChatProvider:
    """官方对话服务提供商配置。"""

    name: str
    base_url: str
    api_key: str
    default_model: str | None = None


class OfficialChatServiceError(Exception):
    """官方直连聊天服务调用失败。"""

    def __init__(self, message: str, error_code: str = "OFFICIAL_CHAT_ERROR", status_code: int = 400) -> None:
        super().__init__(message)
        self.message = message
        self.error_code = error_code
        self.status_code = status_code


class OfficialChatService:
    """封装多个官方 API Key 的 OpenAI-compatible 聊天调用。"""

    def __init__(self) -> None:
        self.providers: list[OfficialChatProvider] = []
        self._init_providers()

    def _init_providers(self) -> None:
        """从 settings 初始化已配置的官方提供商。"""
        # DeepSeek
        deepseek_key = (settings.DEEPSEEK_API_KEY or "").strip()
        if deepseek_key:
            self.providers.append(
                OfficialChatProvider(
                    name="deepseek",
                    base_url=(settings.DEEPSEEK_BASE_URL or "https://api.deepseek.com").strip().rstrip("/"),
                    api_key=deepseek_key,
                    default_model="deepseek-chat",
                )
            )

        # 通义千问
        qwen_key = (settings.QWEN_API_KEY or "").strip()
        if qwen_key:
            self.providers.append(
                OfficialChatProvider(
                    name="qwen",
                    base_url=(settings.QWEN_BASE_URL or "https://dashscope.aliyuncs.com/compatible-mode/v1").strip().rstrip("/"),
                    api_key=qwen_key,
                    default_model="qwen-plus",
                )
            )

        # OpenAI
        openai_key = (settings.OPENAI_API_KEY or "").strip()
        if openai_key:
            self.providers.append(
                OfficialChatProvider(
                    name="openai",
                    base_url=(settings.OPENAI_BASE_URL or "https://api.openai.com/v1").strip().rstrip("/"),
                    api_key=openai_key,
                    default_model="gpt-4o-mini",
                )
            )

        # 豆包
        doubao_key = (settings.DOUBAO_API_KEY or "").strip()
        if doubao_key:
            self.providers.append(
                OfficialChatProvider(
                    name="doubao",
                    base_url=(settings.DOUBAO_BASE_URL or "https://ark.cn-beijing.volces.com/api/v3").strip().rstrip("/"),
                    api_key=doubao_key,
                    default_model="doubao-pro-32k",
                )
            )

        # 智谱
        glm_key = (settings.GLM_API_KEY or "").strip()
        if glm_key:
            self.providers.append(
                OfficialChatProvider(
                    name="glm",
                    base_url=(settings.GLM_BASE_URL or "https://open.bigmodel.cn/api/paas/v4").strip().rstrip("/"),
                    api_key=glm_key,
                    default_model="glm-4-flash",
                )
            )

        # Claude
        claude_key = (settings.CLAUDE_API_KEY or "").strip()
        if claude_key:
            self.providers.append(
                OfficialChatProvider(
                    name="claude",
                    base_url=(settings.CLAUDE_BASE_URL or "https://api.anthropic.com/v1").strip().rstrip("/"),
                    api_key=claude_key,
                    default_model="claude-3-5-sonnet-20241022",
                )
            )

        # Gemini
        gemini_key = (settings.GEMINI_API_KEY or "").strip()
        if gemini_key:
            self.providers.append(
                OfficialChatProvider(
                    name="gemini",
                    base_url=(settings.GEMINI_BASE_URL or "https://generativelanguage.googleapis.com/v1").strip().rstrip("/"),
                    api_key=gemini_key,
                    default_model="gemini-2.5-flash",
                )
            )

    def resolve_provider(self, model: str) -> OfficialChatProvider | None:
        """根据模型名称解析对应提供商。"""
        lower = model.lower()
        for provider in self.providers:
            if lower.startswith(provider.name):
                return provider
            if provider.default_model and lower == provider.default_model.lower():
                return provider
        return None

    async def complete(
        self,
        *,
        model: str,
        messages: list[dict[str, str]],
        temperature: float,
        max_tokens: int,
    ) -> dict[str, object]:
        """完成一次对话请求。"""
        provider = self.resolve_provider(model)
        if provider is None:
            raise OfficialChatServiceError(
                f"模型 {model} 未匹配到已配置的官方提供商",
                "OFFICIAL_CHAT_PROVIDER_NOT_FOUND",
                status_code=400,
            )

        if provider.name == "gemini":
            return await self._gemini_complete(provider, model, messages, temperature, max_tokens)

        payload = {
            "model": model,
            "messages": self._filter_messages(messages),
            "temperature": temperature,
            "max_tokens": max_tokens,
        }

        url = f"{provider.base_url}/chat/completions"
        headers = {
            "Authorization": f"Bearer {provider.api_key}",
            "Content-Type": "application/json",
        }

        try:
            async with httpx.AsyncClient(timeout=120.0) as client:
                response = await client.post(url, headers=headers, json=payload)
                response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise OfficialChatServiceError(
                f"{provider.name} 接口返回错误：HTTP {exc.response.status_code}",
                "OFFICIAL_CHAT_HTTP_ERROR",
                status_code=exc.response.status_code,
            ) from exc
        except httpx.HTTPError as exc:
            raise OfficialChatServiceError(
                f"无法连接 {provider.name} 接口：{exc}",
                "OFFICIAL_CHAT_REQUEST_FAILED",
                status_code=502,
            ) from exc

        data = response.json()
        if not isinstance(data, dict):
            raise OfficialChatServiceError(
                f"{provider.name} 响应格式不合法",
                "OFFICIAL_CHAT_RESPONSE_INVALID",
                status_code=502,
            )

        return {
            "model": model,
            "content": self._extract_content(data),
            "provider": provider.name,
            "raw": data,
        }

    async def _gemini_complete(
        self,
        provider: OfficialChatProvider,
        model: str,
        messages: list[dict[str, str]],
        temperature: float,
        max_tokens: int,
    ) -> dict[str, object]:
        """调用 Gemini 的 generateContent 接口。"""
        contents = self._gemini_contents(messages)
        url = f"{provider.base_url}/models/{model}:generateContent?key={provider.api_key}"
        payload = {
            "contents": contents,
            "generationConfig": {
                "temperature": temperature,
                "maxOutputTokens": max_tokens,
            },
        }

        try:
            async with httpx.AsyncClient(timeout=120.0) as client:
                response = await client.post(url, json=payload, headers={"Content-Type": "application/json"})
                response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise OfficialChatServiceError(
                f"Gemini 接口返回错误：HTTP {exc.response.status_code}",
                "OFFICIAL_CHAT_HTTP_ERROR",
                status_code=exc.response.status_code,
            ) from exc
        except httpx.HTTPError as exc:
            raise OfficialChatServiceError(
                f"无法连接 Gemini 接口：{exc}",
                "OFFICIAL_CHAT_REQUEST_FAILED",
                status_code=502,
            ) from exc

        data = response.json()
        content = self._extract_gemini_content(data)
        return {
            "model": model,
            "content": content,
            "provider": "gemini",
            "raw": data,
        }

    @staticmethod
    def _filter_messages(messages: list[dict[str, str]]) -> list[dict[str, str]]:
        """过滤消息，确保 role 字段合法。"""
        valid_roles = {"system", "user", "assistant"}
        return [msg for msg in messages if msg.get("role") in valid_roles]

    @staticmethod
    def _gemini_contents(messages: list[dict[str, str]]) -> list[dict[str, Any]]:
        """将 OpenAI 格式消息转换为 Gemini 格式。"""
        contents = []
        for msg in messages:
            role = msg.get("role", "user")
            text = msg.get("content", "")
            if role == "system":
                contents.append({
                    "role": "user",
                    "parts": [{"text": f"[System Instruction]\n{text}"}],
                })
                contents.append({
                    "role": "model",
                    "parts": [{"text": "Understood. I will follow these instructions."}],
                })
            else:
                gemini_role = "model" if role == "assistant" else "user"
                contents.append({
                    "role": gemini_role,
                    "parts": [{"text": text}],
                })
        return contents

    @staticmethod
    def _extract_content(data: dict[str, object]) -> str:
        """从 OpenAI 兼容响应中提取文本内容。"""
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
    def _extract_gemini_content(data: dict[str, Any]) -> str:
        """从 Gemini 响应中提取文本内容。"""
        try:
            candidates = data.get("candidates", [])
            if candidates:
                content = candidates[0].get("content", {})
                parts = content.get("parts", [])
                if parts:
                    return parts[0].get("text", "")
        except Exception:
            pass
        return ""


official_chat_service = OfficialChatService()