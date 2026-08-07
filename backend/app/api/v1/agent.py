"""AI 共创工作台 Agent 接口：意图识别 / 需求结构化。"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from app.api.deps import require_auth
from app.services.intent_service import IntentServiceError, intent_service
from app.utils.response import error_response, success_response

router = APIRouter(prefix="/agent")


class IntentAnalysisRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    text: str = Field(max_length=12000)
    context: dict[str, object] = Field(default_factory=dict)


@router.post("/intent", response_model=dict, summary="意图识别与需求结构化")
async def analyze_intent(
    request: IntentAnalysisRequest,
    auth_user: dict[str, object] = Depends(require_auth),
) -> dict[str, object] | JSONResponse:
    """解析用户输入为 {intent, projectName, industry, requirementText, suggestedOptions}。"""
    try:
        data = await intent_service.analyze(request.text)
        return success_response(data=data, message="意图识别完成")
    except IntentServiceError as exc:
        return JSONResponse(
            status_code=exc.status_code,
            content=error_response(
                message=exc.message,
                code=exc.status_code,
                error_code=exc.error_code,
            ),
        )


class MaterialsParseRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    text: str = Field(max_length=12000)


@router.post("/materials/parse", response_model=dict, summary="解析材料描述")
async def parse_materials(
    request: MaterialsParseRequest,
    auth_user: dict[str, object] = Depends(require_auth),
) -> dict[str, object] | JSONResponse:
    """解析用户补充的一段材料描述为结构化材料字段。"""
    try:
        data = await intent_service.parse_materials(request.text)
        return success_response(data=data, message="材料解析完成")
    except IntentServiceError as exc:
        return JSONResponse(
            status_code=exc.status_code,
            content=error_response(
                message=exc.message,
                code=exc.status_code,
                error_code=exc.error_code,
            ),
        )
