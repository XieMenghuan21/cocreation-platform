"""RequirementAgent：用自然语言逐轮构建结构化工业设计需求。

目标：
- 用户不用填表；每轮只追问一个最影响设计方向的问题。
- 输出结构化 requirement，供 Design / Render / 3D / CAD / Quote Agent 复用。
- LLM 失败时退化为稳定的启发式合并，不阻断 Conversation。
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any

from app.services.official_chat_service import OfficialChatServiceError, official_chat_service

logger = logging.getLogger(__name__)

_SYSTEM = """你是工业设计项目中的需求 Agent。你的工作不是一次性让用户填表，而是把自然语言逐轮整理成结构化需求，并且每轮最多追问一个真正影响设计方向的问题。
只能输出 JSON，不要 markdown，不要解释。"""

_PROMPT = """当前结构化需求：
{previous}

本轮用户输入：
{latest}

首轮意图识别结果（可能为空）：
{intent}

输出 JSON：
{{
  "requirement": {{
    "productCategory": "",
    "productDescription": "",
    "targetUser": "",
    "scenario": "",
    "style": [],
    "materials": [],
    "dimensions": {{}},
    "targetPrice": null,
    "features": [],
    "constraints": [],
    "brand": "",
    "references": []
  }},
  "completeness": 0,
  "criticalUnknown": "",
  "question": "",
  "canProceed": false,
  "summary": ""
}}

规则：
1. previous 中已经明确的信息必须保留，除非用户本轮明确修改。
2. 不要虚构尺寸、预算、材料等事实；可以为空。
3. completeness 是 0-100 的整数，表示是否足以开始提出设计方向，不是工程资料完整度。
4. 只要产品是什么、核心场景/用户、主要风格或目标至少有基本信息，通常 completeness 可以达到 60 以上并允许先出设计方向。
5. question 最多一个问题，必须是当前最影响设计方向的未知项；canProceed=true 时 question 可以为空。
6. summary 用 1-3 句话给后续设计 Agent，简洁但保留关键约束。
"""


class RequirementAgent:
    def __init__(self, model: str | None = None) -> None:
        self.model = model

    def _resolve_model(self) -> str:
        if self.model:
            return self.model
        from app.config.settings import settings

        return (getattr(settings, "DEEPSEEK_MODEL", "") or "deepseek-v4-flash").strip() or "deepseek-v4-flash"

    @staticmethod
    def _json_object(text: str) -> dict[str, Any]:
        cleaned = text.strip()
        if cleaned.startswith("```"):
            cleaned = "\n".join(line for line in cleaned.splitlines() if not line.strip().startswith("```"))
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start < 0 or end <= start:
            raise ValueError("RequirementAgent 响应不是 JSON")
        fragment = cleaned[start : end + 1]
        try:
            return json.loads(fragment)
        except json.JSONDecodeError:
            fragment = re.sub(r",\s*([}\]])", r"\1", fragment)
            return json.loads(fragment)

    @staticmethod
    def _normalize_requirement(value: object) -> dict[str, Any]:
        raw = value if isinstance(value, dict) else {}
        result: dict[str, Any] = {
            "productCategory": str(raw.get("productCategory") or ""),
            "productDescription": str(raw.get("productDescription") or ""),
            "targetUser": str(raw.get("targetUser") or ""),
            "scenario": str(raw.get("scenario") or ""),
            "style": raw.get("style") if isinstance(raw.get("style"), list) else [],
            "materials": raw.get("materials") if isinstance(raw.get("materials"), list) else [],
            "dimensions": raw.get("dimensions") if isinstance(raw.get("dimensions"), dict) else {},
            "targetPrice": raw.get("targetPrice"),
            "features": raw.get("features") if isinstance(raw.get("features"), list) else [],
            "constraints": raw.get("constraints") if isinstance(raw.get("constraints"), list) else [],
            "brand": str(raw.get("brand") or ""),
            "references": raw.get("references") if isinstance(raw.get("references"), list) else [],
        }
        return result

    @staticmethod
    def _fallback(
        previous: dict[str, Any],
        latest: str,
        intent: dict[str, Any] | None,
    ) -> dict[str, Any]:
        requirement = RequirementAgent._normalize_requirement(previous)
        intent = intent or {}
        if not requirement["productCategory"]:
            requirement["productCategory"] = str(intent.get("projectName") or "")
        if not requirement["productDescription"]:
            requirement["productDescription"] = str(intent.get("requirementText") or latest)
        elif latest and latest not in requirement["productDescription"]:
            requirement["productDescription"] = f"{requirement['productDescription']}；{latest}".strip("；")

        lower = latest.lower()
        scenes = ["户外", "露营", "办公", "家居", "厨房", "卧室", "客厅", "工业", "门店", "车载", "旅行"]
        styles = ["极简", "简约", "科技", "未来", "复古", "工业风", "户外装备", "高端", "圆润", "几何", "机能"]
        materials = ["铝合金", "不锈钢", "塑料", "abs", "木材", "玻璃", "陶瓷", "皮革", "硅胶"]
        scene = next((item for item in scenes if item in latest), None)
        if scene:
            requirement["scenario"] = scene
        for item in styles:
            if item.lower() in lower and item not in requirement["style"]:
                requirement["style"].append(item)
        for item in materials:
            if item.lower() in lower and item not in requirement["materials"]:
                requirement["materials"].append(item)
        budget = re.search(r"(?:预算|价格|成本|售价)[^\d]{0,5}(\d+(?:\.\d+)?\s*(?:万)?元)", latest)
        if budget:
            requirement["targetPrice"] = budget.group(1)
        dimension = re.search(r"\d+(?:\.\d+)?\s*(?:mm|cm|毫米|厘米|米)", latest, re.I)
        if dimension:
            requirement["dimensions"] = {"summary": dimension.group(0)}

        score = 25
        if requirement["productCategory"] or requirement["productDescription"]:
            score += 25
        if requirement["scenario"] or requirement["targetUser"]:
            score += 20
        if requirement["style"] or requirement["features"]:
            score += 20
        if requirement["materials"] or requirement["dimensions"] or requirement["targetPrice"]:
            score += 10
        score = min(score, 100)

        if not requirement["scenario"] and not requirement["targetUser"]:
            unknown = "核心使用场景"
            question = "这款产品最主要会在什么场景下使用？"
        elif not requirement["style"]:
            unknown = "设计调性"
            question = "你希望它更偏哪一种感觉，例如极简、科技、户外装备感或高端生活方式？"
        else:
            unknown = ""
            question = ""
        can_proceed = score >= 60 and bool(requirement["style"] or requirement["scenario"])
        summary_parts = [
            requirement["productDescription"] or requirement["productCategory"],
            f"场景：{requirement['scenario']}" if requirement["scenario"] else "",
            f"风格：{' / '.join(str(x) for x in requirement['style'])}" if requirement["style"] else "",
            f"材质：{' / '.join(str(x) for x in requirement['materials'])}" if requirement["materials"] else "",
        ]
        return {
            "requirement": requirement,
            "completeness": score,
            "criticalUnknown": unknown,
            "question": question,
            "canProceed": can_proceed,
            "summary": "；".join(x for x in summary_parts if x),
        }

    async def analyze(
        self,
        *,
        previous: dict[str, Any] | None,
        latest: str,
        intent: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        previous = previous or {}
        try:
            result = await official_chat_service.complete(
                model=self._resolve_model(),
                messages=[
                    {"role": "system", "content": _SYSTEM},
                    {
                        "role": "user",
                        "content": _PROMPT.format(
                            previous=json.dumps(previous, ensure_ascii=False),
                            latest=latest,
                            intent=json.dumps(intent or {}, ensure_ascii=False),
                        ),
                    },
                ],
                temperature=0.2,
                max_tokens=1400,
            )
            payload = self._json_object(str(result.get("content") or ""))
            requirement = self._normalize_requirement(payload.get("requirement"))
            completeness = max(0, min(100, int(payload.get("completeness") or 0)))
            summary = str(payload.get("summary") or "").strip()
            if not summary:
                summary = requirement.get("productDescription") or latest
            return {
                "requirement": requirement,
                "completeness": completeness,
                "criticalUnknown": str(payload.get("criticalUnknown") or ""),
                "question": str(payload.get("question") or ""),
                "canProceed": bool(payload.get("canProceed")),
                "summary": summary,
            }
        except (OfficialChatServiceError, ValueError, TypeError, json.JSONDecodeError) as exc:
            logger.warning("RequirementAgent fallback: %s", exc)
            return self._fallback(previous, latest, intent)


requirement_agent = RequirementAgent()
