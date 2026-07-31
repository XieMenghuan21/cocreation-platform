"""本地 Image2 Edit 临时网关。"""
from __future__ import annotations

import asyncio
import json
import os
import signal
import tempfile
from collections.abc import Callable
from pathlib import Path
import uuid
from uuid import UUID

from sqlalchemy.orm import Session

from app.config.settings import settings
from app.schemas.industrial_design import IndustrialDesignImageEditRequest
from app.services.asset_blob_service import (
    AssetAccessDeniedError,
    AssetBlobService,
    AssetBlobError,
)
from app.services.safe_content_validator import (
    is_valid_image,
    trusted_image_decoder_available,
)


class Image2EditServiceError(Exception):
    """Image2 Edit 执行失败。"""

    def __init__(
        self,
        message: str,
        error_code: str = "IMAGE2_EDIT_FAILED",
        status_code: int = 400,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.error_code = error_code
        self.status_code = status_code


class Image2EditService:
    """通过临时脚本把参考图改图能力接入业务后端。"""

    max_output_size_bytes = 50 * 1024 * 1024
    process_timeout_seconds = 180.0
    termination_timeout_seconds = 5.0
    io_chunk_size = 1024 * 1024
    max_log_size_bytes = 1024 * 1024
    log_chunk_size = 64 * 1024

    def __init__(
        self,
        *,
        asset_service: AssetBlobService | None = None,
        script_path: Path | None = None,
        runtime_temp_root: Path | None = None,
        trusted_image_validator: Callable[[str, bytes], bool] | None = None,
    ) -> None:
        self.asset_service = asset_service or AssetBlobService(
            chunk_size=settings.ASSET_CHUNK_SIZE_BYTES
        )
        self.script_path = script_path or Path(
            os.getenv(
                "IMAGE2_EDIT_SCRIPT",
                "/tmp/codex-image2-edit-temp/run_image2_edit.sh",
            )
        )
        self.runtime_temp_root = runtime_temp_root
        self.trusted_image_validator = trusted_image_validator or (
            is_valid_image if trusted_image_decoder_available() else None
        )

    def configured(self) -> bool:
        return self.script_path.exists() and os.access(self.script_path, os.X_OK)

    async def edit_and_store(
        self,
        *,
        db: Session,
        user_id: str,
        request: IndustrialDesignImageEditRequest,
        image_asset_ids: list[str],
        mask_asset_id: str | None = None,
        task_id: str | None = None,
        publish_assets: bool = True,
    ) -> dict[str, object]:
        if self.trusted_image_validator is None:
            raise Image2EditServiceError(
                "图片处理能力不可用：当前环境缺少受信图片解码器",
                "IMAGE_PIPELINE_TRUSTED_DECODER_UNAVAILABLE",
                status_code=503,
            )
        if not self.configured():
            raise Image2EditServiceError(
                f"图片编辑脚本不可用：{self.script_path}",
                "IMAGE2_EDIT_SCRIPT_NOT_FOUND",
            )

        if not image_asset_ids:
            raise Image2EditServiceError(
                "图片资产不能为空",
                "IMAGE2_EDIT_IMAGE_ASSET_EMPTY",
            )
        image_edit_id = f"image2_edit_{uuid.uuid4().hex[:16]}"
        output_suffix = request.output_format if request.output_format != "jpeg" else "jpg"
        with tempfile.TemporaryDirectory(
            prefix="image2-edit-",
            dir=self.runtime_temp_root,
        ) as temporary_directory:
            temporary_path = Path(temporary_directory)
            image_paths = [
                self._materialize_asset(
                    db=db,
                    user_id=user_id,
                    asset_id=asset_id,
                    target_directory=temporary_path,
                    index=index,
                )
                for index, asset_id in enumerate(image_asset_ids)
            ]
            mask_path = (
                self._materialize_asset(
                    db=db,
                    user_id=user_id,
                    asset_id=mask_asset_id,
                    target_directory=temporary_path,
                    index=len(image_paths),
                )
                if mask_asset_id is not None
                else None
            )
            output_path = temporary_path / f"output.{output_suffix}"
            response_path = temporary_path / "response.json"
            cmd = [
                str(self.script_path),
                "--prompt",
                request.prompt,
                "--model",
                self._image_model(),
                "--size",
                request.size,
                "--quality",
                request.quality,
                "--output-format",
                request.output_format,
                "--out",
                str(output_path),
                "--response-out",
                str(response_path),
            ]
            if base_url := self._image_base_url():
                cmd.extend(["--base-url", base_url])
            if api_key := self._image_api_key():
                cmd.extend(["--api-key", api_key])
            if request.input_fidelity:
                cmd.extend(["--input-fidelity", request.input_fidelity])
            for image_path in image_paths:
                cmd.extend(["--image", str(image_path)])
            if mask_path:
                cmd.extend(["--mask", str(mask_path)])

            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=self._subprocess_environment(),
                # Production deployment is POSIX. A new session gives every edit
                # invocation an isolated process group so descendants are cleaned up.
                start_new_session=os.name == "posix",
            )
            process_group_id = process.pid if os.name == "posix" else None
            try:
                stdout, stderr = await self._collect_bounded_process_output(
                    process,
                    process_group_id=process_group_id,
                )
            except asyncio.CancelledError:
                await self._terminate_process(
                    process,
                    process_group_id=process_group_id,
                )
                raise
            stdout_text = stdout.decode("utf-8", errors="replace").strip()
            stderr_text = stderr.decode("utf-8", errors="replace").strip()
            if process.returncode != 0 or not output_path.is_file():
                detail = stderr_text or stdout_text or "图片编辑脚本未返回结果"
                raise Image2EditServiceError(detail)

            output_content = self._read_bounded_file(
                output_path,
                error_code="IMAGE2_EDIT_OUTPUT_TOO_LARGE",
            )
            output_content_type = {
                "png": "image/png",
                "jpeg": "image/jpeg",
                "webp": "image/webp",
            }[request.output_format]
            self._validate_image_output(output_content_type, output_content)
            output_asset = self.asset_service.store_bytes(
                db=db,
                user_id=user_id,
                filename=f"{image_edit_id}.{output_suffix}",
                content_type=output_content_type,
                kind="image",
                source="generated",
                content=output_content,
                task_id=task_id,
                metadata={"generator": "image2-edit"},
                publish=publish_assets,
            )
            response_asset_id: str | None = None
            if response_path.is_file():
                response_content = self._read_bounded_file(
                    response_path,
                    error_code="IMAGE2_EDIT_RESPONSE_TOO_LARGE",
                )
                try:
                    json.loads(response_content)
                except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                    raise Image2EditServiceError(
                        "图片编辑响应格式无效",
                        "IMAGE2_EDIT_RESPONSE_INVALID",
                    ) from exc
                response_asset = self.asset_service.store_bytes(
                    db=db,
                    user_id=user_id,
                    filename=f"{image_edit_id}.json",
                    content_type="application/json",
                    kind="source",
                    source="generated",
                    content=response_content,
                    task_id=task_id,
                    metadata={"generator": "image2-edit", "role": "response"},
                    publish=publish_assets,
                )
                response_asset_id = str(response_asset.id)

        return {
            "taskId": image_edit_id,
            "status": "completed",
            "outputAssetId": str(output_asset.id),
            "downloadUrl": (
                f"{settings.API_V1_PREFIX}/assets/{output_asset.id}/download"
            ),
            "responseAssetId": response_asset_id,
            "stdout": stdout_text,
        }

    async def _collect_bounded_process_output(
        self,
        process: asyncio.subprocess.Process,
        *,
        process_group_id: int | None = None,
    ) -> tuple[bytes, bytes]:
        if process.stdout is None or process.stderr is None:
            await self._terminate_process(
                process,
                process_group_id=process_group_id,
            )
            raise Image2EditServiceError(
                "图片编辑日志管道不可用",
                "IMAGE2_EDIT_LOG_UNAVAILABLE",
            )
        total = [0]
        overflow = asyncio.Event()

        async def read_stream(stream: asyncio.StreamReader) -> bytes:
            chunks: list[bytes] = []
            while chunk := await stream.read(self.log_chunk_size):
                total[0] += len(chunk)
                if total[0] > self.max_log_size_bytes:
                    overflow.set()
                    return b"".join(chunks)
                chunks.append(chunk)
            return b"".join(chunks)

        stdout_task = asyncio.create_task(read_stream(process.stdout))
        stderr_task = asyncio.create_task(read_stream(process.stderr))
        wait_task = asyncio.create_task(process.wait())
        overflow_task = asyncio.create_task(overflow.wait())

        async def wait_for_process_and_streams() -> tuple[bytes, bytes]:
            await wait_task
            return await asyncio.gather(stdout_task, stderr_task)

        completion_task = asyncio.create_task(wait_for_process_and_streams())
        try:
            done, _ = await asyncio.wait(
                {completion_task, overflow_task},
                timeout=self.process_timeout_seconds,
                return_when=asyncio.FIRST_COMPLETED,
            )
            if not done:
                await self._terminate_process(
                    process,
                    process_group_id=process_group_id,
                )
                raise Image2EditServiceError(
                    "图片编辑执行超时，请稍后重试",
                    "IMAGE2_EDIT_TIMEOUT",
                )
            if overflow.is_set():
                await self._terminate_process(
                    process,
                    process_group_id=process_group_id,
                )
                raise Image2EditServiceError(
                    "图片编辑日志超过 1MB 限制",
                    "IMAGE2_EDIT_LOG_TOO_LARGE",
                )
            stdout, stderr = await completion_task
            if overflow.is_set():
                raise Image2EditServiceError(
                    "图片编辑日志超过 1MB 限制",
                    "IMAGE2_EDIT_LOG_TOO_LARGE",
                )
            return stdout, stderr
        finally:
            for task in (
                completion_task,
                stdout_task,
                stderr_task,
                wait_task,
                overflow_task,
            ):
                if not task.done():
                    task.cancel()
            await asyncio.gather(
                stdout_task,
                stderr_task,
                wait_task,
                overflow_task,
                completion_task,
                return_exceptions=True,
            )

    async def _terminate_process(
        self,
        process: asyncio.subprocess.Process,
        *,
        process_group_id: int | None = None,
    ) -> None:
        if os.name == "posix" and process_group_id is not None:
            if self._signal_process_group(process_group_id, signal.SIGTERM):
                exited = await self._wait_for_process_group_exit(
                    process_group_id,
                    timeout=self.termination_timeout_seconds,
                )
                if not exited:
                    self._signal_process_group(process_group_id, signal.SIGKILL)
                    await self._wait_for_process_group_exit(
                        process_group_id,
                        timeout=self.termination_timeout_seconds,
                    )
            return
        if process.returncode is not None:
            return
        process.terminate()
        try:
            await asyncio.wait_for(
                process.wait(),
                timeout=self.termination_timeout_seconds,
            )
        except TimeoutError:
            process.kill()
            await asyncio.wait_for(
                process.wait(),
                timeout=self.termination_timeout_seconds,
            )

    @staticmethod
    def _signal_process_group(
        process_group_id: int,
        signal_number: signal.Signals,
    ) -> bool:
        try:
            os.killpg(process_group_id, signal_number)
        except ProcessLookupError:
            return False
        return True

    @staticmethod
    def _process_group_exists(process_group_id: int) -> bool:
        try:
            os.killpg(process_group_id, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        return True

    async def _wait_for_process_group_exit(
        self,
        process_group_id: int,
        *,
        timeout: float,
    ) -> bool:
        deadline = asyncio.get_running_loop().time() + timeout
        while self._process_group_exists(process_group_id):
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                return False
            await asyncio.sleep(min(0.02, remaining))
        return True

    def _read_bounded_file(self, path: Path, *, error_code: str) -> bytes:
        try:
            size = path.stat().st_size
        except OSError as exc:
            raise Image2EditServiceError(
                "图片编辑输出文件不可读取",
                "IMAGE2_EDIT_OUTPUT_UNREADABLE",
            ) from exc
        if size > self.max_output_size_bytes:
            raise Image2EditServiceError(
                "图片编辑输出超过 50MB 限制",
                error_code,
            )
        chunks: list[bytes] = []
        total = 0
        with path.open("rb") as stream:
            while chunk := stream.read(self.io_chunk_size):
                total += len(chunk)
                if total > self.max_output_size_bytes:
                    raise Image2EditServiceError(
                        "图片编辑输出超过 50MB 限制",
                        error_code,
                    )
                chunks.append(chunk)
        return b"".join(chunks)

    def _validate_image_output(self, content_type: str, content: bytes) -> None:
        if (
            self.trusted_image_validator is None
            or not self.trusted_image_validator(content_type, content)
        ):
            raise Image2EditServiceError(
                "图片编辑输出格式与请求不一致或文件结构无效",
                "IMAGE2_EDIT_OUTPUT_INVALID",
            )

    @staticmethod
    def _subprocess_environment() -> dict[str, str]:
        allowed = (
            "PATH",
            "LANG",
            "LC_ALL",
            "SSL_CERT_FILE",
            "SSL_CERT_DIR",
            "OPENAI_BASE_URL",
            "OPENAI_API_KEY",
        )
        return {
            name: value
            for name in allowed
            if (value := os.environ.get(name, "").strip())
        }

    @staticmethod
    def _image_base_url() -> str:
        return str(settings.NODAPI_BASE_URL or "").strip().rstrip("/")

    @staticmethod
    def _image_api_key() -> str:
        return str(settings.NODAPI_API_KEY or "").strip()

    @staticmethod
    def _image_model() -> str:
        return str(settings.NODAPI_IMAGE_MODEL or "gpt-image-2").strip()

    def _materialize_asset(
        self,
        *,
        db: Session,
        user_id: str,
        asset_id: str,
        target_directory: Path,
        index: int,
    ) -> Path:
        try:
            parsed_id = UUID(asset_id)
            asset = self.asset_service.get_asset(db, parsed_id, user_id)
            content = self.asset_service.read_bytes(db, parsed_id, user_id)
        except (ValueError, AssetAccessDeniedError, AssetBlobError) as exc:
            raise Image2EditServiceError(
                "图片资产不存在或不可用",
                "IMAGE2_EDIT_IMAGE_ASSET_INVALID",
            ) from exc
        suffix = Path(asset.filename).suffix.lower()
        target = target_directory / f"input-{index}{suffix}"
        target.write_bytes(content)
        return target


image2_edit_service = Image2EditService()
