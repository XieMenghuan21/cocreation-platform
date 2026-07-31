"""共创项目历史 API。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.api.deps import require_auth
from app.db.session import get_db
from app.schemas.cocreation_history import (
    HistoryDeleteResponse,
    HistoryListResponse,
    HistoryPublishResponse,
    HistoryWriteResponse,
    UpsertProjectVersionRequest,
)
from app.services.cocreation_history_service import (
    HistoryNotFoundError,
    VersionPublicationError,
    cocreation_history_service,
)
from app.utils.response import success_response

router = APIRouter(prefix="/cocreation-history")


@router.post("/projects/upsert-with-version", response_model=HistoryWriteResponse, summary="写入共创项目历史版本")
def upsert_project_with_version(
    request: UpsertProjectVersionRequest,
    auth_user: dict[str, object] = Depends(require_auth),
    db: Session = Depends(get_db),
) -> dict[str, object]:
    try:
        data = cocreation_history_service.upsert_project_with_version(
            db,
            auth_user=auth_user,
            project_payload=request.project,
            version_payload=request.version,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return success_response(data=data, message="共创项目历史已写入")


@router.get("/projects", response_model=HistoryListResponse, summary="读取当前用户的共创项目历史")
def list_history_projects(
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
    return success_response(data=data, message="共创项目历史读取成功")


@router.post(
    "/projects/{project_id}/versions/{version_id}/publish",
    response_model=HistoryPublishResponse,
    summary="发布数据库项目版本",
)
def publish_history_version(
    project_id: str,
    version_id: str,
    auth_user: dict[str, object] = Depends(require_auth),
    db: Session = Depends(get_db),
) -> dict[str, object]:
    try:
        data = cocreation_history_service.publish_version(
            db,
            auth_user=auth_user,
            project_id=project_id,
            version_id=version_id,
        )
    except HistoryNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except VersionPublicationError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return success_response(data=data, message="项目版本已发布")


@router.delete("/projects/{project_id}/versions/{version_id}", response_model=HistoryDeleteResponse, summary="删除共创项目历史版本")
def delete_history_version(
    project_id: str,
    version_id: str,
    auth_user: dict[str, object] = Depends(require_auth),
    db: Session = Depends(get_db),
) -> dict[str, object]:
    deleted = cocreation_history_service.delete_version(
        db,
        auth_user=auth_user,
        project_id=project_id,
        version_id=version_id,
    )
    if not deleted:
        raise HTTPException(status_code=404, detail="历史版本不存在")
    return success_response(data={"deleted": True}, message="历史版本已删除")
