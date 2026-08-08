"""DesignAgent：根据需求生成 A / B / C 三个设计方向节点。"""
from __future__ import annotations

import json
import logging
from typing import Any

from app.services.official_chat_service import (
    OfficialChatServiceError,
    official_chat_service,
)

logger = logging.getLogger(__name__)

_DESIGN_DIRECTION_PROMPT = """你是资深工业设计师。根据产品需求，给出 3 个差异化设计方向。
只输出 JSON，不要输出任何其他内容。

需求：{product}
项目名称：{project_name}

输出 JSON 结构：
{{
  "directions": [
    {{
      "key": "A",
      "name": "方向A名称（6字内）",
      "summary": "一句话描述设计理念（20字内）",
      "styleKeywords": ["造型关键词1", "造型关键词2"],
      "cmf": "材质-色彩-表面处理描述（用于生图，20字内）",
      "imagePrompt": "用于渲染生成的设计稿描述，含产品名+造型+材质+多视图（中文，40字内）"
    }},
    {{
      "key": "B",
      "name": "方向B名称",
      "summary": "一句话设计理念",
      "styleKeywords": ["关键词"],
      "cmf": "材质-色彩-表面处理",
      "imagePrompt": "设计稿描述"
    }},
    {{
      "key": "C",
      "name": "方向C名称",
      "summary": "一句话设计理念",
      "styleKeywords": ["关键词"],
      "cmf": "材质-色彩-表面处理",
      "imagePrompt": "设计稿描述"
    }}
  ]
}}

规则：
1. 三个方向在造型语言上明显不同（如极简/圆润/几何/机能/复古等）
2. imagePrompt 是中文设计稿描述，直接可供文生图使用，以"{product}设计稿"格式开头，含造型+材质关键词+多视图描述（中文，60字内）
3. 贴合行业与产品品类，避免天马行空的抽象概念
"""


class DesignAgentError(Exception):
    pass


class DesignAgent:
    _MAX_RETRIES = 2

    def __init__(self, model: str | None = None) -> None:
        self.model = model

    def _resolve_model(self) -> str:
        if self.model:
            return self.model
        from app.config.settings import settings

        return (getattr(settings, "DEEPSEEK_MODEL", "") or "deepseek-v4-flash").strip() or "deepseek-v4-flash"

    @staticmethod
    def _extract_json(text: str) -> dict[str, Any]:
        cleaned = text.strip()
        # Remove fenced code blocks
        if cleaned.startswith("```"):
            lines = cleaned.split("\n")
            content_lines = [l for l in lines if not l.startswith("```")]
            cleaned = "\n".join(content_lines).strip()
        if cleaned.lower().startswith("json"):
            cleaned = cleaned[4:].strip()
        # Find outermost JSON object
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start == -1 or end == -1 or start >= end:
            raise ValueError("响应中未找到 JSON")
        try:
            return json.loads(cleaned[start : end + 1])
        except json.JSONDecodeError as exc:
            # Try repairing: remove trailing comma before closing brace/bracket
            fragment = cleaned[start : end + 1]
            import re
            repaired = re.sub(r",\s*([}\]])", r"\1", fragment)
            try:
                return json.loads(repaired)
            except json.JSONDecodeError:
                raise ValueError(f"JSON 解析失败：{exc.msg or str(exc)}") from exc

    async def _try_generate(self, *, requirement: str, project_name: str, temperature: float) -> dict[str, Any]:
        prompt = _DESIGN_DIRECTION_PROMPT.format(
            product=requirement,
            project_name=project_name,
        )
        result = await official_chat_service.complete(
            model=self._resolve_model(),
            messages=[
                {"role": "system", "content": "你是工业设计总监。只能输出 JSON，不能输出任何解释、思考或 markdown。不要用 ``` 包裹。从 { 开始到 } 结束。"},
                {"role": "user", "content": prompt},
            ],
            temperature=temperature,
            max_tokens=1600,
        )
        content = str(result.get("content") or "")
        if not content.strip():
            raise ValueError("模型返回空内容")
        return self._extract_json(content)

    async def generate_directions(
        self,
        *,
        requirement: str,
        project_name: str,
        industry: str | None = None,
    ) -> list[dict[str, str]]:
        last_error: Exception | None = None
        for attempt in range(self._MAX_RETRIES):
            try:
                temperature = 0.85 if attempt == 0 else 0.3
                data = await self._try_generate(
                    requirement=requirement,
                    project_name=project_name,
                    temperature=temperature,
                )
                directions = data.get("directions") or []
                normalized: list[dict[str, str]] = []
                for item in directions[:3]:
                    if not isinstance(item, dict):
                        continue
                    normalized.append(
                        {
                            "key": str(item.get("key") or ""),
                            "name": str(item.get("name") or "设计方向"),
                            "summary": str(item.get("summary") or ""),
                            "styleKeywords": (
                                ",".join(item["styleKeywords"])
                                if isinstance(item.get("styleKeywords"), list)
                                else str(item.get("styleKeywords") or "")
                            ),
                            "cmf": str(item.get("cmf") or ""),
                            "imagePrompt": str(item.get("imagePrompt") or ""),
                        }
                    )
                if len(normalized) < 3:
                    raise ValueError(f"方向数量不足：{len(normalized)}")
                return normalized
            except (OfficialChatServiceError, ValueError, json.JSONDecodeError) as exc:
                last_error = exc
                logger.warning("DesignAgent 第 %d 次尝试失败: %s", attempt + 1, exc)
        raise DesignAgentError(str(last_error)) from last_error


design_agent = DesignAgent()