"""意图识别与需求结构化服务。

将用户的自然语言输入解析为结构化需求，供 AI 共创工作台驱动项目创建与工作流编排。
"""
from __future__ import annotations

import json
import logging
from typing import Any, Literal

from app.services.official_chat_service import (
    OfficialChatServiceError,
    official_chat_service,
)

logger = logging.getLogger(__name__)

IntentKind = Literal["design", "propaganda", "production"]

_INTENT_LABELS: dict[IntentKind, str] = {
    "design": "设计",
    "propaganda": "宣发",
    "production": "生产",
}

_INTENT_PROMPT = """你是 AI 共创设计工作台的意图识别与需求分析师。
根据用户输入，判断业务意图并提取结构化需求，只输出 JSON，不要输出任何其他内容。

业务意图 intent 枚举：
- design      设计路线：从需求到方案，概念草图、外观设计、效果图、方案对比。用户想要设计一个新产品。
- propaganda  宣发路线：基于已有设计/商品图，一键生成宣传素材、电商主图、场景融合图、3D 爆炸图。
- production  生产路线：从方案到生产，CAD 图纸、BOM 清单、工艺路线、生产任务包。

输出 JSON 结构：
{
  "intent": "design | propaganda | production",
  "projectName": "简洁的项目名称（10字以内，去掉'设计一个'等前缀）",
  "industry": "所属行业，如 装备制造、汽车零部件、医疗器械、家居智造、消费电子、其他",
  "requirementText": "提炼后的需求描述（保留关键约束：材质、风格、尺寸、场景、预算等）",
  "needsMaterials": true,
  "suggestedOptions": {
    "generateDrawing": false,
    "generateRender": false,
    "generateCad": false,
    "generateExplosion": false,
    "enhanceImage": false,
    "generatePlanLine": false,
    "generateThreePreview": false
  },
  "reasoning": "一句话说明判断依据"
}

规则：
1. 只输出合法 JSON，禁止 markdown 代码块或前后缀文字
2. 判断意图时注意：
   - 用户说"设计/做一款/开发"新产品 → design
   - 用户说"宣传/海报/电商图/宣发/爆炸图/爆炸/分解/卖点图"等，针对已有产品或参考图 → propaganda 或 production
   - 用户说"爆炸图/分解图"且强调基于图片或商品图 → propaganda（generateExplosion=true）
   - 用户说"CAD/图纸/生产/BOM/工艺" → production（generateCad=true）
3. needsMaterials 规则：
   - intent=design 且用户没给出足够设计信息（材质/尺寸/场景等）→ needsMaterials=true
   - intent=design 且用户已给出完整设计信息 → needsMaterials=false
   - intent=propaganda 或 production → needsMaterials=false（基于参考图直接生成，无需补充材料）
4. suggestedOptions 根据意图调整：
   - design：generateDrawing=true, generateRender=true
   - propaganda 宣传图：generateRender=true, enhanceImage=true
   - propaganda 爆炸图：generateExplosion=true
   - production：generateCad=true, generateThreePreview=true
5. 用户输入若与工业设计无关（如闲聊、问天气），intent 仍取 design 但 requirementText 原样保留，reasoning 标注"无法识别为工业设计需求"
"""


class IntentServiceError(Exception):
    def __init__(self, message: str, error_code: str = "INTENT_SERVICE_ERROR", status_code: int = 502) -> None:
        super().__init__(message)
        self.message = message
        self.error_code = error_code
        self.status_code = status_code


def _extract_json(text: str) -> dict[str, Any]:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`")
        if cleaned.startswith("json"):
            cleaned = cleaned[4:]
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start == -1 or end == -1:
        raise ValueError("响应中未找到 JSON")
    return json.loads(cleaned[start : end + 1])


class IntentService:
    """封装意图识别与需求结构化调用。"""

    def __init__(
        self,
        *,
        model: str | None = None,
    ) -> None:
        self.model = model

    def _resolve_model(self) -> str:
        if self.model:
            return self.model
        from app.config.settings import settings

        return (getattr(settings, "DEEPSEEK_MODEL", "") or "deepseek-v4-flash").strip() or "deepseek-v4-flash"

    def configured(self) -> bool:
        from app.config.settings import settings

        return bool((settings.DEEPSEEK_API_KEY or "").strip())

    async def analyze(self, user_input: str) -> dict[str, Any]:
        """分析用户输入，返回结构化意图与需求。"""
        if not self.configured():
            raise IntentServiceError(
                "意图识别服务未配置（缺少 DEEPSEEK_API_KEY）",
                "INTENT_NOT_CONFIGURED",
                status_code=503,
            )
        if not (user_input or "").strip():
            raise IntentServiceError(
                "用户输入为空，无法分析意图",
                "INTENT_INPUT_EMPTY",
                status_code=400,
            )

        messages: list[dict[str, str]] = [
            {"role": "system", "content": _INTENT_PROMPT},
            {"role": "user", "content": (user_input or "").strip()[:4000]},
        ]
        try:
            result = await official_chat_service.complete(
                model=self._resolve_model(),
                messages=messages,
                temperature=0.2,
                max_tokens=1200,
            )
        except OfficialChatServiceError as exc:
            logger.warning("意图识别调用失败 error_code=%s", exc.error_code, exc_info=True)
            raise IntentServiceError(
                f"意图识别服务调用失败：{exc}",
                exc.error_code,
                status_code=exc.status_code,
            ) from exc

        content = str(result.get("content") or "").strip()
        if not content:
            raise IntentServiceError(
                "意图识别服务返回为空",
                "INTENT_EMPTY_RESPONSE",
                status_code=502,
            )
        try:
            parsed = _extract_json(content)
        except (TypeError, ValueError) as exc:
            logger.warning("意图识别响应解析失败 content=%s", content[:500])
            raise IntentServiceError(
                "意图识别响应解析失败",
                "INTENT_PARSE_FAILED",
                status_code=502,
            ) from exc

        intent = parsed.get("intent")
        if intent not in _INTENT_LABELS:
            intent = "design"
        parsed["intent"] = intent
        parsed["intentLabel"] = _INTENT_LABELS[intent]
        options = parsed.get("suggestedOptions") or {}
        if not isinstance(options, dict):
            options = {}
        for key in (
            "generateDrawing",
            "generateRender",
            "generateCad",
            "generateExplosion",
            "enhanceImage",
            "generatePlanLine",
            "generateThreePreview",
        ):
            options.setdefault(key, False)
        parsed["suggestedOptions"] = options
        needs_materials = parsed.get("needsMaterials")
        if not isinstance(needs_materials, bool):
            needs_materials = intent == "design"
        if intent in {"propaganda", "production"}:
            needs_materials = False
        parsed["needsMaterials"] = needs_materials
        return parsed

    async def parse_materials(self, text: str) -> dict[str, str]:
        """解析用户补充的一段材料描述为结构化材料字段。"""
        if not self.configured():
            raise IntentServiceError(
                "材料解析服务未配置（缺少 DEEPSEEK_API_KEY）",
                "INTENT_NOT_CONFIGURED",
                status_code=503,
            )
        if not (text or "").strip():
            raise IntentServiceError(
                "材料描述为空",
                "INTENT_INPUT_EMPTY",
                status_code=400,
            )

        system_prompt = """你是 AI 共创设计工作台的材料解析器。
用户会粘贴一段关于产品设计的补充材料描述（可能包含材质、尺寸、预算、使用场景、风格、特殊功能、品牌规范等）。
请从描述中提取结构化字段，只输出 JSON，不要任何其他内容。

输出 JSON 结构（字段可选，描述中未提到的字段省略）：
{
  "material": "材质，如 铝合金 / 北美胡桃木",
  "dimension": "尺寸要求，如 高30cm / 400x300x200mm",
  "budget": "预算，如 500元 / 20000元",
  "scene": "使用场景，如 书房 / 客厅 / 工业厂房",
  "style": "风格，如 简约 / 科技感",
  "feature": "特殊功能或约束",
  "brand": "品牌规范或调性"
}

规则：
1. 只输出合法 JSON，禁止 markdown 代码块
2. 提取后的值要简洁精炼，去掉冗长修饰
3. 描述中完全没提到的字段不输出该 key
4. 无法判断的字段不输出
"""

        messages: list[dict[str, str]] = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": (text or "").strip()[:4000]},
        ]
        try:
            result = await official_chat_service.complete(
                model=self._resolve_model(),
                messages=messages,
                temperature=0.1,
                max_tokens=600,
            )
        except OfficialChatServiceError as exc:
            logger.warning("材料解析调用失败 error_code=%s", exc.error_code, exc_info=True)
            raise IntentServiceError(
                f"材料解析服务调用失败：{exc}",
                exc.error_code,
                status_code=exc.status_code,
            ) from exc

        content = str(result.get("content") or "").strip()
        if not content:
            raise IntentServiceError(
                "材料解析服务返回为空",
                "INTENT_EMPTY_RESPONSE",
                status_code=502,
            )
        try:
            parsed = _extract_json(content)
        except (TypeError, ValueError) as exc:
            logger.warning("材料解析响应解析失败 content=%s", content[:500])
            raise IntentServiceError(
                "材料解析响应解析失败",
                "INTENT_PARSE_FAILED",
                status_code=502,
            ) from exc

        return {
            key: str(value).strip()
            for key, value in parsed.items()
            if isinstance(value, str) and value.strip()
        }


intent_service = IntentService()
