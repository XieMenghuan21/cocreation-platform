"""统一 AI 模型网关：集中管理模型目录、路由和调用。"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from sqlalchemy.orm import Session

from app.config.settings import settings
from app.types.json import JSONValue
from app.schemas.ai_chat import AIChatCompletionMessage, AIChatCompletionRequest
from app.schemas.forgecad import ForgeCadGenerateRequest, ForgeCadGenerateResult
from app.services.ai_chat_gateway_service import ai_chat_gateway_service
from app.services.cad_build123d_service import (
    Build123dServiceError,
    build123d_service,
)
from app.services.dashscope_image_service import DashScopeImageServiceError, dashscope_image_service
from app.services.forgecad_service import ForgeCadServiceError, forgecad_service
from app.services.image_prompt_optimizer_service import image_prompt_optimizer_service
from app.services.nodapi_catalog_service import nodapi_catalog_service
from app.services.nodapi_chat_service import nodapi_chat_service
from app.services.gemini_image_service import GeminiImageServiceError, gemini_image_service
from app.services.nodapi_image_service import NodApiImageServiceError, nodapi_image_service
from app.services.official_chat_service import official_chat_service
from app.services.zoo_design_service import zoo_design_service

ModelCapability = Literal["chat", "image", "cad_3d", "catalog"]
OpenAIChatMessage = dict[str, object]

ALLOWED_MODEL_SERIES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("deepseek", ("deepseek", "deep seek", "deep-seek", "深度求索")),
    ("qwen", ("qwen", "qianwen", "tongyi", "通义", "千问", "wanx", "wan2", "万相")),
    ("doubao", ("doubao", "dou bao", "豆包", "volcengine", "火山", "ark")),
    ("minimax", ("minimax", "mini max", "abab", "海螺")),
    ("gpt", ("gpt", "openai", "chatgpt", "o1", "o3", "o4")),
    ("gemini", ("gemini", "google")),
    ("claude", ("claude", "anthropic")),
    ("glm", ("glm", "zhipu", "智谱")),
)


class AIModelGatewayError(Exception):
    """统一模型网关可预期错误。"""

    def __init__(self, message: str, error_code: str = "AI_MODEL_GATEWAY_ERROR", status_code: int = 500) -> None:
        super().__init__(message)
        self.message = message
        self.error_code = error_code
        self.status_code = status_code


@dataclass(frozen=True)
class ModelRoute:
    """统一模型路由结果。"""

    capability: ModelCapability
    provider: str
    model: str
    reason: str


class AIModelGatewayService:
    """业务层唯一依赖的 AI 模型能力入口。"""

    def __init__(
        self,
        *,
        chat_gateway=ai_chat_gateway_service,
        official_chat_service=official_chat_service,
        nodapi_chat_service=nodapi_chat_service,
        nodapi_image_service=nodapi_image_service,
        dashscope_image_service=dashscope_image_service,
        forgecad_service=forgecad_service,
        zoo_design_service=zoo_design_service,
        build123d_service=build123d_service,
        nodapi_catalog_service=nodapi_catalog_service,
        prompt_optimizer_service=image_prompt_optimizer_service,
    ) -> None:
        self.chat_gateway = chat_gateway
        self.official_chat_service = official_chat_service
        self.nodapi_chat_service = nodapi_chat_service
        self.nodapi_image_service = nodapi_image_service
        self.dashscope_image_service = dashscope_image_service
        self.forgecad_service = forgecad_service
        self.zoo_design_service = zoo_design_service
        self.build123d_service = build123d_service
        self.nodapi_catalog_service = nodapi_catalog_service
        self.prompt_optimizer_service = prompt_optimizer_service

    def health(self) -> dict[str, object]:
        return {
            "service": "ai-model-gateway",
            "chat": self.chat_gateway.health(),
            "image": {
                "defaultProvider": "auto",
                "nodapiConfigured": bool(getattr(self.nodapi_image_service, "configured", False)),
                "dashscopeConfigured": bool(getattr(self.dashscope_image_service, "configured", False)),
            },
            "cad3d": {
                "preferredProvider": "local-forgecad",
                "forgecadBridgeConfigured": bool(str(getattr(self.forgecad_service, "bridge_base_url", "") or "").strip()),
                "zooConfigured": bool(getattr(self.zoo_design_service, "configured", False)),
                "build123dAvailable": bool(getattr(self.build123d_service, "available", False)),
                "build123dConfigured": bool(getattr(self.build123d_service, "configured", False)),
            },
        }

    async def async_health(self) -> dict[str, object]:
        return self.health()

    async def complete_chat(self, request: AIChatCompletionRequest, user: dict[str, object]) -> dict[str, object]:
        route = self.resolve_chat_route(request.model)
        messages = self._openai_messages(request.messages)
        if route.provider == "official":
            data = await self.official_chat_service.complete(
                model=route.model,
                messages=messages,
                temperature=request.temperature or 0.7,
                max_tokens=request.max_tokens or 1024,
            )
            data["provider"] = route.provider
            data["route"] = route.__dict__
            return data

        data = await self.nodapi_chat_service.complete(
            model=route.model,
            messages=messages,
            temperature=request.temperature or 0.7,
            max_tokens=request.max_tokens or 1024,
        )
        data["provider"] = route.provider
        data["route"] = route.__dict__
        return data

    async def openai_chat_completion(self, request: AIChatCompletionRequest, user: dict[str, object]) -> dict[str, object]:
        route = self.resolve_chat_route(request.model)
        result = await self.complete_chat(request, user)
        return {
            "id": f"chatcmpl_{route.provider}",
            "object": "chat.completion",
            "created": 0,
            "model": result.get("model") or route.model,
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": str(result.get("content") or "")},
                    "finish_reason": "stop",
                }
            ],
            "usage": result.get("raw", {}).get("usage") if isinstance(result.get("raw"), dict) else None,
        }

    async def stream_chat_completion(self, request: AIChatCompletionRequest, user: dict[str, object]):
        raise AIModelGatewayError("当前统一流式接口未启用", "STREAM_PROVIDER_UNSUPPORTED", 400)

    def resolve_chat_route(self, model: str | None) -> ModelRoute:
        normalized = (model or "default").strip()
        if not self.is_allowed_model(normalized):
            raise AIModelGatewayError(
                f"模型 {normalized} 不在允许的模型系列内",
                "AI_MODEL_NOT_ALLOWED",
                status_code=400,
            )
        if self.official_chat_service.resolve_provider(normalized) is not None:
            return ModelRoute("chat", "official", normalized, "用户显式选择官方模型且已配置 key")
        return ModelRoute("chat", "nodapi", normalized, "没有本地同名模型，回退到统一外部聚合 key")

    def resolve_chat_client_config(self, model: str | None) -> dict[str, str]:
        route = self.resolve_chat_route(model)
        if route.provider == "official":
            provider = self.official_chat_service.resolve_provider(route.model)
            if provider is None:
                raise AIModelGatewayError(f"模型 {route.model} 未匹配到官方直连服务", "OFFICIAL_CHAT_PROVIDER_NOT_FOUND", 400)
            return {
                "provider": provider.name,
                "base_url": provider.base_url,
                "api_key": provider.api_key,
                "model": provider.default_model,
            }
        return {
            "provider": "nodapi",
            "base_url": self.nodapi_chat_service._build_v1_url("").rstrip("/"),
            "api_key": str(getattr(self.nodapi_chat_service, "api_key", "")),
            "model": route.model,
        }

    async def generate_design_image(
        self,
        *,
        prompt: str,
        images: list[str] | None = None,
        model: str | None = None,
        provider: str | None = None,
        optimize_prompt: bool = True,
    ) -> dict[str, JSONValue]:
        provider_name = self._normalize_provider(provider or self._resolve_image_provider(model))

        # 自动回退：当解析出的 Provider 未配置时，按优先级尝试其他可用 Provider
        if not model and not provider and not self.image_provider_configured(provider_name):
            provider_name, model = self._resolve_auto_image_provider()

        prompt_meta = await self._prepare_image_prompt_meta(
            prompt=prompt,
            images=images,
            model=model,
            optimize_prompt=optimize_prompt,
        )
        final_prompt = str(prompt_meta["finalPrompt"])
        auto_retry = not provider
        provider_candidates = [provider_name]
        if auto_retry:
            for candidate in ("dashscope", "gemini", "nodapi"):
                if candidate != provider_name and self.image_provider_configured(candidate):
                    provider_candidates.append(candidate)

        last_error: Exception | None = None
        for candidate in provider_candidates:
            try:
                if candidate == "dashscope":
                    result = await self.dashscope_image_service.generate_design_image(prompt=final_prompt, images=images, model=model)
                elif candidate == "gemini":
                    result = await gemini_image_service.generate_design_image(prompt=final_prompt, images=images, model=model)
                elif candidate == "nodapi":
                    result = await self.nodapi_image_service.generate_design_image(prompt=final_prompt, images=images, model=model)
                else:
                    raise NodApiImageServiceError(
                        f"未知图片生成服务：{candidate}",
                        "NODAPI_IMAGE_PROVIDER_UNSUPPORTED",
                        status_code=503,
                    )

                normalized_result = dict(result)
                normalized_result["resultUrl"] = str(
                    normalized_result.get("resultUrl")
                    or normalized_result.get("imageUrl")
                    or ""
                ).strip()
                normalized_result["provider"] = candidate
                normalized_result["promptMeta"] = prompt_meta
                return normalized_result
            except (NodApiImageServiceError, DashScopeImageServiceError, GeminiImageServiceError) as exc:
                last_error = exc
                if not auto_retry:
                    raise
                continue

        if last_error is not None:
            raise last_error

        raise NodApiImageServiceError(
            f"未知图片生成服务：{provider}",
            "NODAPI_IMAGE_PROVIDER_UNSUPPORTED",
            status_code=503,
        )

    async def _prepare_image_prompt_meta(
        self,
        *,
        prompt: str,
        images: list[str] | None,
        model: str | None,
        optimize_prompt: bool,
    ) -> dict[str, JSONValue]:
        clean_prompt = prompt.strip()
        meta = await self.prompt_optimizer_service.optimize(prompt=clean_prompt, images=images, model=model)
        optimized_prompt = str(meta.get("optimizedPrompt") or clean_prompt)
        references = meta.get("references")
        final_prompt = optimized_prompt if optimize_prompt else clean_prompt
        return {
            "originalPrompt": clean_prompt,
            "optimizedPrompt": optimized_prompt,
            "finalPrompt": final_prompt,
            "enabled": bool(optimize_prompt),
            "aiOptimized": bool(meta.get("aiOptimized")),
            "references": references if isinstance(references, list) else [],
        }

    def image_provider_label(self) -> str:
        providers = []
        if self.gemini_image_configured():
            providers.append("Gemini")
        if self.dashscope_image_configured():
            providers.append("DashScope")
        if self.nodapi_image_configured():
            providers.append("NodAPI")
        return " + ".join(providers) if providers else "未配置"

    def image_configured(self) -> bool:
        return self.dashscope_image_configured() or self.gemini_image_configured() or self.nodapi_image_configured()

    def nodapi_image_configured(self) -> bool:
        return bool(getattr(self.nodapi_image_service, "configured", False))

    def gemini_image_configured(self) -> bool:
        return bool(getattr(gemini_image_service, "configured", False))

    def dashscope_image_configured(self) -> bool:
        return bool(getattr(self.dashscope_image_service, "configured", False))

    def image_provider_configured(self, provider: str | None = None) -> bool:
        provider_name = self._normalize_provider(provider or "nodapi")
        if provider_name == "dashscope":
            return self.dashscope_image_configured()
        if provider_name == "gemini":
            return self.gemini_image_configured()
        if provider_name == "nodapi":
            return self.nodapi_image_configured()
        return False

    async def generate_cad(
        self,
        request: ForgeCadGenerateRequest,
        *,
        db: Session,
        user_id: str,
        task_id: str | None = None,
        publish_assets: bool = True,
    ) -> ForgeCadGenerateResult:
        return await self.forgecad_service.generate(
            request,
            db=db,
            user_id=user_id,
            task_id=task_id,
            publish_assets=publish_assets,
        )

    async def create_text_to_cad(
        self,
        *,
        prompt: str,
        project_name: str,
        model_version: str | None = None,
    ) -> dict[str, JSONValue]:
        return await self.zoo_design_service.create_text_to_cad(
            prompt=prompt,
            project_name=project_name,
            model_version=model_version,
        )

    def cad3d_fallback_configured(self) -> bool:
        return bool(getattr(self.zoo_design_service, "configured", False))

    async def get_catalog_snapshot(self) -> dict[str, JSONValue]:
        models: list[dict[str, JSONValue]] = []
        models.extend(self._gemini_model_catalog(self.gemini_image_configured()))
        models.extend(self._nodapi_image_model_catalog(self.nodapi_image_configured()))
        models.extend(self._dashscope_image_model_catalog(self.dashscope_image_configured()))
        models.extend(self._official_chat_catalog())
        raw_snapshots: list[dict[str, JSONValue]] = []

        models = self._curate_catalog_models(models)
        counts: dict[str, int] = {}
        for model in models:
            expected_type = str(model.get("expectedType") or "unknown")
            counts[expected_type] = counts.get(expected_type, 0) + 1

        return {
            "provider": "unified",
            "configured": True,
            "imageConfigured": self.image_configured(),
            "imageProvider": self.image_provider_label(),
            "imageProviders": {
                "dashscope": self.dashscope_image_configured(),
                "gemini": self.gemini_image_configured(),
                "nodapi": self.nodapi_image_configured(),
            },
            "balance": None,
            "unit": "模型",
            "apiKeyQuota": {},
            "totalModels": len(models),
            "counts": counts,
            "models": models,
            "rawModels": raw_snapshots,
        }

    @classmethod
    def is_allowed_model(cls, model: str) -> bool:
        return cls._match_allowed_series(model) is not None

    @classmethod
    def is_allowed_catalog_model(cls, item: dict[str, JSONValue]) -> bool:
        haystack = " ".join(
            str(value or "")
            for value in (
                item.get("id"),
                item.get("label"),
                item.get("platformName"),
                item.get("platformDisplayName"),
                item.get("provider"),
                item.get("description"),
                item.get("modelType"),
                " ".join(str(tag) for tag in item.get("tags", []) if tag is not None)
                if isinstance(item.get("tags"), list)
                else item.get("tags"),
            )
        )
        return cls._match_allowed_series(haystack) is not None

    @staticmethod
    def _normalize_text(value: str) -> str:
        return value.strip().lower().replace("_", "-")

    @classmethod
    def _match_allowed_series(cls, value: str) -> str | None:
        normalized = cls._normalize_text(value)
        if not normalized:
            return None
        for series, keywords in ALLOWED_MODEL_SERIES:
            if any(keyword in normalized for keyword in keywords):
                return series
        return None

    @classmethod
    def _openai_messages(cls, messages: list[AIChatCompletionMessage]) -> list[OpenAIChatMessage]:
        return [
            {
                "role": message.role,
                "content": cls._openai_content(message),
            }
            for message in messages
        ]

    @staticmethod
    def _openai_content(message: AIChatCompletionMessage) -> str | list[dict[str, object]]:
        if isinstance(message.content, str):
            if not message.images:
                return message.content
            parts: list[dict[str, object]] = [{"type": "text", "text": message.content}]
        else:
            parts = [part.model_dump() for part in message.content]

        parts.extend({"type": "image_url", "image_url": {"url": image}} for image in message.images)
        return parts

    @classmethod
    def _has_image_content(cls, messages: list[AIChatCompletionMessage]) -> bool:
        return any(cls._message_has_image(message) for message in messages)

    @staticmethod
    def _message_has_image(message: AIChatCompletionMessage) -> bool:
        if message.images:
            return True
        if isinstance(message.content, str):
            return False
        return any(part.type == "image_url" for part in message.content)

    @classmethod
    def _text_only_request(cls, request: AIChatCompletionRequest) -> AIChatCompletionRequest:
        messages = [
            AIChatCompletionMessage(role=message.role, content=cls._message_text(message))
            for message in request.messages
        ]
        return request.model_copy(update={"messages": messages})

    @staticmethod
    def _message_text(message: AIChatCompletionMessage) -> str:
        if isinstance(message.content, str):
            return message.content
        return "\n".join(part.text for part in message.content if part.type == "text").strip()

    def _gemini_model_catalog(self, connected: bool) -> list[dict[str, JSONValue]]:
        if not connected:
            return []
        model_id = str(getattr(settings, "GEMINI_IMAGE_MODEL", "") or "gemini-2.0-flash-exp").strip()
        return [
            {
                "id": model_id,
                "label": "Gemini 图片生成",
                "expectedType": "image",
                "connected": connected,
                "platformName": model_id,
                "platformDisplayName": "Gemini 图片生成",
                "platformType": "image",
                "description": "Google Gemini 图片生成模型，适合创意设计和产品渲染" if connected else "Gemini 图片服务未配置",
                "tags": ["gemini", "google", "image", "gemini"],
                "provider": "gemini",
            }
        ]

    # 官方直连模型候选列表：模型ID、显示名、描述
    _OFFICIAL_CHAT_CANDIDATES: tuple[tuple[str, str, str], ...] = (
        ("deepseek-chat", "DeepSeek Chat", "DeepSeek 通用对话模型，适合产业问答和文档总结"),
        ("deepseek-reasoner", "DeepSeek Reasoner", "DeepSeek 推理增强模型，擅长复杂逻辑分析"),
        ("qwen-plus", "通义千问 Plus", "阿里云通义千问增强版，中文场景友好"),
        ("qwen-max", "通义千问 Max", "阿里云通义千问旗舰版，综合能力最强"),
        ("qwen-turbo", "通义千问 Turbo", "阿里云通义千问轻量高速版"),
        ("qwen-coder", "通义千问 Coder", "阿里云代码专用模型，适合编程与审查"),
        ("gpt-4o", "GPT-4o", "OpenAI 多模态旗舰模型"),
        ("gpt-4o-mini", "GPT-4o Mini", "OpenAI 轻量高效模型"),
        ("doubao-pro-32k", "豆包 Pro 32K", "字节跳动豆包专业版，长上下文理解"),
        ("glm-4-plus", "GLM-4 Plus", "智谱 GLM-4 增强版"),
        ("glm-4-flash", "GLM-4 Flash", "智谱 GLM-4 轻量高速版"),
        ("claude-3-5-sonnet-20241022", "Claude 3.5 Sonnet", "Anthropic Claude 3.5，擅长创意与长文写作"),
        ("gemini-2.5-pro", "Gemini 2.5 Pro", "Google Gemini 2.5 Pro，高阶推理与多模态"),
        ("gemini-2.5-flash", "Gemini 2.5 Flash", "Google Gemini 2.5 Flash，轻量高速"),
    )

    def _official_chat_catalog(self) -> list[dict[str, JSONValue]]:
        """列出所有已配置官方 API Key 的直连模型。"""
        models: list[dict[str, JSONValue]] = []
        for model_id, display_name, description in self._OFFICIAL_CHAT_CANDIDATES:
            provider = self.official_chat_service.resolve_provider(model_id)
            if provider is None:
                continue
            models.append({
                "id": model_id,
                "label": display_name,
                "expectedType": "code" if "coder" in model_id.lower() else "chat",
                "connected": True,
                "platformName": model_id,
                "platformDisplayName": display_name,
                "platformType": "chat",
                "description": f"{description}（官方直连）",
                "tags": [model_id.split("-")[0], provider.name, "官方直连"],
                "provider": provider.name,
            })
        return models

    def _nodapi_image_model_catalog(self, connected: bool) -> list[dict[str, JSONValue]]:
        if not connected:
            return []
        model_id = str(getattr(settings, "NODAPI_IMAGE_MODEL", "") or "gpt-image-2").strip()
        return [
            {
                "id": model_id,
                "label": "GPT Image 2（NodAPI）",
                "expectedType": "image",
                "connected": connected,
                "platformName": model_id,
                "platformDisplayName": "GPT Image 2（NodAPI）",
                "platformType": "image",
                "description": "OpenAI 图片生成与编辑模型，适合产品图和视觉增强" if connected else "NodAPI 图片服务未配置",
                "tags": ["gpt", "openai", "image", "nodapi"],
                "provider": "nodapi",
            }
        ]

    def _dashscope_image_model_catalog(self, connected: bool) -> list[dict[str, JSONValue]]:
        if not connected:
            return []
        return [
            {
                "id": "qwen-image-max",
                "label": "通义万相（DashScope）",
                "expectedType": "image",
                "connected": connected,
                "platformName": "qwen-image-max",
                "platformDisplayName": "通义万相（DashScope）",
                "platformType": "image",
                "description": "阿里云通义万相图片生成模型" if connected else "DashScope 图片服务未配置",
                "tags": ["dashscope", "aliyun", "qwen-image", "image"],
                "provider": "dashscope",
            }
        ]

    @classmethod
    def _curate_catalog_models(cls, models: list[dict[str, JSONValue]]) -> list[dict[str, JSONValue]]:
        curated: dict[tuple[str, str], dict[str, JSONValue]] = {}
        for model in models:
            key = cls._catalog_curation_key(model)
            current = curated.get(key)
            if current is None or cls._catalog_rank(model) > cls._catalog_rank(current):
                curated[key] = model
        return sorted(curated.values(), key=cls._catalog_sort_key)

    @classmethod
    def _catalog_resolved_type(cls, item: dict[str, JSONValue]) -> str:
        text = cls._normalize_text(cls._catalog_text(item))
        expected = str(item.get("expectedType") or "chat").lower()
        if any(keyword in text for keyword in ("coder", "codex", "code-", "-code", "代码")):
            return "code"
        return expected

    @classmethod
    def _is_local_catalog_item(cls, item: dict[str, JSONValue]) -> bool:
        provider = str(item.get("provider") or "").lower()
        text = cls._catalog_text(item)
        normalized = cls._normalize_text(text)
        return "本底" in text or "57服务器" in text or "local" in normalized

    @classmethod
    def _catalog_curation_key(cls, item: dict[str, JSONValue]) -> tuple[str, str]:
        expected_type = cls._catalog_resolved_type(item)
        if expected_type == "code":
            return ("code", "latest")
        family = cls._match_allowed_series(cls._catalog_text(item)) or "other"
        if cls._is_local_catalog_item(item):
            model_id = str(item.get("id") or item.get("platformName") or "")
            return (expected_type, f"local:{family}:{model_id}")
        return (expected_type, family)

    @classmethod
    def _catalog_sort_key(cls, item: dict[str, JSONValue]) -> tuple[int, int, str]:
        expected_type = cls._catalog_resolved_type(item)
        family = cls._match_allowed_series(cls._catalog_text(item)) or "other"
        type_order = {"chat": 10, "code": 20, "image": 30, "retrieval": 40, "multimodal": 50}
        family_order = {"qwen": 10, "deepseek": 20, "doubao": 30, "gpt": 40, "gemini": 50, "claude": 60, "minimax": 70, "glm": 80, "local": 90}
        return (type_order.get(expected_type, 99), family_order.get(family, 99), str(item.get("platformDisplayName") or item.get("label") or item.get("id") or ""))

    @classmethod
    def _catalog_rank(cls, item: dict[str, JSONValue]) -> tuple[int, str]:
        text = cls._normalize_text(cls._catalog_text(item))
        score = 0
        if "57服务器" in cls._catalog_text(item) or "local" in text or "本地" in text:
            score += 10000
        for keyword, value in (
            ("v4pro", 9000),
            ("v4-pro", 9000),
            ("flash", 8500),
            ("latest", 6000),
            ("max", 5000),
            ("pro", 4500),
            ("coder", 4000),
            ("code", 3900),
        ):
            if keyword in text:
                score += value
        for number in ("5.5", "5.4", "4.1", "4", "3.1", "3", "2.7", "2.5", "2"):
            if number in text:
                score += int(float(number) * 100)
                break
        return (score, text)

    @staticmethod
    def _catalog_text(item: dict[str, JSONValue]) -> str:
        tags = item.get("tags")
        return " ".join(
            str(value or "")
            for value in (
                item.get("id"),
                item.get("label"),
                item.get("platformName"),
                item.get("platformDisplayName"),
                item.get("provider"),
                item.get("description"),
                item.get("modelType"),
                " ".join(str(tag) for tag in tags if tag is not None) if isinstance(tags, list) else tags,
            )
        )

    @staticmethod
    def _normalize_provider(provider: str) -> str:
        normalized = provider.strip().lower().replace("-", "_")
        if normalized in {"", "nodapi", "midjourney", "mj"}:
            return "nodapi"
        if normalized in {"gemini", "google"}:
            return "gemini"
        if normalized in {"dashscope", "qwen", "qianwen", "tongyi", "aliyun", "ali", "阿里云"}:
            return "dashscope"
        return normalized

    @classmethod
    def _resolve_image_provider(cls, model: str | None) -> str:
        normalized = cls._normalize_text(model or "")
        if any(keyword in normalized for keyword in ("wanx", "万相", "qwen-image")):
            return "dashscope"
        if any(keyword in normalized for keyword in ("gemini", "google", "imagen")):
            return "gemini"
        return "nodapi"

    def _resolve_auto_image_provider(self) -> tuple[str, str | None]:
        """自动模式下按优先级选择已配置的图片生成 Provider，返回 (provider_name, model)。"""
        if self.dashscope_image_configured():
            return ("dashscope", "qwen-image-max")
        if self.gemini_image_configured():
            return ("gemini", str(getattr(settings, "GEMINI_IMAGE_MODEL", "gemini-2.0-flash-exp") or "gemini-2.0-flash-exp").strip())
        if self.nodapi_image_configured():
            return ("nodapi", None)
        return ("nodapi", None)

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


ai_model_gateway_service = AIModelGatewayService()
