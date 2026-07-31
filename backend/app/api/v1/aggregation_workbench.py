"""聚合工作台 API：模型目录、对话、提示词优化。"""

from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from app.api.deps import require_auth
from app.services.ai_model_gateway_service import ai_model_gateway_service
from app.services.image_prompt_optimizer_service import image_prompt_optimizer_service

router = APIRouter(prefix="/platform-tools/aggregation-workbench", tags=["聚合工作台"])


class OptimizePromptRequest(BaseModel):
    prompt: str = Field(min_length=1, max_length=12000)
    model: str | None = Field(default=None, max_length=120)


@router.get("/catalog")
async def get_catalog(_user: dict = Depends(require_auth)) -> dict[str, Any]:
    """获取可用模型目录"""
    return await ai_model_gateway_service.get_catalog_snapshot()


@router.post("/prompt/optimize")
async def optimize_prompt(payload: OptimizePromptRequest, _user: dict = Depends(require_auth)) -> dict[str, Any]:
    """优化设计描述/生成提示词。"""
    result = await image_prompt_optimizer_service.optimize(prompt=payload.prompt, model=payload.model)
    return {
        "originalPrompt": result.get("originalPrompt"),
        "optimizedPrompt": result.get("optimizedPrompt"),
        "finalPrompt": result.get("finalPrompt"),
        "enabled": result.get("enabled", True),
        "aiOptimized": result.get("aiOptimized", False),
        "references": result.get("references", []),
    }
