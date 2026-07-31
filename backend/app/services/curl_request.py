"""基于 curl 的轻量 HTTP 请求工具。

当前 Python 运行环境对部分外部域名的 DNS 解析不稳定，而系统 curl 可正常访问。
这里仅用于 NodAPI 这类外部服务，避免影响本地服务链路。
"""
from __future__ import annotations

import asyncio
import json
import shutil
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class CurlResponse:
    status_code: int
    body: str
    stderr: str


class CurlRequestError(RuntimeError):
    """curl 请求失败。"""

    def __init__(self, message: str, *, status_code: int = 502) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code


async def request_json(
    *,
    method: str,
    url: str,
    headers: dict[str, str],
    json_payload: dict[str, Any] | None,
    timeout_seconds: float,
) -> CurlResponse:
    """使用 curl 发起请求并返回原始响应文本。"""
    curl_bin = shutil.which("curl")
    if not curl_bin:
        raise CurlRequestError("系统未安装 curl，无法访问 NodAPI", status_code=503)

    args: list[str] = [curl_bin, "-sS", "-X", method.upper()]
    for key, value in headers.items():
        if value:
            args.extend(["-H", f"{key}: {value}"])
    if json_payload is not None:
        args.extend(["--data-raw", json.dumps(json_payload, ensure_ascii=False, separators=(",", ":"))])
    args.extend([url, "-w", "\n%{http_code}"])

    proc = await asyncio.create_subprocess_exec(
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout_seconds)
    except TimeoutError as exc:
        proc.kill()
        await proc.wait()
        raise CurlRequestError(f"curl 请求超时：{url}", status_code=504) from exc

    output = stdout.decode("utf-8", errors="replace")
    error_output = stderr.decode("utf-8", errors="replace")
    if proc.returncode not in (0, None):
        raise CurlRequestError(error_output.strip() or output.strip() or f"curl 请求失败：{url}", status_code=502)
    if not output.strip():
        raise CurlRequestError(error_output.strip() or "curl 未返回任何响应", status_code=502)

    body, status_code = _split_body_and_status(output)
    return CurlResponse(status_code=status_code, body=body, stderr=error_output.strip())


def _split_body_and_status(output: str) -> tuple[str, int]:
    text = output.rstrip("\n")
    if not text:
        raise CurlRequestError("curl 返回内容为空", status_code=502)

    body, _, status_text = text.rpartition("\n")
    if not status_text.isdigit():
        raise CurlRequestError("curl 响应缺少 HTTP 状态码", status_code=502)
    if not body:
        body = ""
    return body, int(status_text)
