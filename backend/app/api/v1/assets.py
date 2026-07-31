"""资产上传、查询、下载与删除 API。"""
from __future__ import annotations

import json
import re
from collections.abc import AsyncGenerator, Iterator
from dataclasses import dataclass
from pathlib import PurePath
from urllib.parse import quote
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import Response, StreamingResponse
from starlette.background import BackgroundTask
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import FormData, Headers, UploadFile
from starlette.formparsers import MultiPartException, MultiPartParser
from sqlalchemy import delete, func, or_, select, update
from sqlalchemy.orm import Session

from app.api.deps import require_auth
from app.config.settings import settings
from app.core.middleware import RequestBodyTooLargeError
from app.db.session import get_db
from app.models.cocreation_history import (
    CocreationAssetLibraryEntry,
    CocreationProjectHistory,
    CocreationProjectVersionHistory,
    CocreationVersionAssetEntry,
)
from app.models.persistence import Asset, WorkflowTask
from app.schemas.assets import AssetListResponse, AssetResponse
from app.services.asset_blob_service import (
    AssetAccessDeniedError,
    AssetBlobError,
    AssetBlobService,
    AssetIntegrityError,
    AssetNotFoundError,
    AssetStateError,
)

router = APIRouter(prefix="/assets")
asset_service = AssetBlobService(chunk_size=settings.ASSET_CHUNK_SIZE_BYTES)


class UploadTooLargeError(Exception):
    pass


class AssetMetadataTooLarge(MultiPartException):
    """multipart 普通字段超过允许大小。"""


class LimitedMultiPartParser(MultiPartParser):
    """在聚合普通 multipart 字段前执行逐块限制。"""

    def __init__(
        self,
        headers: Headers,
        stream: AsyncGenerator[bytes, None],
        *,
        max_field_bytes: int,
    ) -> None:
        super().__init__(headers, stream, max_files=1, max_fields=6)
        if max_field_bytes <= 0:
            raise ValueError("max_field_bytes must be positive")
        self._max_field_bytes = max_field_bytes

    def on_part_data(self, data: bytes, start: int, end: int) -> None:
        if (
            self._current_part.file is None
            and len(self._current_part.data) + end - start > self._max_field_bytes
        ):
            raise AssetMetadataTooLarge("multipart 字段超过大小限制")
        super().on_part_data(data, start, end)

    async def parse_safely(self) -> FormData:
        """确保 receive、解析器或取消异常均不会泄漏临时文件。"""
        try:
            return await super().parse()
        except BaseException:
            for temporary_file in self._files_to_close_on_error:
                if not temporary_file.closed:
                    temporary_file.close()
            raise


@dataclass(frozen=True)
class ParsedAssetUpload:
    file: UploadFile
    kind: str
    project_id: str | None
    version_id: str | None
    task_id: str | None
    source: str
    metadata: str | None


def _user_id(auth_user: dict[str, object]) -> str:
    value = auth_user.get("sub")
    if not isinstance(value, str) or not value:
        raise HTTPException(status_code=401, detail="无效用户身份")
    return value


def _safe_filename(filename: str | None) -> str:
    raw = filename or ""
    if not raw or any(ord(character) < 32 or ord(character) == 127 for character in raw):
        raise HTTPException(status_code=422, detail="filename 包含非法字符或为空")
    name = PurePath(raw.replace("\\", "/")).name
    cleaned = name.strip(" .")
    if not cleaned:
        raise HTTPException(status_code=422, detail="filename 不能为空")
    if len(cleaned) > 255:
        raise HTTPException(status_code=422, detail="filename 超过 255 个字符")
    extension = PurePath(cleaned).suffix.lstrip(".")
    if len(extension) > 32:
        raise HTTPException(status_code=422, detail="文件扩展名超过 32 个字符")
    return cleaned


_MIME_PATTERN = re.compile(
    r'^[A-Za-z0-9!#$&^_.+-]+/[A-Za-z0-9!#$&^_.+-]+'
    r'(?:\s*;\s*[A-Za-z0-9!#$&^_.+-]+=(?:[A-Za-z0-9!#$&^_.+-]+|"[^"]*"))*$'
)


def _validate_content_type(content_type: str | None) -> str:
    if content_type is None or not content_type:
        raise HTTPException(status_code=422, detail="Content-Type 不能为空")
    if len(content_type) > 160:
        raise HTTPException(status_code=422, detail="Content-Type 超过 160 个字符")
    if any(ord(character) < 32 or ord(character) == 127 for character in content_type):
        raise HTTPException(status_code=422, detail="Content-Type 包含非法控制字符")
    if _MIME_PATTERN.fullmatch(content_type) is None:
        raise HTTPException(status_code=422, detail="Content-Type 格式无效")
    return content_type


def _validate_upload_headers(file: UploadFile) -> None:
    allowed_headers = {"content-disposition", "content-type"}
    if any(header.lower() not in allowed_headers for header in file.headers):
        raise HTTPException(status_code=422, detail="文件 multipart 头包含非法字段")


def _required_text(
    fields: dict[str, str],
    name: str,
    *,
    max_length: int,
) -> str:
    value = fields.get(name, "").strip()
    if not value:
        raise HTTPException(status_code=422, detail=f"{name} 不能为空")
    if len(value) > max_length:
        raise HTTPException(status_code=422, detail=f"{name} 超过长度限制")
    return value


def _optional_text(
    fields: dict[str, str],
    name: str,
    *,
    max_length: int,
) -> str | None:
    raw = fields.get(name)
    if raw is None or not raw.strip():
        return None
    value = raw.strip()
    if len(value) > max_length:
        raise HTTPException(status_code=422, detail=f"{name} 超过长度限制")
    return value


def _extract_upload(form: FormData) -> ParsedAssetUpload:
    allowed = {
        "file",
        "kind",
        "projectId",
        "versionId",
        "taskId",
        "source",
        "metadata",
    }
    fields: dict[str, str] = {}
    upload_file: UploadFile | None = None
    seen: set[str] = set()
    for name, value in form.multi_items():
        if name not in allowed:
            raise HTTPException(status_code=422, detail=f"不支持的字段: {name}")
        if name in seen:
            raise HTTPException(status_code=422, detail=f"字段不可重复: {name}")
        seen.add(name)
        if name == "file":
            if not isinstance(value, UploadFile):
                raise HTTPException(status_code=422, detail="file 必须是上传文件")
            upload_file = value
        else:
            if not isinstance(value, str):
                raise HTTPException(status_code=422, detail=f"{name} 必须是文本字段")
            fields[name] = value
    if upload_file is None:
        raise HTTPException(status_code=422, detail="缺少 file")
    return ParsedAssetUpload(
        file=upload_file,
        kind=_required_text(fields, "kind", max_length=64),
        project_id=_optional_text(fields, "projectId", max_length=160),
        version_id=_optional_text(fields, "versionId", max_length=160),
        task_id=_optional_text(fields, "taskId", max_length=160),
        source=(
            _required_text(fields, "source", max_length=64)
            if "source" in fields
            else "upload"
        ),
        metadata=fields.get("metadata"),
    )


def _parse_metadata(raw: str | None) -> dict[str, object]:
    if raw is None or not raw.strip():
        return {}
    if len(raw.encode("utf-8")) > settings.ASSET_METADATA_MAX_BYTES:
        raise HTTPException(status_code=413, detail="metadata 超过大小限制")
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=422, detail="metadata 必须是有效 JSON 对象") from exc
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise HTTPException(status_code=422, detail="metadata 必须是 JSON 对象")
    return value


def _file_chunks(file: UploadFile) -> Iterator[bytes]:
    total = 0
    while True:
        chunk = file.file.read(settings.ASSET_CHUNK_SIZE_BYTES)
        if not chunk:
            return
        total += len(chunk)
        if total > settings.UPLOAD_MAX_SIZE:
            raise UploadTooLargeError
        yield chunk


def _owned_available(db: Session, asset_id: UUID, user_id: str) -> Asset:
    asset = db.scalar(
        select(Asset).where(
            Asset.id == asset_id,
            Asset.user_id == user_id,
            Asset.status == "available",
        )
    )
    if asset is None:
        raise HTTPException(status_code=404, detail="资产不存在")
    return asset


def _owned_for_download(db: Session, asset_id: UUID, user_id: str) -> Asset:
    asset = db.scalar(
        select(Asset).where(Asset.id == asset_id, Asset.user_id == user_id)
    )
    if asset is None:
        raise HTTPException(status_code=404, detail="资产不存在")
    if asset.status != "available":
        raise HTTPException(status_code=409, detail="资产当前不可下载")
    return asset


def _validate_upload_references(
    db: Session,
    user_id: str,
    project_id: str | None,
    version_id: str | None,
    task_id: str | None,
) -> None:
    project: CocreationProjectHistory | None = None
    if project_id is not None:
        project = db.scalar(
            select(CocreationProjectHistory).where(
                CocreationProjectHistory.user_id == user_id,
                CocreationProjectHistory.project_id == project_id,
            )
        )
        if project is None:
            raise HTTPException(status_code=404, detail="项目不存在")
    if version_id is not None:
        conditions = [
            CocreationProjectVersionHistory.user_id == user_id,
            CocreationProjectVersionHistory.version_id == version_id,
        ]
        if project is not None:
            conditions.append(
                CocreationProjectVersionHistory.project_history_id == project.id
            )
        if db.scalar(select(CocreationProjectVersionHistory).where(*conditions)) is None:
            raise HTTPException(status_code=404, detail="项目版本不存在")
    if task_id is not None:
        task = db.scalar(
            select(WorkflowTask).where(
                WorkflowTask.id == task_id,
                WorkflowTask.user_id == user_id,
            )
        )
        if task is None:
            raise HTTPException(status_code=404, detail="任务不存在")
        if project_id is not None and task.project_id != project_id:
            raise HTTPException(status_code=409, detail="任务与项目不匹配")
        if version_id is not None and task.version_id != version_id:
            raise HTTPException(status_code=409, detail="任务与项目版本不匹配")


@router.get("", response_model=AssetListResponse)
def list_assets(
    auth_user: dict[str, object] = Depends(require_auth),
    db: Session = Depends(get_db),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    kind: str | None = Query(default=None, max_length=64),
    asset_status: str | None = Query(default=None, alias="status", max_length=64),
    library: bool | None = None,
    source: str | None = Query(default=None, max_length=64),
) -> AssetListResponse:
    user_id = _user_id(auth_user)
    conditions = [Asset.user_id == user_id]
    if kind is not None:
        conditions.append(Asset.kind == kind)
    if asset_status is not None:
        conditions.append(Asset.status == asset_status)
    if source is not None:
        conditions.append(Asset.source == source)
    if library is not None:
        conditions.append(
            CocreationAssetLibraryEntry.user_id == user_id
            if library
            else or_(
                CocreationAssetLibraryEntry.id.is_(None),
                CocreationAssetLibraryEntry.user_id != user_id,
            )
        )
    base_query = (
        select(Asset, CocreationProjectVersionHistory.version_id)
        .outerjoin(
            CocreationAssetLibraryEntry,
            CocreationAssetLibraryEntry.asset_id == Asset.id,
        )
        .outerjoin(
            CocreationProjectVersionHistory,
            CocreationProjectVersionHistory.id
            == CocreationAssetLibraryEntry.version_history_id,
        )
        .where(*conditions)
    )
    total = db.scalar(
        select(func.count()).select_from(base_query.subquery())
    ) or 0
    rows = db.execute(
        base_query
        .order_by(Asset.created_at.desc(), Asset.id.desc())
        .limit(limit)
        .offset(offset)
    ).all()
    return AssetListResponse(
        items=[
            AssetResponse.model_validate(asset).model_copy(
                update={"source_version_id": source_version_id}
            )
            for asset, source_version_id in rows
        ],
        total=total,
    )


def _persist_upload(
    db: Session,
    user_id: str,
    upload: ParsedAssetUpload,
) -> AssetResponse:
    parsed_metadata = _parse_metadata(upload.metadata)
    _validate_upload_headers(upload.file)
    _validate_upload_references(
        db,
        user_id,
        upload.project_id,
        upload.version_id,
        upload.task_id,
    )
    asset = asset_service.store_stream(
        db=db,
        user_id=user_id,
        filename=_safe_filename(upload.file.filename),
        content_type=_validate_content_type(upload.file.content_type),
        kind=upload.kind,
        source=upload.source,
        stream=_file_chunks(upload.file),
        project_id=upload.project_id,
        version_id=upload.version_id,
        task_id=upload.task_id,
        metadata=parsed_metadata,
    )
    db.commit()
    db.refresh(asset)
    return AssetResponse.model_validate(asset)


@router.post("/upload", response_model=AssetResponse)
async def upload_asset(
    request: Request,
    auth_user: dict[str, object] = Depends(require_auth),
    db: Session = Depends(get_db),
) -> AssetResponse:
    form: FormData | None = None
    try:
        parser = LimitedMultiPartParser(
            request.headers,
            request.stream(),
            max_field_bytes=settings.ASSET_METADATA_MAX_BYTES,
        )
        form = await parser.parse_safely()
        upload = _extract_upload(form)
        return await run_in_threadpool(
            _persist_upload,
            db,
            _user_id(auth_user),
            upload,
        )
    except AssetMetadataTooLarge as exc:
        db.rollback()
        raise HTTPException(status_code=413, detail="metadata 超过大小限制") from exc
    except RequestBodyTooLargeError as exc:
        db.rollback()
        raise HTTPException(status_code=413, detail="请求体超过大小限制") from exc
    except MultiPartException as exc:
        db.rollback()
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except UploadTooLargeError as exc:
        db.rollback()
        raise HTTPException(status_code=413, detail="上传文件超过大小限制") from exc
    except ValueError as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except HTTPException:
        db.rollback()
        raise
    except Exception as exc:
        db.rollback()
        raise HTTPException(status_code=500, detail="资产上传失败") from exc
    finally:
        if form is not None:
            await form.close()


@router.get("/{asset_id}", response_model=AssetResponse)
def get_asset(
    asset_id: UUID,
    auth_user: dict[str, object] = Depends(require_auth),
    db: Session = Depends(get_db),
) -> AssetResponse:
    return AssetResponse.model_validate(
        _owned_available(db, asset_id, _user_id(auth_user))
    )


@router.get("/{asset_id}/download")
def download_asset(
    asset_id: UUID,
    auth_user: dict[str, object] = Depends(require_auth),
    db: Session = Depends(get_db),
) -> StreamingResponse:
    user_id = _user_id(auth_user)
    asset = _owned_for_download(db, asset_id, user_id)
    safe_name = _safe_filename(asset.filename)
    ascii_name = safe_name.encode("ascii", "ignore").decode() or "download"
    ascii_name = ascii_name.replace('"', "_").replace("\\", "_")
    disposition = (
        f'attachment; filename="{ascii_name}"; '
        f"filename*=UTF-8''{quote(safe_name, safe='')}"
    )
    try:
        prepared = asset_service.prepare_content(asset_id, user_id)
    except (AssetNotFoundError, AssetAccessDeniedError) as exc:
        raise HTTPException(status_code=404, detail="资产不存在") from exc
    except AssetStateError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except AssetIntegrityError as exc:
        raise HTTPException(status_code=500, detail="资产完整性校验失败") from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail="资产读取失败") from exc

    def stream_prepared() -> Iterator[bytes]:
        try:
            yield from prepared.iter_chunks()
        finally:
            prepared.close()

    return StreamingResponse(
        stream_prepared(),
        media_type=asset.content_type,
        headers={
            "Content-Length": str(asset.size_bytes),
            "Content-Disposition": disposition,
            "X-Content-Type-Options": "nosniff",
        },
        background=BackgroundTask(prepared.close),
    )


@router.delete("/{asset_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_asset(
    asset_id: UUID,
    auth_user: dict[str, object] = Depends(require_auth),
    db: Session = Depends(get_db),
) -> Response:
    try:
        user_id = _user_id(auth_user)
        owned_asset = db.scalar(
            select(Asset.id).where(Asset.id == asset_id, Asset.user_id == user_id)
        )
        if owned_asset is None:
            raise AssetNotFoundError("asset not found")
        db.execute(
            delete(CocreationAssetLibraryEntry).where(
                CocreationAssetLibraryEntry.asset_id == asset_id,
                CocreationAssetLibraryEntry.user_id == user_id,
            )
        )
        db.execute(
            delete(CocreationVersionAssetEntry).where(
                CocreationVersionAssetEntry.asset_id == asset_id,
                CocreationVersionAssetEntry.user_id == user_id,
            )
        )
        db.execute(
            update(CocreationProjectHistory)
            .where(
                CocreationProjectHistory.last_image_asset_id == asset_id,
                CocreationProjectHistory.user_id == user_id,
            )
            .values(last_image_asset_id=None)
        )
        asset_service.delete_asset(db, asset_id, user_id)
        db.commit()
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    except (AssetNotFoundError, AssetAccessDeniedError) as exc:
        db.rollback()
        raise HTTPException(status_code=404, detail="资产不存在") from exc
    except AssetStateError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except AssetBlobError as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        db.rollback()
        raise HTTPException(status_code=500, detail="资产删除失败") from exc
