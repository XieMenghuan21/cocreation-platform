"""build123d 开源 CAD 生成服务：LLM 生成建模代码 -> 子进程执行 -> STEP/STL/GLB + HLR 线图 + 多视角渲染。"""
from __future__ import annotations

import importlib.util
import json
import logging
import os
import re
import subprocess
import sys
import tempfile
import textwrap
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping

import httpx
from sqlalchemy.orm import Session

from app.config.settings import settings
from app.services.asset_blob_service import AssetBlobService

logger = logging.getLogger(__name__)

JsonValue = None | bool | int | float | str | list["JsonValue"] | dict[str, "JsonValue"]

BUILD123D_SYSTEM_PROMPT = """你是资深机械设计 CAD 建模专家，使用 Python 的 build123d 库（基于 OpenCascade 内核）生成参数化 3D 模型代码。

严格要求：
1. 只输出纯 Python 代码，不要任何解释、markdown 代码块标记或其他文本。
2. 第一行必须是：from build123d import *
3. 使用这些标准 API（不要发明不存在的函数）：
   - 基础体：Box(width, height, depth)、Cylinder(radius, height)、Sphere(radius)、Cone(radius_bottom, height)
   - 2D：Rectangle(w, h)、Circle(radius)、RegularPolygon(radius, side_count)
   - 拉伸：extrude(face_or_sketch, amount)
   - 定位：Pos(x, y, z) * obj、Rot(axis, angle) * obj、Plane 组合
   - 布尔运算：part += obj / part -= obj / part *= obj
   - 圆角倒角：除非产品确实需要，否则默认不要使用 fillet()/chamfer()；必须用时，radius 要小于相邻边长一半，且先确认所选边存在（用 obj.edges().filter_by(...) 精确选少量边，禁止对孔洞/圆孔边缘使用，禁止用边号/索引猜测边）
   - 挖孔：用 Cylinder + part -=，孔穿通时高度要足够
   - 2D 面对象（Circle、Rectangle、Sketch）没有 outer_wire()/edges()/vertices() 等方法；取 3D 实体的边统一用 obj.edges().filter_by(...)，不要对某个面再取 outer_wire
   - 选择器只允许这两种写法：obj.edges().filter_by(lambda e: e.length == 20) 或 obj.faces().sort_by(Axis.X)[-1]，禁止使用 Length 之类的单词作过滤器；除这两种之外的选择器 API（filter_by_position、filter_by_radius、sort_by_distance、vertices()、wires()、outer_wire() 等）一律禁止使用，否则代码必然报错
   - 一个子对象被 part += 合并进主对象后，禁止再对那个子对象调用任何方法（其边/面已不属于主对象）；需要圆角时只对最终主对象（如 part/result）的边做 fillet，且 radius 取较小值（如 0.5-2）
   - 需要三角函数等数学函数时，在代码开头加：from math import *
4. 绝对禁止：对象名 .translate(...) 和 .rotate(...) 方法（build123d 不支持这种调用方式），一律用 Pos(...) * obj 或 Rot(...) * obj 实现平移旋转；不要在空对象上做 += 操作，先用 Box/Cylinder 创建初始对象再加减。
5. 所有尺寸单位为毫米，模型要结构合理、可制造。
6. 脚本最后一行必须是：result = <你的最终模型变量>
7. 不要使用 export_step/export_stl/import_* 等导出导入函数，也不要用 show_object 等查看函数。

下面是一个完全正确可运行的示例代码，请严格模仿它的写法：
from build123d import *

part = Box(100, 60, 20)
part -= Pos(0, 0, 0) * Cylinder(8, 30)
plate = Pos(0, 0, 10) * Box(40, 40, 5)
part += plate
part = fillet(part.edges().filter_by(lambda e: e.length == 20), 2)
result = part"""

_MODEL_EXPORT_SNIPPET = """
# ---- 平台导出段（自动注入，勿修改） ----
import sys as _b123_sys
from pathlib import Path as _b123_Path
_b123_out = _b123_Path(_b123_sys.argv[1])
_b123_out.mkdir(parents=True, exist_ok=True)
from build123d import export_step, export_stl
export_step(result, str(_b123_out / "model.step"))
export_stl(result, str(_b123_out / "model.stl"))
try:
    from build123d import export_gltf
    export_gltf(result, str(_b123_out / "model.glb"), binary=True)
except Exception as _b123_glb_error:
    print("GLB_SKIPPED:", type(_b123_glb_error).__name__)
print("MODEL_OK")
"""

_HLR_SCRIPT = """\
import importlib.util, sys
from pathlib import Path
import os
spec = importlib.util.spec_from_file_location("gen", sys.argv[1])
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
from build123d.exporters import Drawing, ExportSVG, ExportDXF
out = Path(sys.argv[2])
out.mkdir(parents=True, exist_ok=True)
part = mod.result
drawing = Drawing(part, look_from=(1, -1, 1), look_up=(0, 0, 1), with_hidden=False)
svg = ExportSVG()
svg.add_shape(drawing.visible_lines)
svg.write(str(out / "plan.svg"))
dxf = ExportDXF()
dxf.add_shape(drawing.visible_lines)
dxf.write(str(out / "plan.dxf"))
print("HLR_OK")
"""

_RENDER_SCRIPT = """\
import os
os.environ.setdefault("PYOPENGL_PLATFORM", "osmesa")
import sys
import numpy as np
import pyrender
import trimesh
from PIL import Image
from pathlib import Path

stl_path, out_dir = sys.argv[1], Path(sys.argv[2])
out_dir.mkdir(parents=True, exist_ok=True)
mesh = trimesh.load(stl_path)
scale = 1.0
bounds = mesh.bounds
size = (bounds[1] - bounds[0]).max()
if size > 0:
    scale = 0.1 / size
mesh.apply_scale(scale)

radius = max(mesh.bounds[1] - mesh.bounds[0]) * 2.2

views = {
    "iso": (radius, -radius, radius),
    "front": (0, -1.6 * radius, 0.3 * radius),
    "side": (1.6 * radius, 0, 0.3 * radius),
    "top": (0.3 * radius, 0.3 * radius, 1.6 * radius),
}
colors = {
    "metal_blue": (0.20, 0.32, 0.46),
    "anodized_black": (0.12, 0.12, 0.13),
    "brushed_alum": (0.72, 0.73, 0.74),
    "matte_white": (0.85, 0.85, 0.85),
}


def look_at(eye, target=(0.0, 0.0, 0.0), up=(0.0, 0.0, 1.0)):
    eye = np.asarray(eye, dtype=float)
    target = np.asarray(target, dtype=float)
    up = np.asarray(up, dtype=float)
    fwd = target - eye
    fwd = fwd / np.linalg.norm(fwd)
    side = np.cross(fwd, up)
    side = side / np.linalg.norm(side)
    up2 = np.cross(side, fwd)
    pose = np.eye(4)
    pose[:3, 0] = side
    pose[:3, 1] = up2
    pose[:3, 2] = -fwd
    pose[:3, 3] = eye
    return pose


renderer = pyrender.OffscreenRenderer(900, 700)
for view_key, eye in views.items():
    for color_key, rgb in colors.items():
        colored = mesh.copy()
        rgb255 = np.array(
            [int(rgb[0] * 255), int(rgb[1] * 255), int(rgb[2] * 255), 255],
            dtype=np.uint8,
        )
        colored.visual.vertex_colors = np.tile(rgb255, (len(colored.vertices), 1))
        scene = pyrender.Scene(ambient_light=[0.35, 0.35, 0.35])
        scene.add(pyrender.Mesh.from_trimesh(colored, smooth=True))
        cam = pyrender.PerspectiveCamera(yfov=np.pi / 4.0)
        scene.add(cam, pose=look_at(eye))
        light = pyrender.DirectionalLight(color=[1, 1, 1], intensity=4.0)
        light_pose = np.eye(4)
        light_pose[2, 3] = 1.5
        scene.add(light, pose=light_pose)
        light2 = pyrender.DirectionalLight(color=[0.8, 0.8, 0.85], intensity=1.5)
        light_pose2 = np.eye(4)
        light_pose2[2, 3] = -1.5
        scene.add(light2, pose=light_pose2)
        color_img, _ = renderer.render(scene)
        Image.fromarray(color_img).save(str(out_dir / f"{view_key}_{color_key}.png"))
renderer.delete()
print("RENDER_OK")
"""

_VIEW_LABELS = {
    "iso": "正等轴测",
    "front": "主视图",
    "side": "侧视图",
    "top": "俯视图",
}
_COLOR_LABELS = {
    "metal_blue": "金属蓝",
    "anodized_black": "阳极黑",
    "brushed_alum": "拉丝铝",
    "matte_white": "哑光白",
}


class Build123dServiceError(Exception):
    """build123d 服务可预期错误。"""

    def __init__(
        self,
        message: str,
        error_code: str,
        status_code: int = 502,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.error_code = error_code
        self.status_code = status_code


class Build123dService:
    """负责 LLM 生成 build123d 代码并执行导出 STEP/STL/GLB、HLR 线图与多视角渲染图。"""

    def __init__(
        self,
        *,
        asset_service: AssetBlobService | None = None,
        runtime_temp_root: Path | None = None,
    ) -> None:
        self.base_url = os.getenv(
            "BUILD123D_LLM_BASE_URL",
            str(getattr(settings, "QWEN_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1")),
        ).rstrip("/")
        self.api_key = (
            os.getenv("BUILD123D_LLM_API_KEY", "").strip()
            or getattr(settings, "QWEN_API_KEY", "") or ""
            or getattr(settings, "DASHSCOPE_API_KEY", "") or ""
        ).strip()
        self.model = os.getenv("BUILD123D_LLM_MODEL", "qwen-plus").strip() or "qwen-plus"
        self.request_timeout = float(os.getenv("BUILD123D_LLM_TIMEOUT_SECONDS", "180"))
        self.exec_timeout = float(os.getenv("BUILD123D_EXEC_TIMEOUT_SECONDS", "300"))
        self.max_retries = int(os.getenv("BUILD123D_MAX_RETRIES", "3"))
        self.render_views_enabled = os.getenv("BUILD123D_RENDER_ENABLED", "1") != "0"
        self.asset_service = asset_service or AssetBlobService(
            chunk_size=settings.ASSET_CHUNK_SIZE_BYTES
        )
        self.runtime_temp_root = runtime_temp_root

    @property
    def available(self) -> bool:
        return importlib.util.find_spec("build123d") is not None

    @property
    def configured(self) -> bool:
        return self.available and bool(self.base_url)

    def _asset_url(self, asset_id: object) -> str | None:
        return f"{settings.API_V1_PREFIX}/assets/{asset_id}/download" if asset_id else None

    def _subprocess_environment(self) -> dict[str, str]:
        env = dict(os.environ)
        for key in (
            "FORGECAD_BRIDGE_BASE_URL",
            "FORGECAD_BRIDGE_TOKEN",
            "FORGECAD_QWEN_API_KEY",
            "BUILD123D_LLM_API_KEY",
            "QWEN_API_KEY",
            "DASHSCOPE_API_KEY",
            "OPENAI_API_KEY",
            "GEMINI_API_KEY",
            "DEEPSEEK_API_KEY",
            "CLAUDE_API_KEY",
            "GLM_API_KEY",
            "DOUBAO_API_KEY",
            "JWT_SECRET_KEY",
            "DATABASE_URL",
        ):
            env.pop(key, None)
        env["PYOPENGL_PLATFORM"] = "osmesa"
        return env

    async def _request_llm(
        self,
        messages: list[dict[str, str]],
        temperature: float,
    ) -> str:
        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": 4096,
        }
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        try:
            async with httpx.AsyncClient(timeout=self.request_timeout) as client:
                response = await client.post(
                    f"{self.base_url}/chat/completions",
                    json=payload,
                    headers=headers,
                )
                response.raise_for_status()
                data = response.json()
        except httpx.HTTPError as exc:
            raise Build123dServiceError(
                f"调用 LLM 生成 build123d 代码失败：{exc}",
                "BUILD123D_LLM_REQUEST_FAILED",
                status_code=502,
            ) from exc
        content = self._extract_content(data)
        if not content.strip():
            raise Build123dServiceError(
                "LLM 响应中没有可用内容",
                "BUILD123D_LLM_RESPONSE_EMPTY",
                status_code=502,
            )
        return content

    @staticmethod
    def _extract_content(data: object) -> str:
        if not isinstance(data, Mapping):
            return ""
        choices = data.get("choices")
        if not isinstance(choices, list) or not choices:
            return ""
        first = choices[0]
        if not isinstance(first, Mapping):
            return ""
        message = first.get("message")
        if not isinstance(message, Mapping):
            return ""
        content = message.get("content")
        return content if isinstance(content, str) else ""

    def _extract_code(self, raw_content: str) -> str:
        content = raw_content.strip()
        fenced = re.search(
            r"```(?:python|py)?\s*(.*?)```",
            content,
            re.DOTALL | re.IGNORECASE,
        )
        code = fenced.group(1).strip() if fenced else content
        if not code.strip().startswith("from build123d"):
            code = code.strip() + "\n"
        return code

    def _build_user_prompt(self, design_prompt: str) -> str:
        return f"生成以下设计的 build123d 代码：\n{design_prompt}"

    def _run_script(
        self,
        script: Path,
        work_dir: Path,
        *args: str,
    ) -> tuple[bool, str]:
        try:
            proc = subprocess.run(
                [sys.executable, str(script), *args],
                cwd=str(work_dir),
                capture_output=True,
                text=True,
                timeout=self.exec_timeout,
                env=self._subprocess_environment(),
                check=False,
            )
        except subprocess.TimeoutExpired:
            return False, f"脚本执行超时（{int(self.exec_timeout)}s）"
        except OSError as exc:
            return False, f"脚本进程启动失败：{exc}"
        combined = proc.stdout.strip() + "\n" + proc.stderr.strip()
        if proc.returncode != 0:
            return False, (combined or "脚本执行失败").strip()[-3000:]
        return True, combined

    async def _generate_executable_code(
        self,
        design_prompt: str,
    ) -> tuple[str, list[dict[str, str]]]:
        """生成可执行代码，执行失败时带 stderr 反馈重试，返回 (code, messages)。"""
        messages: list[dict[str, str]] = [
            {"role": "system", "content": BUILD123D_SYSTEM_PROMPT},
            {"role": "user", "content": self._build_user_prompt(design_prompt)},
        ]
        temperatures = [0.2, 0.15, 0.1]
        last_error = ""
        for attempt in range(self.max_retries):
            temperature = temperatures[min(attempt, len(temperatures) - 1)]
            raw = await self._request_llm(messages, temperature=temperature)
            code = self._extract_code(raw)
            if not code.strip():
                raise Build123dServiceError(
                    "LLM 未返回可执行的 build123d 代码",
                    "BUILD123D_SCRIPT_EMPTY",
                    status_code=502,
                )
            with tempfile.TemporaryDirectory(
                prefix="build123d-exec-",
                dir=self.runtime_temp_root,
            ) as temporary_directory:
                work_dir = Path(temporary_directory)
                script_path = work_dir / "model.py"
                script_path.write_text(code + "\n" + _MODEL_EXPORT_SNIPPET, encoding="utf-8")
                ok, error_output = await self._run_script_sync(
                    script_path,
                    work_dir,
                    str(work_dir),
                )
            if ok:
                return code, messages
            last_error = error_output
            retry_guidance = ""
            if re.search(r"fillet|chamfer|Nothing to (fillet|chamfer)", error_output, re.IGNORECASE):
                retry_guidance = (
                    "\n特别注意：如果错误来自 fillet 或 chamfer，直接删除代码中所有 fillet() 和 chamfer() 调用（圆角是可选优化，不要因此牺牲模型可用性），保持其余几何不变。"
                )
            messages.append({"role": "assistant", "content": raw})
            messages.append({
                "role": "user",
                "content": (
                    f"脚本执行报错，请修复后重新输出完整代码。错误信息：\n{error_output[:3000]}\n{retry_guidance}"
                ),
            })
        raise Build123dServiceError(
            f"build123d 脚本执行重试 {self.max_retries} 次后仍失败，最近错误：{last_error[:800]}",
            "BUILD123D_EXEC_FAILED",
            status_code=502,
        )

    async def _run_script_sync(
        self,
        script: Path,
        work_dir: Path,
        *args: str,
    ) -> tuple[bool, str]:
        """线程池中执行子进程脚本，返回 (ok, output)。"""
        import asyncio
        return await asyncio.get_running_loop().run_in_executor(
            None,
            self._run_script,
            script,
            work_dir,
            *args,
        )

    def _store_asset(
        self,
        *,
        db: Session,
        user_id: str,
        task_id: str,
        filename: str,
        content_type: str,
        kind: str,
        content: bytes,
        publish: bool,
        metadata: dict[str, object] | None = None,
    ):
        return self.asset_service.store_bytes(
            db=db,
            user_id=user_id,
            filename=filename,
            content_type=content_type,
            kind=kind,
            source="generated",
            content=content,
            task_id=task_id,
            metadata={"build123dTaskId": task_id, **(metadata or {})},
            publish=publish,
        )

    async def generate_model(
        self,
        *,
        prompt: str,
        db: Session,
        user_id: str,
        task_id: str | None = None,
        publish_assets: bool = True,
        render_views: bool = False,
    ) -> dict[str, JsonValue]:
        """生成 build123d 模型并导出 STEP/STL/GLB，可选附带多视角渲染图。"""
        if not self.available:
            raise Build123dServiceError(
                "build123d 未安装，无法执行 3D 生成",
                "BUILD123D_IMPORT_UNAVAILABLE",
                status_code=503,
            )
        if not self.base_url:
            raise Build123dServiceError(
                "未配置 build123d LLM 通道（BUILD123D_LLM_BASE_URL）",
                "BUILD123D_LLM_NOT_CONFIGURED",
                status_code=503,
            )
        code, _ = await self._generate_executable_code(prompt)
        build123d_task_id = f"build123d_{uuid.uuid4().hex[:16]}"
        with tempfile.TemporaryDirectory(
            prefix="build123d-task-",
            dir=self.runtime_temp_root,
        ) as temporary_directory:
            work_dir = Path(temporary_directory)
            script_path = work_dir / "model.py"
            script_path.write_text(code + "\n" + _MODEL_EXPORT_SNIPPET, encoding="utf-8")
            ok, output = await self._run_script_sync(script_path, work_dir, str(work_dir))
            if not ok:
                raise Build123dServiceError(
                    f"build123d 脚本执行失败：{output[:1000]}",
                    "BUILD123D_EXEC_FAILED",
                    status_code=502,
                )
            step_bytes = (work_dir / "model.step").read_bytes()
            stl_bytes = (work_dir / "model.stl").read_bytes()
            glb_path = work_dir / "model.glb"
            glb_bytes = glb_path.read_bytes() if glb_path.is_file() else None

            script_asset = self._store_asset(
                db=db, user_id=user_id, task_id=task_id,
                filename=f"{build123d_task_id}.py",
                content_type="text/x-python", kind="source",
                content=code.encode("utf-8"), publish=publish_assets,
            )
            step_asset = self._store_asset(
                db=db, user_id=user_id, task_id=task_id,
                filename=f"{build123d_task_id}.step",
                content_type="application/step", kind="cad",
                content=step_bytes, publish=publish_assets,
                metadata={"format": "step"},
            )
            stl_asset = self._store_asset(
                db=db, user_id=user_id, task_id=task_id,
                filename=f"{build123d_task_id}.stl",
                content_type="model/stl", kind="cad",
                content=stl_bytes, publish=publish_assets,
                metadata={"format": "stl"},
            )
            glb_asset = None
            if glb_bytes is not None:
                glb_asset = self._store_asset(
                    db=db, user_id=user_id, task_id=task_id,
                    filename=f"{build123d_task_id}.glb",
                    content_type="model/gltf-binary", kind="cad",
                    content=glb_bytes, publish=publish_assets,
                    metadata={"format": "glb"},
                )
            render_views_result = None
            if render_views and self.render_views_enabled:
                render_views_result = await self._generate_render_views(
                    stl_bytes=stl_bytes,
                    task_id=task_id,
                    build123d_task_id=build123d_task_id,
                    db=db,
                    user_id=user_id,
                    publish_assets=publish_assets,
                )
        step_url = self._asset_url(step_asset.id)
        result: dict[str, JsonValue] = {
            "taskId": build123d_task_id,
            "status": "completed",
            "script": code,
            "scriptAssetId": str(script_asset.id),
            "outputAssetId": str(step_asset.id),
            "modelStepAssetId": str(step_asset.id),
            "modelStlAssetId": str(stl_asset.id),
            "modelScriptAssetId": str(script_asset.id),
            "modelDownloadUrl": step_url,
            "modelStep": step_url,
            "modelStl": self._asset_url(stl_asset.id),
            "logs": output.strip()[-2000:],
            "cliExecuted": True,
            "exportFormat": "step",
        }
        if glb_asset is not None:
            result["modelGlbAssetId"] = str(glb_asset.id)
            result["modelGlb"] = self._asset_url(glb_asset.id)
        if render_views_result is not None:
            result["renderViews"] = render_views_result["renderViews"]
            result["renderViewsPreview"] = render_views_result["renderViewsPreview"]
        return result

    async def _generate_render_views(
        self,
        *,
        stl_bytes: bytes,
        task_id: str | None,
        build123d_task_id: str,
        db: Session,
        user_id: str,
        publish_assets: bool,
    ) -> dict[str, JsonValue]:
        try:
            with tempfile.TemporaryDirectory(
                prefix="build123d-render-",
                dir=self.runtime_temp_root,
            ) as temporary_directory:
                work_dir = Path(temporary_directory)
                stl_path = work_dir / "model.stl"
                stl_path.write_bytes(stl_bytes)
                script_path = work_dir / "render.py"
                script_path.write_text(textwrap.dedent(_RENDER_SCRIPT), encoding="utf-8")
                ok, output = await self._run_script_sync(script_path, work_dir, str(stl_path), str(work_dir))
                if not ok or "RENDER_OK" not in output:
                    logger.warning("build123d 渲染失败：%s", output[-1000:])
                    return {
                        "renderViews": [],
                        "renderViewsPreview": None,
                        "diagnostics": [{
                            "level": "warning",
                            "title": "多视角渲染生成失败",
                            "detail": "3D 渲染图生成失败，模型已正常产出。",
                        }],
                    }
                render_views: list[dict[str, JsonValue]] = []
                preview_url: str | None = None
                for png_path in sorted(work_dir.glob("*.png")):
                    key = png_path.stem
                    if "_" not in key:
                        continue
                    view_key, color_key = key.split("_", 1)
                    asset = self._store_asset(
                        db=db, user_id=user_id, task_id=task_id,
                        filename=f"{build123d_task_id}_{key}.png",
                        content_type="image/png", kind="image",
                        content=png_path.read_bytes(), publish=publish_assets,
                        metadata={"view": view_key, "color": color_key, "format": "png"},
                    )
                    url = self._asset_url(asset.id)
                    render_views.append({
                        "key": key,
                        "view": view_key,
                        "color": color_key,
                        "label": f"{_VIEW_LABELS.get(view_key, view_key)}·{_COLOR_LABELS.get(color_key, color_key)}",
                        "assetId": str(asset.id),
                        "url": url,
                    })
                    if preview_url is None:
                        preview_url = url
                return {
                    "renderViews": render_views,
                    "renderViewsPreview": preview_url,
                }
        except Exception as exc:
            logger.exception("build123d 渲染视图生成失败: %s", exc)
            return {
                "renderViews": [],
                "renderViewsPreview": None,
                "diagnostics": [{
                    "level": "warning",
                    "title": "多视角渲染生成失败",
                    "detail": "3D 渲染图生成失败，模型已正常产出。",
                }],
            }

    async def generate_plan_line(
        self,
        *,
        prompt: str,
        db: Session,
        user_id: str,
        task_id: str | None = None,
        publish_assets: bool = True,
    ) -> dict[str, JsonValue]:
        """生成 2D CAD 线图（SVG + DXF，HLR 隐藏线消除投影）。"""
        if not self.available:
            raise Build123dServiceError(
                "build123d 未安装，无法生成 CAD 线图",
                "BUILD123D_IMPORT_UNAVAILABLE",
                status_code=503,
            )
        if not self.base_url:
            raise Build123dServiceError(
                "未配置 build123d LLM 通道（BUILD123D_LLM_BASE_URL）",
                "BUILD123D_LLM_NOT_CONFIGURED",
                status_code=503,
            )
        code, _ = await self._generate_executable_code(prompt)
        build123d_task_id = f"build123d_line_{uuid.uuid4().hex[:16]}"
        with tempfile.TemporaryDirectory(
            prefix="build123d-line-",
            dir=self.runtime_temp_root,
        ) as temporary_directory:
            work_dir = Path(temporary_directory)
            model_path = work_dir / "model.py"
            model_path.write_text(code, encoding="utf-8")
            hlr_path = work_dir / "hlr.py"
            hlr_path.write_text(textwrap.dedent(_HLR_SCRIPT), encoding="utf-8")
            ok, output = await self._run_script_sync(
                hlr_path,
                work_dir,
                str(model_path),
                str(work_dir),
            )
            if not ok or "HLR_OK" not in output:
                raise Build123dServiceError(
                    f"CAD 线图投影失败：{output[-1000:]}",
                    "BUILD123D_PLANLINE_FAILED",
                    status_code=502,
                )
            svg_bytes = (work_dir / "plan.svg").read_bytes()
            dxf_bytes = (work_dir / "plan.dxf").read_bytes()
            svg_asset = self._store_asset(
                db=db, user_id=user_id, task_id=task_id,
                filename=f"{build123d_task_id}.svg",
                content_type="image/svg+xml", kind="drawing",
                content=svg_bytes, publish=publish_assets,
                metadata={"format": "svg"},
            )
            dxf_asset = self._store_asset(
                db=db, user_id=user_id, task_id=task_id,
                filename=f"{build123d_task_id}.dxf",
                content_type="application/dxf", kind="drawing",
                content=dxf_bytes, publish=publish_assets,
                metadata={"format": "dxf"},
            )
        svg_url = self._asset_url(svg_asset.id)
        return {
            "taskId": build123d_task_id,
            "status": "completed",
            "script": code,
            "scriptAssetId": None,
            "outputAssetId": str(svg_asset.id),
            "planLineSvgAssetId": str(svg_asset.id),
            "planLineDxfAssetId": str(dxf_asset.id),
            "planLine": svg_url,
            "planLineDxf": self._asset_url(dxf_asset.id),
            "logs": output.strip()[-2000:],
            "cliExecuted": True,
            "exportFormat": "svg+dxf",
        }


build123d_service = Build123dService()
