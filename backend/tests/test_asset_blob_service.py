from __future__ import annotations

from collections.abc import Iterator
from hashlib import sha256
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from sqlalchemy import Engine, create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker

from app.db.session import Base
from app.models.persistence import Asset, AssetBlobChunk
from app.schemas.assets import AssetListResponse, AssetResponse
from app.services.asset_blob_service import (
    AssetAccessDeniedError,
    AssetBlobService,
    AssetIntegrityError,
    AssetNotFoundError,
    AssetStateError,
)


@pytest.fixture
def engine(tmp_path: Path) -> Iterator[Engine]:
    test_engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'assets.db'}")
    Base.metadata.create_all(test_engine)
    try:
        yield test_engine
    finally:
        Base.metadata.drop_all(test_engine)
        test_engine.dispose()


def store(
    session: Session,
    service: AssetBlobService,
    content: bytes = b"abcdefghij",
    *,
    user_id: str = "user-1",
) -> Asset:
    return service.store_bytes(
        db=session,
        user_id=user_id,
        filename="sample.bin",
        content_type="application/octet-stream",
        kind="binary",
        source="upload",
        content=content,
        project_id="project-1",
        version_id="version-1",
        metadata={"purpose": "test"},
    )


def make_service(engine: Engine, chunk_size: int = 4) -> AssetBlobService:
    return AssetBlobService(
        chunk_size=chunk_size,
        session_factory=sessionmaker(bind=engine, expire_on_commit=False),
    )


def committed_asset(
    engine: Engine,
    service: AssetBlobService,
    content: bytes = b"abcdefghij",
    *,
    user_id: str = "user-1",
) -> UUID:
    with Session(engine) as session:
        asset = store(session, service, content, user_id=user_id)
        session.commit()
        return asset.id


def test_store_bytes_creates_fixed_chunks_and_hashes(engine: Engine) -> None:
    service = make_service(engine)
    asset_id = committed_asset(engine, service)

    with Session(engine) as verification:
        asset = verification.get(Asset, asset_id)
        assert asset is not None
        chunks = list(
            verification.scalars(
                select(AssetBlobChunk)
                .where(AssetBlobChunk.asset_id == asset_id)
                .order_by(AssetBlobChunk.chunk_index)
            )
        )
        assert asset.status == "available"
        assert asset.extension == "bin"
        assert asset.size_bytes == 10
        assert asset.chunk_size == 4
        assert asset.chunk_count == 3
        assert asset.sha256 == sha256(b"abcdefghij").hexdigest()
        assert [chunk.content for chunk in chunks] == [b"abcd", b"efgh", b"ij"]
        assert [chunk.size_bytes for chunk in chunks] == [4, 4, 2]
        assert [chunk.sha256 for chunk in chunks] == [
            sha256(chunk.content).hexdigest() for chunk in chunks
        ]
        assert b"".join(service.iter_content(asset_id, "user-1")) == b"abcdefghij"


def test_store_stream_rechunks_arbitrary_input_boundaries(engine: Engine) -> None:
    service = make_service(engine)
    with Session(engine) as session:
        asset = service.store_stream(
            db=session,
            user_id="user-1",
            filename="stream.dat",
            content_type="application/octet-stream",
            kind="binary",
            source="generated",
            stream=iter([b"a", b"", b"bcdef", b"ghi", b"j"]),
        )
        session.commit()
        asset_id = asset.id

    with Session(engine) as verification:
        chunks = list(
            verification.scalars(
                select(AssetBlobChunk)
                .where(AssetBlobChunk.asset_id == asset_id)
                .order_by(AssetBlobChunk.chunk_index)
            )
        )
        assert [chunk.content for chunk in chunks] == [b"abcd", b"efgh", b"ij"]


def test_empty_file_uses_zero_chunks_and_standard_empty_hash(engine: Engine) -> None:
    service = make_service(engine)
    asset_id = committed_asset(engine, service, b"")

    with Session(engine) as verification:
        asset = service.get_asset(verification, asset_id, "user-1")
        assert asset.size_bytes == 0
        assert asset.chunk_count == 0
        assert asset.sha256 == sha256(b"").hexdigest()
        assert list(service.iter_content(asset_id, "user-1")) == []
        assert verification.scalar(
            select(func.count())
            .select_from(AssetBlobChunk)
            .where(AssetBlobChunk.asset_id == asset_id)
        ) == 0


def test_get_asset_distinguishes_owner_not_found_and_state(engine: Engine) -> None:
    service = make_service(engine)
    asset_id = committed_asset(engine, service)

    with Session(engine) as session:
        with pytest.raises(AssetAccessDeniedError):
            service.get_asset(session, asset_id, "user-2")
        with pytest.raises(AssetNotFoundError):
            service.get_asset(session, uuid4(), "user-1")
        asset = session.get(Asset, asset_id)
        assert asset is not None
        asset.status = "uploading"
        session.commit()

    with Session(engine) as verification:
        with pytest.raises(AssetStateError):
            service.get_asset(verification, asset_id, "user-1")
        with pytest.raises(AssetStateError):
            list(service.iter_content(asset_id, "user-1"))


@pytest.mark.parametrize(
    "mutation",
    [
        "content",
        "chunk_hash",
        "missing",
        "gap",
        "total_size",
        "full_hash",
    ],
)
def test_iter_content_detects_tampering(
    engine: Engine,
    mutation: str,
) -> None:
    service = make_service(engine)
    asset_id = committed_asset(engine, service)

    with Session(engine) as session:
        asset = session.get(Asset, asset_id)
        assert asset is not None
        chunks = list(
            session.scalars(
                select(AssetBlobChunk)
                .where(AssetBlobChunk.asset_id == asset_id)
                .order_by(AssetBlobChunk.chunk_index)
            )
        )
        if mutation == "content":
            chunks[0].content = b"xbcd"
        elif mutation == "chunk_hash":
            chunks[0].sha256 = "0" * 64
        elif mutation == "missing":
            session.delete(chunks[-1])
        elif mutation == "gap":
            chunks[1].chunk_index = 7
        elif mutation == "total_size":
            asset.size_bytes += 1
        elif mutation == "full_hash":
            asset.sha256 = "0" * 64
        session.commit()

    yielded = bytearray()
    with Session(engine) as verification:
        with pytest.raises(AssetIntegrityError):
            for chunk in service.iter_content(asset_id, "user-1"):
                yielded.extend(chunk)
    assert bytes(yielded) == b""


def test_iter_content_does_not_capture_request_session(engine: Engine) -> None:
    service = make_service(engine)
    asset_id = committed_asset(engine, service)
    request_session = Session(engine)
    iterator = service.iter_content(asset_id, "user-1")
    request_session.close()

    assert b"".join(iterator) == b"abcdefghij"
    assert engine.pool.checkedout() == 0


def test_iter_content_releases_connection_after_full_consumption(engine: Engine) -> None:
    service = make_service(engine)
    asset_id = committed_asset(engine, service)

    assert b"".join(service.iter_content(asset_id, "user-1")) == b"abcdefghij"
    assert engine.pool.checkedout() == 0


def test_iter_content_releases_connection_when_generator_is_closed_early(
    engine: Engine,
) -> None:
    service = make_service(engine)
    asset_id = committed_asset(engine, service)
    iterator = service.iter_content(asset_id, "user-1")

    assert next(iterator) == b"abcd"
    iterator.close()

    assert engine.pool.checkedout() == 0


def test_iter_content_releases_connection_on_integrity_error(engine: Engine) -> None:
    service = make_service(engine)
    asset_id = committed_asset(engine, service)
    with Session(engine) as session:
        asset = session.get(Asset, asset_id)
        assert asset is not None
        asset.sha256 = "0" * 64
        session.commit()

    yielded = bytearray()
    with pytest.raises(AssetIntegrityError):
        for chunk in service.iter_content(asset_id, "user-1"):
            yielded.extend(chunk)

    assert yielded == b""
    assert engine.pool.checkedout() == 0


def test_prepare_content_is_snapshot_and_close_is_idempotent(engine: Engine) -> None:
    service = make_service(engine)
    asset_id = committed_asset(engine, service)
    prepared = service.prepare_content(asset_id, "user-1")
    assert engine.pool.checkedout() == 0

    with Session(engine) as session:
        asset = session.get(Asset, asset_id)
        assert asset is not None
        session.delete(asset)
        session.commit()

    assert b"".join(prepared.iter_chunks()) == b"abcdefghij"
    prepared.close()
    prepared.close()
    assert prepared.closed is True


def test_prepare_content_raises_before_returning_for_corruption(engine: Engine) -> None:
    service = make_service(engine)
    asset_id = committed_asset(engine, service)
    with Session(engine) as session:
        asset = session.get(Asset, asset_id)
        assert asset is not None
        asset.sha256 = "0" * 64
        session.commit()

    with pytest.raises(AssetIntegrityError):
        service.prepare_content(asset_id, "user-1")
    assert engine.pool.checkedout() == 0


def test_iter_content_rejects_rehashed_noncanonical_chunk_layout(
    engine: Engine,
) -> None:
    service = make_service(engine)
    asset_id = committed_asset(engine, service)

    with Session(engine) as session:
        chunks = list(
            session.scalars(
                select(AssetBlobChunk)
                .where(AssetBlobChunk.asset_id == asset_id)
                .order_by(AssetBlobChunk.chunk_index)
            )
        )
        replacement = [b"abc", b"defg", b"hij"]
        for chunk, content in zip(chunks, replacement, strict=True):
            chunk.content = content
            chunk.size_bytes = len(content)
            chunk.sha256 = sha256(content).hexdigest()
        session.commit()

    with Session(engine) as verification:
        with pytest.raises(AssetIntegrityError, match="expected 4 bytes"):
            list(service.iter_content(asset_id, "user-1"))


@pytest.mark.parametrize(
    ("size_bytes", "chunk_count"),
    [
        (10, 2),
        (10, 4),
        (0, 1),
        (4, 0),
    ],
)
def test_iter_content_rejects_structurally_inconsistent_manifest(
    engine: Engine,
    size_bytes: int,
    chunk_count: int,
) -> None:
    service = make_service(engine)
    asset_id = committed_asset(engine, service)

    with Session(engine) as session:
        asset = session.get(Asset, asset_id)
        assert asset is not None
        asset.size_bytes = size_bytes
        asset.chunk_count = chunk_count
        session.commit()

    with Session(engine) as verification:
        with pytest.raises(AssetIntegrityError, match="manifest chunk count"):
            list(service.iter_content(asset_id, "user-1"))


def test_iter_content_rejects_zero_length_nonfinal_chunk(engine: Engine) -> None:
    service = make_service(engine)
    asset_id = committed_asset(engine, service)

    with Session(engine) as session:
        first = session.scalar(
            select(AssetBlobChunk).where(
                AssetBlobChunk.asset_id == asset_id,
                AssetBlobChunk.chunk_index == 0,
            )
        )
        assert first is not None
        first.content = b""
        first.size_bytes = 0
        first.sha256 = sha256(b"").hexdigest()
        session.commit()

    with Session(engine) as verification:
        with pytest.raises(AssetIntegrityError, match="expected 4 bytes"):
            list(service.iter_content(asset_id, "user-1"))


def test_iter_content_rejects_declared_chunk_size_mismatch(engine: Engine) -> None:
    service = make_service(engine)
    asset_id = committed_asset(engine, service)

    with Session(engine) as session:
        first = session.scalar(
            select(AssetBlobChunk).where(
                AssetBlobChunk.asset_id == asset_id,
                AssetBlobChunk.chunk_index == 0,
            )
        )
        assert first is not None
        first.size_bytes = 3
        session.commit()

    with Session(engine) as verification:
        with pytest.raises(AssetIntegrityError, match="size mismatch"):
            list(service.iter_content(asset_id, "user-1"))


def test_delete_asset_cascades_chunks(engine: Engine) -> None:
    service = make_service(engine)
    asset_id = committed_asset(engine, service)

    with Session(engine) as session:
        service.delete_asset(session, asset_id, "user-1")
        session.commit()

    with Session(engine) as verification:
        assert verification.get(Asset, asset_id) is None
        assert verification.scalar(
            select(func.count())
            .select_from(AssetBlobChunk)
            .where(AssetBlobChunk.asset_id == asset_id)
        ) == 0


def test_delete_rejects_wrong_owner(engine: Engine) -> None:
    service = make_service(engine)
    asset_id = committed_asset(engine, service)

    with Session(engine) as session:
        with pytest.raises(AssetAccessDeniedError):
            service.delete_asset(session, asset_id, "user-2")
        session.rollback()

    with Session(engine) as verification:
        assert verification.get(Asset, asset_id) is not None


def test_service_never_commits_and_outer_rollback_removes_everything(engine: Engine) -> None:
    service = make_service(engine)
    with Session(engine) as session:
        asset = store(session, service)
        asset_id = asset.id
        assert session.get(Asset, asset_id) is not None
        session.rollback()

    with Session(engine) as verification:
        assert verification.get(Asset, asset_id) is None
        assert verification.scalar(select(func.count()).select_from(AssetBlobChunk)) == 0


def test_failed_stream_rolls_back_partial_asset_inside_caller_transaction(
    engine: Engine,
) -> None:
    service = make_service(engine)

    def broken_stream() -> Iterator[bytes]:
        yield b"abcd"
        raise RuntimeError("source stream failed")

    with Session(engine) as session:
        with pytest.raises(RuntimeError, match="source stream failed"):
            service.store_stream(
                db=session,
                user_id="user-1",
                filename="broken.bin",
                content_type="application/octet-stream",
                kind="binary",
                source="upload",
                stream=broken_stream(),
            )
        session.commit()

    with Session(engine) as verification:
        assert verification.scalar(select(func.count()).select_from(Asset)) == 0
        assert verification.scalar(select(func.count()).select_from(AssetBlobChunk)) == 0


def test_two_users_are_isolated_for_read_and_delete(engine: Engine) -> None:
    service = make_service(engine)
    first_id = committed_asset(engine, service, b"first", user_id="user-1")
    second_id = committed_asset(engine, service, b"second", user_id="user-2")

    with Session(engine) as session:
        assert b"".join(service.iter_content(first_id, "user-1")) == b"first"
        assert b"".join(service.iter_content(second_id, "user-2")) == b"second"
        with pytest.raises(AssetAccessDeniedError):
            list(service.iter_content(second_id, "user-1"))
        with pytest.raises(AssetAccessDeniedError):
            service.delete_asset(session, first_id, "user-2")


@pytest.mark.parametrize("chunk_size", [0, -1])
def test_chunk_size_must_be_positive(chunk_size: int) -> None:
    with pytest.raises(ValueError):
        AssetBlobService(chunk_size=chunk_size)


def test_invalid_scalar_input_is_rejected_before_session_mutation(engine: Engine) -> None:
    service = make_service(engine)
    with Session(engine) as session:
        with pytest.raises(ValueError):
            service.store_bytes(
                db=session,
                user_id="",
                filename="sample.bin",
                content_type="application/octet-stream",
                kind="binary",
                source="upload",
                content=b"value",
            )
        assert not session.new
        assert session.scalar(select(func.count()).select_from(Asset)) == 0


def test_asset_response_uses_camel_case_aliases(engine: Engine) -> None:
    service = make_service(engine)
    asset_id = committed_asset(engine, service)

    with Session(engine) as session:
        asset = session.get(Asset, asset_id)
        assert asset is not None
        response = AssetResponse.model_validate(asset)
        payload = response.model_dump(by_alias=True)
        assert payload["id"] == asset_id
        assert payload["userId"] == "user-1"
        assert payload["sizeBytes"] == 10
        assert payload["chunkCount"] == 3
        assert payload["metadata"] == {"purpose": "test"}
        listing = AssetListResponse(items=[response], total=1)
        assert listing.total == 1
