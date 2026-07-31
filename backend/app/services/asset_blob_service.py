"""事务型分块资产 BLOB 服务。"""
from __future__ import annotations

import sqlite3
from collections.abc import Callable, Iterable, Iterator
from hashlib import sha256
from pathlib import Path
from tempfile import SpooledTemporaryFile
from typing import BinaryIO
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.session import SessionLocal
from app.models.persistence import Asset, AssetBlobChunk


class AssetBlobError(Exception):
    """资产 BLOB 操作基础异常。"""


class AssetAccessDeniedError(AssetBlobError):
    """当前用户无权访问资产。"""


class AssetNotFoundError(AssetBlobError):
    """资产不存在。"""


class AssetIntegrityError(AssetBlobError):
    """资产内容完整性验证失败。"""


class AssetStateError(AssetBlobError):
    """资产状态不允许当前操作。"""


class PreparedAssetContent:
    """已完整验证、与数据库生命周期解耦的资产快照。"""

    def __init__(
        self,
        spool: SpooledTemporaryFile[bytes],
        chunk_size: int,
    ) -> None:
        self._spool = spool
        self._chunk_size = chunk_size

    @property
    def closed(self) -> bool:
        return self._spool.closed

    def iter_chunks(self) -> Iterator[bytes]:
        if self.closed:
            raise ValueError("prepared asset content is closed")
        while content := self._spool.read(self._chunk_size):
            yield content

    def close(self) -> None:
        if not self.closed:
            self._spool.close()


class AssetBlobService:
    """在调用方事务中存储、读取与删除分块资产。"""

    def __init__(
        self,
        chunk_size: int,
        session_factory: Callable[[], Session] = SessionLocal,
    ) -> None:
        if chunk_size <= 0:
            raise ValueError("chunk_size must be positive")
        self.chunk_size = chunk_size
        self._session_factory = session_factory

    def store_bytes(
        self,
        *,
        db: Session,
        user_id: str,
        filename: str,
        content_type: str,
        kind: str,
        source: str,
        content: bytes,
        project_id: str | None = None,
        version_id: str | None = None,
        task_id: str | None = None,
        metadata: dict[str, object] | None = None,
        publish: bool = True,
    ) -> Asset:
        if not isinstance(content, bytes):
            raise TypeError("content must be bytes")
        return self.store_stream(
            db=db,
            user_id=user_id,
            filename=filename,
            content_type=content_type,
            kind=kind,
            source=source,
            stream=(content,),
            project_id=project_id,
            version_id=version_id,
            task_id=task_id,
            metadata=metadata,
            publish=publish,
        )

    def store_stream(
        self,
        *,
        db: Session,
        user_id: str,
        filename: str,
        content_type: str,
        kind: str,
        source: str,
        stream: Iterable[bytes],
        project_id: str | None = None,
        version_id: str | None = None,
        task_id: str | None = None,
        metadata: dict[str, object] | None = None,
        publish: bool = True,
    ) -> Asset:
        self._validate_store_inputs(
            user_id=user_id,
            filename=filename,
            content_type=content_type,
            kind=kind,
            source=source,
        )
        asset_metadata = dict(metadata) if metadata is not None else {}
        extension = Path(filename).suffix.lstrip(".").lower() or None
        empty_digest = sha256(b"").hexdigest()
        asset = Asset(
            user_id=user_id,
            project_id=project_id,
            version_id=version_id,
            task_id=task_id,
            kind=kind,
            filename=filename,
            extension=extension,
            content_type=content_type,
            size_bytes=0,
            sha256=empty_digest,
            chunk_size=self.chunk_size,
            chunk_count=0,
            status="uploading",
            source=source,
            asset_metadata=asset_metadata,
        )

        full_digest = sha256()
        buffer = bytearray()
        total_size = 0
        chunk_index = 0

        # 保存点确保流读取或 flush 失败时不会在调用方事务内残留半成品。
        self._ensure_database_transaction(db)
        with db.begin_nested():
            db.add(asset)
            db.flush()
            for input_chunk in stream:
                if not isinstance(input_chunk, bytes):
                    raise TypeError("stream chunks must be bytes")
                offset = 0
                while offset < len(input_chunk):
                    take = min(self.chunk_size - len(buffer), len(input_chunk) - offset)
                    buffer.extend(input_chunk[offset : offset + take])
                    offset += take
                    if len(buffer) == self.chunk_size:
                        self._store_chunk(db, asset.id, chunk_index, bytes(buffer))
                        full_digest.update(buffer)
                        total_size += len(buffer)
                        chunk_index += 1
                        buffer.clear()

            if buffer:
                self._store_chunk(db, asset.id, chunk_index, bytes(buffer))
                full_digest.update(buffer)
                total_size += len(buffer)
                chunk_index += 1

            asset.size_bytes = total_size
            asset.sha256 = full_digest.hexdigest()
            asset.chunk_count = chunk_index
            asset.status = "available" if publish else "staged"
            db.flush()
        return asset

    @staticmethod
    def _ensure_database_transaction(db: Session) -> None:
        """规避 SQLite 旧事务模式将根 SAVEPOINT 直接提交的问题。"""
        connection = db.connection()
        driver_connection = connection.connection.driver_connection
        if (
            isinstance(driver_connection, sqlite3.Connection)
            and not driver_connection.in_transaction
        ):
            connection.exec_driver_sql("BEGIN")

    def get_asset(self, db: Session, asset_id: UUID, user_id: str) -> Asset:
        return self._get_owned_asset(db, asset_id, user_id, require_available=True)

    def read_bytes(self, db: Session, asset_id: UUID, user_id: str) -> bytes:
        """在调用方事务内读取并校验资产，供外部 CLI 临时物化输入。"""
        asset = self.get_asset(db, asset_id, user_id)
        chunks = list(
            db.scalars(
                select(AssetBlobChunk)
                .where(AssetBlobChunk.asset_id == asset.id)
                .order_by(AssetBlobChunk.chunk_index)
            )
        )
        if len(chunks) != asset.chunk_count:
            raise AssetIntegrityError(
                f"expected {asset.chunk_count} chunks, got {len(chunks)}"
            )
        content = bytearray()
        for expected_index, chunk in enumerate(chunks):
            if chunk.chunk_index != expected_index:
                raise AssetIntegrityError(
                    f"expected chunk index {expected_index}, got {chunk.chunk_index}"
                )
            if chunk.size_bytes != len(chunk.content):
                raise AssetIntegrityError(f"chunk {chunk.chunk_index} size mismatch")
            if sha256(chunk.content).hexdigest() != chunk.sha256:
                raise AssetIntegrityError(f"chunk {chunk.chunk_index} hash mismatch")
            content.extend(chunk.content)
        result = bytes(content)
        if len(result) != asset.size_bytes:
            raise AssetIntegrityError(
                f"expected {asset.size_bytes} bytes, got {len(result)}"
            )
        if sha256(result).hexdigest() != asset.sha256:
            raise AssetIntegrityError("asset hash mismatch")
        return result

    def iter_content(
        self,
        asset_id: UUID,
        user_id: str,
    ) -> Iterator[bytes]:
        """兼容接口：准备完整快照后迭代，并确保最终释放。"""
        prepared = self.prepare_content(asset_id, user_id)
        try:
            yield from prepared.iter_chunks()
        finally:
            prepared.close()

    def prepare_content(
        self,
        asset_id: UUID,
        user_id: str,
    ) -> PreparedAssetContent:
        """同步完成数据库读取和完整性验证，成功后返回独立快照。"""
        db = self._session_factory()
        spool = SpooledTemporaryFile(max_size=self.chunk_size, mode="w+b")
        try:
            self._validate_content_to_spool(db, spool, asset_id, user_id)
            spool.seek(0)
            db.close()
            return PreparedAssetContent(spool, self.chunk_size)
        except Exception:
            spool.close()
            db.close()
            raise

    def _validate_content_to_spool(
        self,
        db: Session,
        spool: BinaryIO,
        asset_id: UUID,
        user_id: str,
    ) -> None:
        asset = self.get_asset(db, asset_id, user_id)
        if asset.chunk_size <= 0 or asset.chunk_count < 0 or asset.size_bytes < 0:
            raise AssetIntegrityError("asset manifest contains invalid sizes")
        expected_chunk_count = (
            (asset.size_bytes + asset.chunk_size - 1) // asset.chunk_size
            if asset.size_bytes
            else 0
        )
        if asset.chunk_count != expected_chunk_count:
            raise AssetIntegrityError(
                "manifest chunk count does not match total size and chunk size"
            )

        full_digest = sha256()
        total_size = 0
        observed_count = 0
        query = (
            select(AssetBlobChunk)
            .where(AssetBlobChunk.asset_id == asset.id)
            .order_by(AssetBlobChunk.chunk_index)
            .execution_options(yield_per=1)
        )
        for chunk in db.scalars(query):
            if observed_count >= expected_chunk_count:
                raise AssetIntegrityError(
                    f"unexpected chunk at index {chunk.chunk_index}"
                )
            if chunk.chunk_index != observed_count:
                raise AssetIntegrityError(
                    f"expected chunk index {observed_count}, got {chunk.chunk_index}"
                )
            content = chunk.content
            if chunk.size_bytes != len(content):
                raise AssetIntegrityError(f"chunk {chunk.chunk_index} size mismatch")
            expected_size = (
                asset.chunk_size
                if observed_count < expected_chunk_count - 1
                else asset.size_bytes - asset.chunk_size * (expected_chunk_count - 1)
            )
            if chunk.size_bytes != expected_size:
                raise AssetIntegrityError(
                    f"chunk {chunk.chunk_index} expected {expected_size} bytes, "
                    f"got {chunk.size_bytes}"
                )
            if sha256(content).hexdigest() != chunk.sha256:
                raise AssetIntegrityError(f"chunk {chunk.chunk_index} hash mismatch")

            full_digest.update(content)
            total_size += len(content)
            observed_count += 1
            spool.write(content)

        if observed_count != asset.chunk_count:
            raise AssetIntegrityError(
                f"expected {asset.chunk_count} chunks, got {observed_count}"
            )
        if total_size != asset.size_bytes:
            raise AssetIntegrityError(
                f"expected {asset.size_bytes} bytes, got {total_size}"
            )
        if full_digest.hexdigest() != asset.sha256:
            raise AssetIntegrityError("asset hash mismatch")

    def delete_asset(self, db: Session, asset_id: UUID, user_id: str) -> None:
        asset = self._get_owned_asset(
            db,
            asset_id,
            user_id,
            require_available=False,
        )
        db.delete(asset)
        db.flush()

    @staticmethod
    def _validate_store_inputs(
        *,
        user_id: str,
        filename: str,
        content_type: str,
        kind: str,
        source: str,
    ) -> None:
        values = {
            "user_id": user_id,
            "filename": filename,
            "content_type": content_type,
            "kind": kind,
            "source": source,
        }
        for name, value in values.items():
            if not value.strip():
                raise ValueError(f"{name} must not be empty")

    @staticmethod
    def _store_chunk(
        db: Session,
        asset_id: UUID,
        chunk_index: int,
        content: bytes,
    ) -> None:
        db.add(
            AssetBlobChunk(
                asset_id=asset_id,
                chunk_index=chunk_index,
                size_bytes=len(content),
                sha256=sha256(content).hexdigest(),
                content=content,
            )
        )
        db.flush()

    @staticmethod
    def _get_owned_asset(
        db: Session,
        asset_id: UUID,
        user_id: str,
        *,
        require_available: bool,
    ) -> Asset:
        asset = db.get(Asset, asset_id)
        if asset is None:
            raise AssetNotFoundError(f"asset {asset_id} was not found")
        if asset.user_id != user_id:
            raise AssetAccessDeniedError("asset belongs to another user")
        if require_available and asset.status != "available":
            raise AssetStateError(f"asset is not available: {asset.status}")
        return asset
