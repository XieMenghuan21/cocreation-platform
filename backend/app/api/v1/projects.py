"""AI 共创工作台项目 API：创建 / 查询项目实体。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict, Field

from app.api.deps import require_auth
from app.db.session import get_db
from app.schemas.cocreation_history import HistoryListResponse
from app.services.cocreation_history_service import cocreation_history_service
from app.utils.response import success_response
from sqlalchemy.orm import Session

router = APIRouter(prefix="/projects")


class CreateProjectRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    name: str = Field(..., max_length=120, description="项目名称")
    description: str = Field(default="", max_length=20000)
    industry: str | None = Field(default=None, max_length=80)
    input_mode: str = Field(default="prompt", alias="inputMode", max_length=64)


@router.post("", response_model=dict, summary="创建项目")
def create_project(
    request: CreateProjectRequest,
    auth_user: dict[str, object] = Depends(require_auth),
    db: Session = Depends(get_db),
) -> dict[str, object]:
    """输入即建项目：自动生成项目 ID 与项目记录。"""
    data = cocreation_history_service.create_project(
        db,
        auth_user=auth_user,
        name=request.name,
        description=request.description,
        industry=request.industry,
        input_mode=request.input_mode,
    )
    return success_response(data=data, message="项目已创建")


@router.get("", response_model=HistoryListResponse, summary="获取项目列表")
def list_projects(
    limit: int = Query(default=200, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    auth_user: dict[str, object] = Depends(require_auth),
    db: Session = Depends(get_db),
) -> dict[str, object]:
    data = cocreation_history_service.list_history(
        db,
        auth_user=auth_user,
        limit=limit,
        offset=offset,
    )
    return success_response(data=data, message="项目列表读取成功")
