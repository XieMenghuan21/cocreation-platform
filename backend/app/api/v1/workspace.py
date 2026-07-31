"""工作区状态 API。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.api.deps import require_auth
from app.db.session import get_db
from app.models.cocreation_history import CocreationProjectVersionHistory
from app.models.persistence import WorkspaceState
from app.schemas.workspace import (
    WorkspaceReferenceUpdate,
    WorkspaceResponse,
    WorkspaceUpdate,
)
from app.services.workspace_service import (
    WorkspaceConflict,
    WorkspaceReferenceConflict,
    WorkspaceReferenceError,
    workspace_service,
)

router = APIRouter(prefix="/workspace")


def _user_id(auth_user: dict[str, object]) -> str:
    value = auth_user.get("sub")
    if not isinstance(value, str) or not value:
        raise HTTPException(status_code=401, detail="无效用户身份")
    return value


def _serialize_workspace(
    db: Session,
    state: WorkspaceState,
) -> WorkspaceResponse:
    version_id: str | None = None
    if state.selected_reference_version_history_id is not None:
        referenced_version = db.get(
            CocreationProjectVersionHistory,
            state.selected_reference_version_history_id,
        )
        if referenced_version is not None and referenced_version.user_id == state.user_id:
            version_id = referenced_version.version_id
    return WorkspaceResponse.model_validate(state).model_copy(
        update={"selected_reference_version_id": version_id}
    )


@router.get("", response_model=WorkspaceResponse)
def get_workspace(
    auth_user: dict[str, object] = Depends(require_auth),
    db: Session = Depends(get_db),
) -> WorkspaceResponse:
    return _serialize_workspace(
        db,
        workspace_service.get_or_default(db, _user_id(auth_user)),
    )


@router.put("", response_model=WorkspaceResponse)
def update_workspace(
    payload: WorkspaceUpdate,
    auth_user: dict[str, object] = Depends(require_auth),
    db: Session = Depends(get_db),
) -> WorkspaceResponse:
    try:
        state = workspace_service.update(db, _user_id(auth_user), payload)
        db.commit()
        db.refresh(state)
        return _serialize_workspace(db, state)
    except WorkspaceConflict as exc:
        db.rollback()
        latest = workspace_service.get_or_default(db, _user_id(auth_user))
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "message": "工作区版本冲突",
                "latest": _serialize_workspace(db, latest).model_dump(
                    mode="json",
                    by_alias=True,
                ),
            },
        ) from exc
    except WorkspaceReferenceError as exc:
        db.rollback()
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except WorkspaceReferenceConflict as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except HTTPException:
        db.rollback()
        raise
    except Exception as exc:
        db.rollback()
        raise HTTPException(status_code=500, detail="工作区保存失败") from exc


@router.put("/reference", response_model=WorkspaceResponse)
def update_workspace_reference(
    payload: WorkspaceReferenceUpdate,
    auth_user: dict[str, object] = Depends(require_auth),
    db: Session = Depends(get_db),
) -> WorkspaceResponse:
    try:
        state = workspace_service.set_reference_version(
            db,
            _user_id(auth_user),
            payload.version_id,
            payload.project_id,
        )
        db.commit()
        db.refresh(state)
        return _serialize_workspace(db, state)
    except WorkspaceReferenceError as exc:
        db.rollback()
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except WorkspaceReferenceConflict as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception as exc:
        db.rollback()
        raise HTTPException(status_code=500, detail="工作区引用保存失败") from exc
