"""图片生成提示词检索与优化服务。"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
import re
from typing import Any

import httpx

from app.config.settings import settings
from app.services.nodapi_chat_service import nodapi_chat_service

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PromptReference:
    source: str
    category: str
    prompt: str
    tags: tuple[str, ...]
    score: int = 0

    def to_dict(self) -> dict[str, object]:
        return {
            "source": self.source,
            "category": self.category,
            "prompt": self.prompt,
            "tags": list(self.tags),
            "score": self.score,
        }


@dataclass(frozen=True)
class PromptExample:
    title: str
    scene: str
    prompt: str
    tags: tuple[str, ...]
    score: int = 0


PROMPT_REFERENCES: tuple[PromptReference, ...] = (
    PromptReference(
        source="DiffusionDB",
        category="通用生图",
        prompt="high quality image, clear subject, coherent composition, detailed material, clean background",
        tags=("通用", "高质量", "构图", "材质"),
    ),
    PromptReference(
        source="awesome-stable-diffusion-prompts",
        category="通用生图",
        prompt="professional photography, studio lighting, sharp focus, realistic texture, no watermark",
        tags=("摄影", "棚拍", "真实材质", "负面约束"),
    ),
    PromptReference(
        source="stable-diffusion-prompt-templates",
        category="电商商品图",
        prompt="commercial product photography, centered product, softbox lighting, white background, ecommerce hero image",
        tags=("电商", "商品图", "白底", "棚拍"),
    ),
    PromptReference(
        source="Awesome-AI-Image-Prompts",
        category="电商商品图",
        prompt="premium brand visual, clean layout, product detail, realistic shadows, market-ready advertising image",
        tags=("电商", "品牌", "海报", "广告"),
    ),
    PromptReference(
        source="3D-FRONT/3D-FUTURE",
        category="家具室内设计",
        prompt="interior design render, accurate furniture layout, realistic wood grain, fabric texture, warm natural lighting",
        tags=("家具", "室内", "布局", "木纹", "软装"),
    ),
    PromptReference(
        source="Pix3D",
        category="家具室内设计",
        prompt="single furniture object, aligned perspective, accurate geometry, clean silhouette, realistic material",
        tags=("家具", "三维", "几何", "单品"),
    ),
    PromptReference(
        source="IKEA-Dataset",
        category="家具商品图",
        prompt="catalog style furniture product image, simple scene, visible dimensions, practical storage details",
        tags=("家具", "商品", "目录", "尺寸"),
    ),
    PromptReference(
        source="Amazon Berkeley Objects",
        category="家居商品图",
        prompt="consumer product image, multi-angle product clarity, plain background, metadata-friendly composition",
        tags=("商品", "家居", "多角度", "检索"),
    ),
    PromptReference(
        source="HomeObjects-3K",
        category="室内物体",
        prompt="indoor object scene, bed sofa chair table lamp wardrobe visible, balanced perspective, natural room light",
        tags=("室内", "沙发", "椅子", "灯具", "衣柜"),
    ),
    PromptReference(
        source="Roboflow RF100 furniture",
        category="家具检测",
        prompt="furniture detection friendly image, separated objects, clear edges, uncluttered background",
        tags=("家具", "检测", "边缘", "分割"),
    ),
    PromptReference(
        source="industrial-design-template",
        category="工业产品图",
        prompt="industrial product concept render, engineering detail, metal and polymer material, precise edges, technical presentation",
        tags=("工业", "装备", "产品", "工程", "金属"),
    ),
    PromptReference(
        source="negative-prompt-template",
        category="负面提示词",
        prompt="avoid blurry image, deformed object, wrong text, watermark, logo artifacts, extra fingers, messy background",
        tags=("负面词", "水印", "乱码", "畸变"),
    ),
    # ---- 新增：借鉴 AI 生图平台的工业设计专用提示词 ----
    PromptReference(
        source="Midjourney-industrial",
        category="工业设计概念图",
        prompt="industrial design concept, orthographic projection, engineering drawing style, clean line work, dimension annotation, material callout, technical blueprint aesthetic",
        tags=("工业", "设计", "工程图", "正交", "蓝图", "尺寸"),
    ),
    PromptReference(
        source="DALL-E-product-viz",
        category="产品可视化",
        prompt="product visualization render, photorealistic material, studio lighting, neutral background, accurate geometry, presentation quality, 8k detail",
        tags=("产品", "可视化", "渲染", "真实", "棚拍", "高质量"),
    ),
    PromptReference(
        source="SD-engineering-style",
        category="工程图风格",
        prompt="technical engineering drawing, isometric view, cross-section detail, assembly exploded view, CAD rendering style, precise edges, monochrome with accent color",
        tags=("工程", "技术", "等轴测", "剖面", "爆炸图", "CAD", "装配"),
    ),
    PromptReference(
        source="ComfyUI-material-study",
        category="材质研究",
        prompt="material study render, brushed metal surface, injection molded plastic, anodized aluminum, glass reflection, PBR material accuracy",
        tags=("材质", "金属", "塑料", "铝", "玻璃", "PBR"),
    ),
    PromptReference(
        source="Leonardo-ai-architecture",
        category="装备/机械结构",
        prompt="mechanical assembly visualization, structural framework, bolted joints, reinforcement ribs, maintenance access panel, engineering precision",
        tags=("机械", "装配", "结构", "螺栓", "加强筋", "维护"),
    ),
    PromptReference(
        source="Ideogram-design-poster",
        category="设计汇报图",
        prompt="design review presentation board, multiple view angles, detail callouts, color palette swatch, typography hierarchy, professional layout",
        tags=("汇报", "展板", "多视图", "细节", "配色", "排版"),
    ),
    PromptReference(
        source="Stable-3D-render",
        category="3D渲染品质词",
        prompt="ray tracing global illumination, subsurface scattering, ambient occlusion, anti-aliased edges, physically based rendering, volumetric light",
        tags=("渲染", "光追", "全局光照", "AO", "抗锯齿", "PBR", "体积光"),
    ),
    PromptReference(
        source="negative-engineering",
        category="工业设计负面词",
        prompt="avoid: cartoon style, hand-drawn sketch, watercolor, abstract art, excessive decoration, fantasy elements, unrealistic proportions, blurry detail",
        tags=("负面词", "卡通", "手绘", "水彩", "抽象", "过度装饰", "奇幻"),
    ),
)

PROMPT_EXAMPLES: tuple[PromptExample, ...] = (
    PromptExample(
        title="古风衣柜",
        scene="家具",
        prompt=(
            "Chinese antique wardrobe design, home furniture hero concept, walnut wood cabinet, carved panel details, "
            "symmetrical doors, brass handles, elegant proportions, warm indoor studio lighting, clean neutral background, "
            "realistic wood grain, high detail, product-focused composition, no unrelated machinery"
        ),
        tags=("古风", "衣柜", "中式", "木质", "雕花", "家具", "家居"),
    ),
    PromptExample(
        title="IP主题茶几",
        scene="家具",
        prompt=(
            "character-inspired coffee table design, cute home furniture product concept, rounded silhouette, playful accent details, "
            "stable tabletop proportions, lacquer and wood material mix, bright studio lighting, clean white background, "
            "clear product structure, no industrial blueprint, presentation-ready"
        ),
        tags=("茶几", "hellokitty", "IP", "可爱", "家具", "家居"),
    ),
    PromptExample(
        title="现代边几",
        scene="家具",
        prompt=(
            "modern side table design, compact furniture object, clean geometry, balanced proportion, matte painted finish, "
            "soft shadow, neutral studio setup, realistic material texture, product-centered composition"
        ),
        tags=("边几", "桌几", "现代", "家具", "家居"),
    ),
    PromptExample(
        title="工业控制柜",
        scene="工业",
        prompt=(
            "industrial control cabinet concept render, engineering-grade structure, sheet metal enclosure, ventilation panel, "
            "service door, modular internal layout, precise edges, technical presentation, neutral background"
        ),
        tags=("控制柜", "工业", "机柜", "设备", "钣金"),
    ),
)

# ── 从 Prompt 花园加载通用提示词模板 ──

def _load_garden_prompts() -> list[PromptReference]:
    """从 docs/prompt-library/prompts.json 加载花园 Prompt 模板。"""
    import json
    import os

    garden: list[PromptReference] = []
    candidates = [
        os.path.join(os.path.dirname(__file__), "..", "..", "..", "docs", "prompt-library", "prompts.json"),
        os.path.join(os.path.dirname(__file__), "..", "..", "docs", "prompt-library", "prompts.json"),
    ]
    path = None
    for candidate in candidates:
        if os.path.isfile(candidate):
            path = candidate
            break
    if path is None:
        return garden
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except Exception:
        return garden
    prompts = data.get("prompts") or []
    if not isinstance(prompts, list):
        prompts = []
    for item in prompts:
        if not isinstance(item, dict):
            continue
        raw_title = str(item.get("title") or "")
        raw_prompt = str(item.get("prompt") or "")
        if not raw_prompt.strip():
            continue
        # 去掉占位符，保留模板骨架，加上标题做标注
        import re as _re
        clean_prompt = _re.sub(r"{{[^}]+}}", "...", raw_prompt)
        clean_prompt = _re.sub(r"\s+", " ", clean_prompt).strip()
        garden.append(
            PromptReference(
                source="prompt-garden",
                category=f"模板-{item.get('category', '通用')}",
                prompt=f"[{raw_title}] {clean_prompt}",
                tags=[str(t) for t in (item.get("tags") or [])],
            )
        )
    return garden

_GARDEN_PROMPTS: list[PromptReference] = _load_garden_prompts()


class ImagePromptOptimizerService:
    """基于 API 对话模型优先、提示词库辅助的提示词检索与优化服务。"""

    def __init__(self) -> None:
        self.dashscope_base_url = (
            os.getenv("FORGECAD_DASHSCOPE_BASE_URL", "").strip()
            or str(getattr(settings, "QWEN_BASE_URL", "") or "").strip()
        ).rstrip("/")
        self.dashscope_api_key = (
            os.getenv("FORGECAD_DASHSCOPE_API_KEY", "").strip()
            or str(getattr(settings, "QWEN_API_KEY", "") or "").strip()
            or str(getattr(settings, "DASHSCOPE_API_KEY", "") or "").strip()
        )
        self.dashscope_model = (
            os.getenv("FORGECAD_DASHSCOPE_MODEL", "").strip()
            or "qwen-plus"
        )
        self.dashscope_timeout = float(
            os.getenv("FORGECAD_DASHSCOPE_TIMEOUT_SECONDS", "").strip()
            or "60"
        )
        self.nodapi_base_url = (
            os.getenv("FORGECAD_NODAPI_BASE_URL", "").strip()
            or str(getattr(settings, "NODAPI_BASE_URL", "") or "").strip()
        ).rstrip("/")
        self.nodapi_api_key = (
            os.getenv("FORGECAD_NODAPI_API_KEY", "").strip()
            or str(getattr(settings, "NODAPI_API_KEY", "") or "").strip()
        )
        self.nodapi_model = (
            os.getenv("FORGECAD_NODAPI_CHAT_MODEL", "").strip()
            or str(getattr(settings, "NODAPI_CHAT_MODEL", "") or "").strip()
            or "gpt-4o-mini"
        )
        self.nodapi_timeout = float(
            os.getenv("FORGECAD_NODAPI_CHAT_TIMEOUT_SECONDS", "").strip()
            or str(getattr(settings, "NODAPI_CHAT_TIMEOUT_SECONDS", "") or "").strip()
            or "120"
        )

    @property
    def dashscope_configured(self) -> bool:
        return bool(self.dashscope_base_url and self.dashscope_api_key)

    @property
    def nodapi_configured(self) -> bool:
        return bool(self.nodapi_base_url and self.nodapi_api_key)

    async def optimize(
        self,
        *,
        prompt: str,
        images: list[str] | None = None,
        model: str | None = None,
    ) -> dict[str, Any]:
        clean_prompt = self._normalize_prompt(prompt)
        references = self.search(clean_prompt, limit=5)
        examples = self.search_examples(clean_prompt, limit=3)

        # ── 知识库检索 ──
        kb_hits: list[PromptReference] = []
        try:
            from app.services.knowledge_base_service import knowledge_base_service
            kb_results = knowledge_base_service.search(clean_prompt, top_k=3)
            if kb_results and isinstance(kb_results, list):
                for hit in kb_results:
                    if isinstance(hit, dict) and hit.get("text"):
                        kb_hits.append(
                            PromptReference(
                                source="knowledge-base",
                                category=f"知识库-{hit.get('source', '产业共享')}",
                                prompt=str(hit["text"])[:800],
                                tags=[],
                            )
                        )
        except Exception as exc:
            logger.debug("[PromptOptimizer] 知识库检索跳过: %s", exc)

        all_references = references + kb_hits

        ai_optimized = None
        if self.dashscope_configured:
            try:
                ai_optimized = await self._optimize_with_dashscope(clean_prompt, all_references, examples, images=images, model=model)
            except Exception as exc:
                logger.warning("[PromptOptimizer] DashScope AI 优化失败，尝试 NodAPI / 规则拼接: %s", exc)

        if not ai_optimized and self.nodapi_configured:
            try:
                ai_optimized = await self._optimize_with_nodapi(clean_prompt, all_references, examples, images=images, model=model)
            except Exception as exc:
                logger.warning("[PromptOptimizer] NodAPI AI 优化失败，回退到规则拼接: %s", exc)

        if ai_optimized and not self._looks_like_unhelpful_optimization(clean_prompt, ai_optimized.get("zh", "")):
            zh_prompt = str(ai_optimized.get("zh") or "").strip()
            en_prompt = str(ai_optimized.get("en") or "").strip()
            if not zh_prompt:
                zh_prompt = en_prompt
            return {
                "originalPrompt": clean_prompt,
                "optimizedPrompt": zh_prompt,
                "finalPrompt": zh_prompt,
                "comfyuiPrompt": en_prompt or zh_prompt,
                "enabled": True,
                "aiOptimized": True,
                "references": [item.to_dict() for item in all_references],
            }

        # 回退：规则拼接（中文展示 + 英文生图）
        optimized_prompt = self._build_optimized_prompt(clean_prompt, all_references, images=images, model=model)
        comfyui_prompt = self._build_comfyui_prompt(clean_prompt)
        return {
            "originalPrompt": clean_prompt,
            "optimizedPrompt": optimized_prompt,
            "finalPrompt": optimized_prompt,
            "comfyuiPrompt": comfyui_prompt,
            "enabled": True,
            "aiOptimized": False,
            "references": [item.to_dict() for item in all_references],
        }

    async def _optimize_with_nodapi(
        self,
        prompt: str,
        references: list[PromptReference],
        examples: list[PromptExample],
        *,
        images: list[str] | None = None,
        model: str | None = None,
    ) -> str | None:
        """调用 NodAPI 对话接口优化图片生成提示词。"""
        reference_hints = "\n".join(
            f"- [{r.category}] {r.prompt}" for r in references[:3]
        ) if references else "无"
        example_hints = "\n".join(
            f"- [{item.scene}] {item.title}: {item.prompt}" for item in examples[:3]
        ) if examples else "无"

        system_prompt = (
            "你是工业设计图片生成提示词优化师。根据用户产品需求，生成高质量工业设计稿提示词。\n"
            "要求：\n"
            "1. 输出为设计稿（design specification sheet），不是产品摄影图。包含：正视图、侧视图、顶视图、局部细节。\n"
            "2. 保留用户指定的产品名、材质、尺寸，严禁虚构功能或结构。\n"
            "3. 标注材质说明（material callout）、表面处理（finish）、颜色方案。\n"
            "4. 补充工业设计质量词：orthographic projection, engineering drawing style, dimension annotation, material callout, clean line work, white background, technical presentation layout, multi-view arrangement。\n"
            "5. 如有尺寸数据，在 prompt 中保留具体数值。\n"
            "6. zh：中文提示词，包含产品名、材质、尺寸、多视图描述和质量词（展示给用户看）。\n"
            "7. en：英文提示词，把 zh 完整翻译成地道的英文生图提示词，面向 Stable Diffusion / ComfyUI，逗号分隔标签式写法，包含 product、material、perspective、lighting、style（真正发给生图模型用）。\n"
            "8. 只输出 JSON，不要多余解释，格式：{\"zh\": \"中文提示词\", \"en\": \"English prompt\"}。\n"
            "9. 保持简洁：用户只提供了基础信息就不要扩写成复杂描述。\n"
        )

        user_message = (
            f"请基于输入词语和相关参考片段，动态优化以下设计需求为图片生成提示词：\n\n"
            f"用户原始描述：\n{prompt}\n\n"
            f"参考提示词片段（仅在相关时采用）：\n{reference_hints}\n"
            f"\n可复用的历史示例 Prompt（只借鉴相关写法，不要照抄）：\n{example_hints}\n"
        )
        if images:
            user_message += f"\n用户提供了 {len(images)} 张参考图片，请在提示词中加入「保持参考图主体结构和关键特征」的要求。\n"
        if model:
            user_message += f"\n目标生图模型：{model}\n"

        result = await nodapi_chat_service.complete(
            model=self.nodapi_model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message},
            ],
            temperature=0.7,
            max_tokens=1024,
        )
        content = str(result.get("content") or "")
        if not content.strip():
            raise RuntimeError("NodAPI 响应为空")

        return self._parse_bilingual_json(content.strip())

    async def _optimize_with_dashscope(
        self,
        prompt: str,
        references: list[PromptReference],
        examples: list[PromptExample],
        *,
        images: list[str] | None,
        model: str | None,
    ) -> str:
        """调用 DashScope 通义对话优化图片生成提示词。"""
        return await self._optimize_with_openai_compatible(
            prompt=prompt,
            references=references,
            examples=examples,
            images=images,
            model=model,
            service_model=self.dashscope_model,
            service_base_url=self.dashscope_base_url,
            service_api_key=self.dashscope_api_key,
            service_timeout=self.dashscope_timeout,
            service_name="DashScope",
        )

    async def _optimize_with_openai_compatible(
        self,
        *,
        prompt: str,
        references: list[PromptReference],
        examples: list[PromptExample],
        images: list[str] | None,
        model: str | None,
        service_model: str,
        service_base_url: str,
        service_api_key: str,
        service_timeout: float,
        service_name: str,
    ) -> str:
        """调用 OpenAI-compatible 对话接口优化图片生成提示词。"""
        reference_hints = "\n".join(
            f"- [{r.category}] {r.prompt}" for r in references[:3]
        ) if references else "无"
        example_hints = "\n".join(
            f"- [{item.scene}] {item.title}: {item.prompt}" for item in examples[:3]
        ) if examples else "无"

        system_prompt = (
            "你是工业设计图片生成提示词优化师。根据用户产品需求，生成高质量工业设计稿提示词。\n"
            "要求：\n"
            "1. 输出为设计稿（design specification sheet），不是产品摄影图。包含：正视图、侧视图、顶视图、局部细节。\n"
            "2. 保留用户指定的产品名、材质、尺寸，严禁虚构功能或结构。\n"
            "3. 标注材质说明（material callout）、表面处理（finish）、颜色方案。\n"
            "4. 补充工业设计质量词：orthographic projection, engineering drawing style, dimension annotation, material callout, clean line work, white background, technical presentation layout, multi-view arrangement。\n"
            "5. 如有尺寸数据，在 prompt 中保留具体数值。\n"
            "6. zh：中文提示词，包含产品名、材质、尺寸、多视图描述和质量词（展示给用户看）。\n"
            "7. en：英文提示词，把 zh 完整翻译成地道的英文生图提示词，面向 Stable Diffusion / ComfyUI，逗号分隔标签式写法，包含 product、material、perspective、lighting、style（真正发给生图模型用）。\n"
            "8. 只输出 JSON，不要多余解释，格式：{\"zh\": \"中文提示词\", \"en\": \"English prompt\"}。\n"
            "9. 保持简洁：用户只提供了基础信息就不要扩写成复杂描述。\n"
        )

        user_message = (
            f"请基于输入词语和相关参考片段，动态优化以下设计需求为图片生成提示词：\n\n"
            f"用户原始描述：\n{prompt}\n\n"
            f"参考提示词片段（仅在相关时采用）：\n{reference_hints}\n"
            f"\n可复用的历史示例 Prompt（只借鉴相关写法，不要照抄）：\n{example_hints}\n"
        )
        if images:
            user_message += f"\n用户提供了 {len(images)} 张参考图片，请在提示词中加入「保持参考图主体结构和关键特征」的要求。\n"
        if model:
            user_message += f"\n目标生图模型：{model}\n"

        payload = {
            "model": self.nodapi_model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message},
            ],
            "temperature": 0.7,
            "max_tokens": 1024,
        }

        headers = {"Content-Type": "application/json"}
        if self.nodapi_key:
            headers["Authorization"] = f"Bearer {self.nodapi_key}"

        async with httpx.AsyncClient(timeout=self.nodapi_timeout) as client:
            response = await client.post(
                f"{self.nodapi_base}/v1/chat/completions",
                json=payload,
                headers=headers,
            )
            response.raise_for_status()
            data = response.json()

        content = self._extract_content(data)
        if not content.strip():
            raise RuntimeError("NodAPI 响应为空")
        return self._parse_bilingual_json(content.strip())

    @staticmethod
    def _extract_content(data: object) -> str:
        if not isinstance(data, dict):
            return ""
        choices = data.get("choices")
        if not isinstance(choices, list) or not choices:
            return ""
        first = choices[0]
        if not isinstance(first, dict):
            return ""
        message = first.get("message")
        if not isinstance(message, dict):
            return ""
        content = message.get("content")
        return content if isinstance(content, str) else ""

    @staticmethod
    def _parse_bilingual_json(content: str) -> dict[str, str]:
        """解析 {"zh": "...", "en": "..."}。失败时整段作为中文，英文用规则兜底。"""
        text = content.strip()
        if text.startswith("```"):
            text = re.sub(r"^```[a-zA-Z]*\s*", "", text)
            text = re.sub(r"\s*```$", "", text)
        try:
            import json as _json

            parsed = _json.loads(text)
            if isinstance(parsed, dict):
                zh = str(parsed.get("zh") or parsed.get("displayPrompt") or "").strip()
                en = str(parsed.get("en") or parsed.get("generationPrompt") or "").strip()
                if zh or en:
                    return {
                        "zh": zh or en,
                        "en": en or zh,
                    }
        except (ValueError, TypeError):
            pass

        zh_match = re.search(r"\"?zh\"?\s*[:：]\s*\"(.*?)\"\s*,?\s*\"?en\"?\s*[:：]\s*\"(.*?)\"", text, flags=re.DOTALL)
        if zh_match:
            return {"zh": zh_match.group(1).strip(), "en": zh_match.group(2).strip()}

        normalized = re.sub(r"^(优化后的提示词|优化提示词|Prompt|优化结果)[:：]\s*", "", text, flags=re.IGNORECASE)
        normalized = normalized.strip().strip('"').strip("'")
        return {"zh": normalized, "en": ImagePromptOptimizerService._build_comfyui_prompt(normalized)}

    @staticmethod
    def _build_comfyui_prompt(prompt: str) -> str:
        """中文生图提示词无法直接用 AD/SD 时，用英文质量词模板兜底。"""
        text = re.sub(r"\s+", " ", prompt).strip()
        if not text:
            return ""
        quality = (
            "industrial design concept render, orthographic projection, engineering drawing style, "
            "dimension annotation, material callout, clean line work, white background, "
            "technical presentation layout, multi-view arrangement, high detail, sharp focus"
        )
        return f"{text}, {quality}"

    def search(self, prompt: str, *, limit: int = 5) -> list[PromptReference]:
        tokens = self._tokenize(prompt)
        domain = self._classify_prompt_domain(prompt)
        query_phrases = self._extract_query_phrases(prompt)
        scored: list[PromptReference] = []
        for pool in (PROMPT_REFERENCES, _GARDEN_PROMPTS):
            for item in pool:
                if not self._reference_matches_domain(item, domain):
                    continue
                score = self._score_reference(item, tokens, prompt, query_phrases)
                # 花园模板分数打折，只保留强匹配（>=6）
                if score > 0 and (pool is not _GARDEN_PROMPTS or score >= 6):
                    scored.append(PromptReference(item.source, item.category, item.prompt, item.tags, score))
        if not scored:
            scored = self._default_references_for_domain(domain)
        scored.sort(key=lambda item: item.score, reverse=True)
        return scored[: max(1, limit)]

    def search_examples(self, prompt: str, *, limit: int = 3) -> list[PromptExample]:
        tokens = self._tokenize(prompt)
        query_phrases = self._extract_query_phrases(prompt)
        scored: list[PromptExample] = []
        for item in PROMPT_EXAMPLES:
            score = 0
            example_text = f"{item.title} {item.scene} {item.prompt} {' '.join(item.tags)}".lower()
            for phrase in query_phrases:
                phrase_lower = phrase.lower()
                if phrase_lower and phrase_lower in example_text:
                    score += 8
            for tag in item.tags:
                if tag in prompt or tag.lower() in tokens:
                    score += 4
            if item.scene in prompt:
                score += 2
            if score > 0:
                scored.append(PromptExample(item.title, item.scene, item.prompt, item.tags, score))
        scored.sort(key=lambda item: item.score, reverse=True)
        return scored[: max(1, limit)]

    def _build_optimized_prompt(
        self,
        prompt: str,
        references: list[PromptReference],
        *,
        images: list[str] | None,
        model: str | None,
    ) -> str:
        reference_phrases = self._dedupe_phrases(
            phrase
            for item in references
            for phrase in re.split(r"[,，]\s*", item.prompt)
            if phrase.strip()
        )
        quality_phrases = [
            "主体清晰",
            "构图稳定",
            "真实材质",
            "边缘准确",
            "光线干净",
            "无水印",
            "无乱码文字",
        ]
        if self._looks_like_ecommerce(prompt):
            quality_phrases.extend(["电商主图构图", "商品居中", "适合平台商品卡片"])
        if self._looks_like_furniture(prompt):
            quality_phrases.extend(["家具比例准确", "室内空间关系合理", "木纹和织物细节真实"])
        if self._looks_like_industrial(prompt):
            quality_phrases.extend(["工程表达清晰", "金属与塑料材质分明", "适合方案评审"])
        if self._looks_like_exploded(prompt):
            quality_phrases.extend(["爆炸图层次分明", "零件分离清晰", "装配关系可追溯"])
        if self._looks_like_render(prompt):
            quality_phrases.extend(["PBR材质准确", "光追全局光照", "抗锯齿边缘", "环境光遮蔽"])
        if images:
            quality_phrases.append("保持参考图主体结构和关键特征")
        if model and "midjourney" in model.lower():
            quality_phrases.append("--ar 3:2")

        parts = [
            prompt,
            "画面要求：" + "，".join(self._dedupe_phrases([*quality_phrases, *reference_phrases[:8]])),
            "负面约束：避免模糊、畸变、多余水印、乱码文字、错误标识、杂乱背景。",
        ]
        return "\n".join(part for part in parts if part.strip())

    @staticmethod
    def _normalize_prompt(prompt: str) -> str:
        return re.sub(r"\s+", " ", prompt).strip()

    @staticmethod
    def _tokenize(prompt: str) -> set[str]:
        lower = prompt.lower()
        tokens = set(re.findall(r"[a-zA-Z0-9_\-]+", lower))
        for keyword in (
            "电商", "商品", "主图", "海报", "工业", "装备", "产品", "金属", "家具", "室内", "沙发",
            "衣柜", "书柜", "床", "椅子", "灯", "木", "客厅", "卧室", "厨房", "设计", "渲染",
        ):
            if keyword in prompt:
                tokens.add(keyword)
        return tokens

    @classmethod
    def _score_reference(
        cls,
        item: PromptReference,
        tokens: set[str],
        prompt: str,
        query_phrases: list[str],
    ) -> int:
        score = 0
        prompt_lower = prompt.lower()
        for tag in item.tags:
            tag_lower = tag.lower()
            if tag in tokens or tag_lower in tokens or tag in prompt or tag_lower in prompt_lower:
                score += 4
        item_text = f"{item.category} {item.prompt} {' '.join(item.tags)}".lower()
        for phrase in query_phrases:
            phrase_lower = phrase.lower()
            if phrase_lower and phrase_lower in item_text:
                score += 6 if len(phrase_lower) >= 2 else 2
        for keyword in cls._tokenize(item.prompt):
            if keyword in tokens:
                score += 1
        if item.category in prompt:
            score += 3
        return score

    @staticmethod
    def _dedupe_phrases(phrases: list[str] | tuple[str, ...] | Any) -> list[str]:
        seen: set[str] = set()
        results: list[str] = []
        for phrase in phrases:
            text = str(phrase).strip()
            if not text:
                continue
            key = text.lower()
            if key in seen:
                continue
            seen.add(key)
            results.append(text)
        return results

    @staticmethod
    def _looks_like_ecommerce(prompt: str) -> bool:
        return any(keyword in prompt for keyword in ("电商", "商品", "主图", "卖点", "品牌", "海报", "宣传"))

    @staticmethod
    def _looks_like_furniture(prompt: str) -> bool:
        return any(
            keyword in prompt
            for keyword in (
                "家具", "家居", "室内", "沙发", "衣柜", "书柜", "床", "椅", "木", "客厅", "卧室",
                "茶几", "桌几", "边几", "桌", "餐桌", "咖啡桌", "斗柜", "电视柜", "玄关柜",
            )
        )

    @staticmethod
    def _looks_like_industrial(prompt: str) -> bool:
        return any(keyword in prompt for keyword in ("工业", "装备", "控制柜", "零部件", "设备", "制造", "机械", "钣金", "机柜"))

    @staticmethod
    def _looks_like_exploded(prompt: str) -> bool:
        return any(keyword in prompt for keyword in ("爆炸", "分解", "拆解", "装配", "exploded", "assembly"))

    @staticmethod
    def _looks_like_render(prompt: str) -> bool:
        return any(keyword in prompt for keyword in ("渲染", "光追", "PBR", "render", "ray tracing", "global illumination", "材质研究"))

    @staticmethod
    def _extract_query_phrases(prompt: str) -> list[str]:
        phrases: list[str] = []
        for pattern in (
            r"项目名称[：:]\s*([^\n]+)",
            r"设计描述[：:]\s*([^\n]+)",
            r"Design requirement[：:]\s*([^\n]+)",
        ):
            phrases.extend(match.strip() for match in re.findall(pattern, prompt, flags=re.IGNORECASE) if str(match).strip())

        explicit_keywords = re.findall(
            r"(hellokitty|hello kitty|茶几|桌几|边几|咖啡桌|餐桌|电视柜|斗柜|玄关柜|衣柜|书柜|沙发|椅子|床|控制柜|支架|机柜|设备|零部件)",
            prompt,
            flags=re.IGNORECASE,
        )
        phrases.extend(explicit_keywords)
        return ImagePromptOptimizerService._dedupe_phrases(phrases)

    @classmethod
    def _classify_prompt_domain(cls, prompt: str) -> str:
        if cls._looks_like_furniture(prompt):
            return "furniture"
        if cls._looks_like_industrial(prompt):
            return "industrial"
        if cls._looks_like_ecommerce(prompt):
            return "ecommerce"
        return "generic"

    @staticmethod
    def _reference_matches_domain(item: PromptReference, domain: str) -> bool:
        if domain == "generic":
            return True

        category = item.category
        tags = set(item.tags)

        if domain == "furniture":
            if "工业" in category or "工程" in category or "机械" in category:
                return False
            if {"工业", "装备", "工程", "蓝图", "CAD", "机械"} & tags:
                return False
            return True

        if domain == "industrial":
            if "家具" in category or "室内" in category or "家居" in category:
                return False
            if {"家具", "室内", "木纹", "软装", "衣柜", "沙发"} & tags:
                return False
            return True

        if domain == "ecommerce":
            return "负面" in category or "商品" in category or "电商" in category or "通用" in category

        return True

    @staticmethod
    def _default_references_for_domain(domain: str) -> list[PromptReference]:
        domain_map: dict[str, tuple[str, ...]] = {
            "furniture": ("家具室内设计", "家具商品图", "家居商品图", "负面提示词"),
            "industrial": ("工业产品图", "工业设计概念图", "工程图风格", "负面提示词", "工业设计负面词"),
            "ecommerce": ("电商商品图", "通用生图", "负面提示词"),
            "generic": ("通用生图", "负面提示词"),
        }
        allowed = domain_map.get(domain, domain_map["generic"])
        fallback: list[PromptReference] = []
        for pool in (PROMPT_REFERENCES, _GARDEN_PROMPTS):
            for item in pool:
                if item.category in allowed or (pool is _GARDEN_PROMPTS and domain == "generic"):
                    fallback.append(PromptReference(item.source, item.category, item.prompt, item.tags, 1))
        return fallback[:5]

    @staticmethod
    def _looks_like_unhelpful_optimization(original_prompt: str, optimized_prompt: str) -> bool:
        normalized_original = re.sub(r"\s+", " ", original_prompt).strip().lower()
        normalized_optimized = re.sub(r"\s+", " ", optimized_prompt).strip().lower()
        if not normalized_optimized:
            return True
        if normalized_original == normalized_optimized and len(normalized_optimized) < 80:
            return True
        if len(normalized_optimized) <= len(normalized_original) + 8 and len(normalized_original) < 40:
            return True
        return False


image_prompt_optimizer_service = ImagePromptOptimizerService()
