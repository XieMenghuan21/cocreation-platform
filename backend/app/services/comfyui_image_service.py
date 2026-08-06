"""ComfyUI 图片生成服务：文生图 / 图生图。

封装 ComfyUI HTTP API（/prompt 提交工作流 + /history 轮询 + /view 取图），
返回与 NodAPI/DashScope 图片服务一致的 {resultUrl, taskId, model, status} 结构。
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx

from app.config.settings import settings


class ComfyUIImageServiceError(Exception):
    """ComfyUI 图片生成异常。"""

    def __init__(self, message: str, error_code: str = "COMFYUI_IMAGE_ERROR", status_code: int = 502) -> None:
        super().__init__(message)
        self.message = message
        self.error_code = error_code
        self.status_code = status_code


class ComfyUIImageService:
    """调用自建 ComfyUI 服务生成图片。"""

    def __init__(self) -> None:
        self.base_url = (settings.COMFYUI_BASE_URL or "").strip().rstrip("/")
        self.timeout_seconds = float(getattr(settings, "COMFYUI_TIMEOUT_SECONDS", 180) or 180)
        self.poll_interval = float(getattr(settings, "COMFYUI_POLL_INTERVAL_SECONDS", 2.0) or 2.0)
        self.default_size = settings.COMFYUI_IMAGE_SIZE.strip()

    @property
    def configured(self) -> bool:
        return bool(self.base_url)

    async def _request_json(self, method: str, path: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        url = f"{self.base_url}{path}"
        try:
            async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
                response = await client.request(method, url, json=payload if payload is not None else None)
                response.raise_for_status()
                return response.json()
        except httpx.HTTPStatusError as exc:
            raise ComfyUIImageServiceError(
                f"ComfyUI 请求失败：HTTP {exc.response.status_code}",
                "COMFYUI_HTTP_ERROR",
                status_code=exc.response.status_code,
            ) from exc
        except (httpx.TimeoutException, httpx.ConnectError) as exc:
            raise ComfyUIImageServiceError(
                f"ComfyUI 服务连接失败：{self.base_url}",
                "COMFYUI_UNREACHABLE",
                status_code=503,
            ) from exc

    async def _upload_image(self, image_url: str) -> str:
        """下载远端图片并上传到 ComfyUI input 目录，返回文件名。"""
        filename = "comfyui_input.png"
        try:
            async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
                image_response = await client.get(image_url)
                image_response.raise_for_status()
                upload_response = await client.post(
                    f"{self.base_url}/upload/image",
                    files={"image": (filename, image_response.content, "image/png")},
                    data={"overwrite": "true"},
                )
                upload_response.raise_for_status()
                data = upload_response.json()
                return str(data.get("name") or filename)
        except (httpx.HTTPStatusError, httpx.TimeoutException, httpx.ConnectError) as exc:
            raise ComfyUIImageServiceError(
                f"ComfyUI 图片上传失败：{image_url}",
                "COMFYUI_UPLOAD_FAILED",
                status_code=502,
            ) from exc

    def _build_workflow(
        self,
        *,
        prompt: str,
        input_filename: str | None = None,
        seed: int,
        steps: int,
        denoise: float,
        size: str,
    ) -> dict[str, Any]:
        width, height = self._parse_size(size)
        workflow: dict[str, Any] = {
            "1": {"class_type": "UNETLoader", "inputs": {"unet_name": settings.COMFYUI_UNET_NAME, "weight_dtype": "default"}},
            "2": {"class_type": "DualCLIPLoader", "inputs": {"clip_name1": settings.COMFYUI_CLIP_NAME1, "clip_name2": settings.COMFYUI_CLIP_NAME2, "type": "flux"}},
            "3": {"class_type": "VAELoader", "inputs": {"vae_name": settings.COMFYUI_VAE_NAME}},
            "6": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["2", 0], "text": prompt}},
        }
        if input_filename:
            workflow["4"] = {"class_type": "LoadImage", "inputs": {"image": input_filename}}
            workflow["5"] = {"class_type": "VAEEncode", "inputs": {"pixels": ["4", 0], "vae": ["3", 0]}}
            workflow["7"] = {
                "class_type": "KSampler",
                "inputs": {
                    "cfg": 1.0,
                    "denoise": denoise,
                    "latent_image": ["5", 0],
                    "model": ["1", 0],
                    "negative": ["6", 0],
                    "positive": ["6", 0],
                    "sampler_name": "euler",
                    "scheduler": "simple",
                    "seed": seed,
                    "steps": steps,
                },
            }
        else:
            workflow["4"] = {"class_type": "EmptyLatentImage", "inputs": {"batch_size": 1, "height": height, "width": width}}
            workflow["7"] = {
                "class_type": "KSampler",
                "inputs": {
                    "cfg": 1.0,
                    "denoise": 1.0,
                    "latent_image": ["4", 0],
                    "model": ["1", 0],
                    "negative": ["6", 0],
                    "positive": ["6", 0],
                    "sampler_name": "euler",
                    "scheduler": "simple",
                    "seed": seed,
                    "steps": steps,
                },
            }
        workflow["8"] = {"class_type": "VAEDecode", "inputs": {"samples": ["7", 0], "vae": ["3", 0]}}
        workflow["9"] = {
            "class_type": "SaveImage",
            "inputs": {"filename_prefix": settings.COMFYUI_OUTPUT_PREFIX, "images": ["8", 0]},
        }
        return workflow

    async def _submit_and_wait(self, workflow: dict[str, Any]) -> str:
        """提交工作流并轮询直到完成，返回输出图片文件名。"""
        result = await self._request_json(
            "POST",
            "/prompt",
            {"prompt": workflow},
        )
        node_errors = result.get("node_errors")
        if node_errors:
            raise ComfyUIImageServiceError(
                f"ComfyUI 工作流校验失败：{node_errors}",
                "COMFYUI_WORKFLOW_INVALID",
                status_code=502,
            )
        prompt_id = str(result.get("prompt_id") or "")
        if not prompt_id:
            raise ComfyUIImageServiceError(
                "ComfyUI 未返回 prompt_id",
                "COMFYUI_PROMPT_ID_MISSING",
                status_code=502,
            )

        deadline = asyncio.get_event_loop().time() + self.timeout_seconds
        while True:
            remaining = deadline - asyncio.get_event_loop().time()
            if remaining <= 0:
                raise ComfyUIImageServiceError(
                    "ComfyUI 图片生成超时",
                    "COMFYUI_GENERATION_TIMEOUT",
                    status_code=504,
                )
            await asyncio.sleep(min(self.poll_interval, remaining))
            history = await self._request_json("GET", f"/history/{prompt_id}")
            entry = history.get(prompt_id)
            if not entry:
                continue
            status = entry.get("status", {})
            if status.get("status_str") == "error" or status.get("status_str") == "error":
                raise ComfyUIImageServiceError(
                    f"ComfyUI 执行失败：{status.get('messages', [])}",
                    "COMFYUI_EXECUTION_ERROR",
                    status_code=502,
                )
            if not status.get("completed"):
                continue
            for node_output in entry.get("outputs", {}).values():
                for image in node_output.get("images", []):
                    if image.get("type") == "output":
                        return str(image.get("filename") or "").strip()
            raise ComfyUIImageServiceError(
                "ComfyUI 执行完成但未找到输出图片",
                "COMFYUI_OUTPUT_MISSING",
                status_code=502,
            )

    @staticmethod
    def _parse_size(size: str) -> tuple[int, int]:
        normalized = (size or "1024x1024").strip().lower().replace(" ", "")
        if "x" in normalized:
            parts = normalized.split("x")
            try:
                return int(parts[0]), int(parts[1])
            except ValueError:
                pass
        return 1024, 1024

    async def generate_design_image(
        self,
        *,
        prompt: str,
        images: list[str] | None = None,
        model: str | None = None,
    ) -> dict[str, Any]:
        """文生图 / 图生图。images 提供时走图生图（denoise 0.6），否则文生图。"""
        if not self.configured:
            raise ComfyUIImageServiceError(
                "ComfyUI 未配置（COMFYUI_BASE_URL 为空）",
                "COMFYUI_NOT_CONFIGURED",
                status_code=503,
            )
        final_prompt = (prompt or "").strip()[:2000]
        if not final_prompt:
            raise ComfyUIImageServiceError(
                "ComfyUI 提示词为空",
                "COMFYUI_PROMPT_EMPTY",
                status_code=400,
            )

        input_filename = None
        denoise = float(getattr(settings, "COMFYUI_IMG2IMG_DENOISE", 0.6) or 0.6)
        if images:
            reference_url = next((url for url in images if url), None)
            if reference_url:
                input_filename = await self._upload_image(reference_url)

        seed = int(getattr(settings, "COMFYUI_FIXED_SEED", 0) or 0)
        steps = int(getattr(settings, "COMFYUI_STEPS", 4) or 4)
        workflow = self._build_workflow(
            prompt=final_prompt,
            input_filename=input_filename,
            seed=seed,
            steps=steps,
            denoise=denoise,
            size=self.default_size,
        )
        output_filename = await self._submit_and_wait(workflow)

        result_url = f"{self.base_url}/view?filename={output_filename}&subfolder=&type=output"
        return {
            "taskId": f"comfyui_{output_filename}",
            "model": settings.COMFYUI_MODEL_LABEL,
            "resultUrl": result_url,
            "status": "completed",
            "raw": {"filename": output_filename},
        }


comfyui_image_service = ComfyUIImageService()
