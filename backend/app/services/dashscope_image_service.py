"""DashScope 通义万相图片生成服务。"""
from __future__ import annotations

from typing import Any

import httpx

from app.config.settings import settings


class DashScopeImageServiceError(Exception):
    """DashScope 图片生成调用失败。"""

    def __init__(self, message: str, error_code: str = "DASHSCOPE_IMAGE_ERROR", status_code: int = 400) -> None:
        super().__init__(message)
        self.message = message
        self.error_code = error_code
        self.status_code = status_code


class DashScopeImageService:
    """封装阿里云 DashScope 通义万相图片生成调用。"""

    def __init__(self) -> None:
        self.api_key = (settings.DASHSCOPE_API_KEY or "").strip()
        self.base_url = "https://dashscope.aliyuncs.com/api/v1"
        self.model = "qwen-image-max"
        self.timeout_seconds = 120.0

    @property
    def configured(self) -> bool:
        return bool(self.api_key)

    async def generate_design_image(
        self,
        *,
        prompt: str,
        images: list[str] | None = None,
        model: str | None = None,
    ) -> dict[str, Any]:
        if not self.configured:
            raise DashScopeImageServiceError(
                "DashScope 图片服务未配置，请设置 DASHSCOPE_API_KEY",
                "DASHSCOPE_IMAGE_NOT_CONFIGURED",
                status_code=503,
            )

        image_model = (model or self.model).strip()
        clean_prompt = prompt.strip()

        payload = {
            "model": image_model,
            "input": {
                "prompt": clean_prompt,
            },
        }

        if images:
            payload["input"]["reference_images"] = images

        url = f"{self.base_url}/services/aigc/text2image/image-synthesis"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "X-DashScope-Async": "enable",
        }

        try:
            async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
                response = await client.post(url, json=payload, headers=headers)
                response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise DashScopeImageServiceError(
                f"DashScope 图片接口返回错误：HTTP {exc.response.status_code}",
                "DASHSCOPE_IMAGE_HTTP_ERROR",
                status_code=exc.response.status_code,
            ) from exc
        except httpx.HTTPError as exc:
            raise DashScopeImageServiceError(
                f"无法连接 DashScope 图片接口：{exc}",
                "DASHSCOPE_IMAGE_REQUEST_FAILED",
                status_code=502,
            ) from exc

        data = response.json()
        if not isinstance(data, dict):
            raise DashScopeImageServiceError(
                "DashScope 响应格式不合法",
                "DASHSCOPE_IMAGE_RESPONSE_INVALID",
                status_code=502,
            )

        # 解析异步任务结果
        task_id = data.get("output", {}).get("task_id")
        if task_id:
            result = await self._poll_task(task_id)
            return {
                "imageUrl": result.get("results", [{}])[0].get("url"),
                "taskId": task_id,
                "model": image_model,
                "provider": "dashscope",
            }

        return {
            "imageUrl": None,
            "model": image_model,
            "provider": "dashscope",
        }

    async def _poll_task(self, task_id: str, max_retries: int = 30) -> dict[str, Any]:
        """轮询异步任务状态。"""
        url = f"{self.base_url}/tasks/{task_id}"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
        }

        for _ in range(max_retries):
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.get(url, headers=headers)
                response.raise_for_status()
                data = response.json()

                status = data.get("output", {}).get("task_status", "")
                if status == "SUCCEEDED":
                    return data.get("output", {})
                elif status == "FAILED":
                    raise DashScopeImageServiceError(
                        f"DashScope 任务失败：{data.get('output', {}).get('message', '未知错误')}",
                        "DASHSCOPE_TASK_FAILED",
                        status_code=500,
                    )

            import asyncio
            await asyncio.sleep(2)

        raise DashScopeImageServiceError(
            "DashScope 任务超时",
            "DASHSCOPE_TASK_TIMEOUT",
            status_code=504,
        )


dashscope_image_service = DashScopeImageService()
