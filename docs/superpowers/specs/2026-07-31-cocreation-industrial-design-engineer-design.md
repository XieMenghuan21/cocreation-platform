# 共创平台工作台 7 步独立生成重构 + 3D 生成开源替换（build123d）设计

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把共创平台工作台从「三场景锁定步骤、按场景整批生成」重构为「7 个扁平步骤各自独立生成、可任意跳转」的形态；同时用开源 build123d（OpenCascade 内核）替换不可用的 ForgeCAD 闭源链路，支撑 3D 打样与 STEP 图两个步骤；2D 平面图改为 CAD 线图（从 3D 数模 HLR 投影的 SVG+DXF 线稿）；3D 打样步骤增强「多视角渲染图 + 配色方案」（从 3D 数模直接渲染，几何与配色一致），与真实渲染风格的设计图区分开。

**Architecture:** 前端将 `scenarioConfigs` 的步骤分组升级为单一扁平步骤序列（参考图/文字、2D 平面图、设计图、精修图、场景融合图、3D 打样、STEP图），场景标签（设计/宣发/生产）退化为纯视觉分组；每个步骤持有独立的状态机（idle/running/completed/failed）与独立产物，点击未执行步骤只触发生成该步骤，点击已执行步骤切换预览。后端新增 build123d 代码生成执行服务（LLM 生成 Python 建模代码 → 子进程执行 → 导出 STEP/STL/GLB + HLR 线图 SVG/DXF + pyrender 多视角配色渲染 PNG），复用现有资产入库与版本快照协议；`IndustrialDesignWorkflowOptions` 新增 `generatePlanLine`（2D 线图）、`generateRenderViews`（3D 多视角渲染+配色）与 `cadProvider`（3D 后端切换开关）三个选项。

**Tech Stack:** React 18 + TypeScript + Tailwind CSS（前端）；Python 3.12 + FastAPI + build123d 0.11.1 + cadquery-ocp-novtk（建模）+ pyrender 0.1.45 + trimesh（渲染）+ PyOpenGL 3.1.7（OSMesa 软渲染）（后端）；LLM 走现有 DashScope/本地 Qwen 兼容通道。

---

## 一、背景与已验证结论

### 1.1 现状问题

1. **步骤被场景锁定**：`scenarioConfigs` 中每步只属于一个场景（设计3步/宣发2步/生产2步），`scenarioTabs` 只渲染「设计/宣发」两个 tab（生产场景无独立 tab）；切换场景会重置 `activeStepIndex`，无法从设计图直接跳到精修图或 3D 打样。
2. **按场景整批生成**：`submitIndustrialDesignWorkflow` 以 stage 为粒度一次提交整场景选项（如 production → generateCad + generateThreePreview 同时为 true），用户无法只生成其中一步。
3. **ForgeCAD 链路不可用**：服务器无 forgecad CLI、无 bridge 服务、本地 Qwen 推理服务未启动；`FORGECAD_BRIDGE_BASE_URL` 未配置，`FORGECAD_QWEN_BASE_URL` 默认指向不存在的 `127.0.0.1:55904` → 3D 打样/STEP 图两个步骤实际必然失败。
4. **2D 平面图定位缺失**：现有 `generate_drawing` 在 image 配置时走外部模型生成「设计图」（真实渲染风格），未配置时走本地工程图（SVG/PDF/DXF）；两个产物都不是「线稿图」。用户期望的 2D 平面图是 **AI 生成的白底黑线线稿图**（类似工业设计线稿/技术草图），与真实渲染风格的设计图区分开。

### 1.2 已验证结论（2026-07-31 服务器实测）

在 ascend-001 的 `cocreation-backend` 容器（Debian 13 + Python 3.12.13 + aarch64）上完成 POC：

| 验证项 | 结果 |
|---|---|
| `pip install build123d`（0.11.1 + cadquery-ocp-novtk 7.9.3.1.1） | ✅ 有 cp312-manylinux-aarch64 wheel，安装成功 |
| 补充系统依赖 | ✅ 需 `libgl1`（OCP 链接依赖），装后正常 |
| 建模 + 导出 STEP/STL | ✅ 体积/包围盒回读正确 |
| 导出 GLB/glTF | ✅ export_gltf(..., binary=True) 产出 GLB |
| LLM 全链路（DashScope qwen-plus 生成代码 → 子进程执行 → 导出） | ✅ 4 案例全通过（安装底座、行星齿轮、法兰、电子外壳），3/4 首轮成功，失败重试可修复 |
| 多视角渲染 + 配色（pyrender 0.1.45 + trimesh + OSMesa 软渲染） | ✅ 齿轮 STL 4 视角 × 4 配色 8 张全成功（900x700）；材质方案须用 vertex colors（uint8），MetallicRoughnessMaterial 在 OSMesa 下渲染全白 |
| 渲染依赖组合（aarch64） | ✅ `pyrender trimesh` + `PyOpenGL==3.1.7`（3.1.0 有 osmesa bug）+ apt `libosmesa6 libxrender1 libx11-6 libxext6 libxi6`，`PYOPENGL_PLATFORM=osmesa` 环境变量 |
| HLR 线图投影（Drawing/ExportSVG/ExportDXF） | ⚠️ API 存在，`add_shape(drawing)` 报 `'Drawing' object is not iterable`，正确用法待落地时解决（备用方案：`build123d.exporters.ExportSVG().add_shape(drawing.edges())` 或降级 `boundary_edges` 投影） |

**LLM 提示词工程结论**（决定生成质量的关键）：
- few-shot 示例（1 个完整可运行代码）能显著提升首轮成功率；
- 必须显式禁用易错 API：`.translate()` / `.rotate()`（应使用 `Pos/Rot * obj`）、`sort_by_length()`（应用 `filter_by(lambda e: ...)`）、`outer_bound()`（应为 `outer_wire()`）、`Length` 单词过滤器；
- 需要 `from math import *` 提示（LLM 常忘导入三角函数）；
- 错误反馈重试 2-3 轮可兜底大部分失败。

---

## 二、总体设计：7 步扁平工作台

### 2.1 步骤模型

> 产物精简定位（对照工业界完整图纸清单，本平台按现有能力精简，不做全套工程图纸）：

| 步骤 | 产物 | 工业定位 |
|---|---|---|
| 参考图/文字 | 输入素材（图片/语音/文字） | 概念输入，非图纸 |
| 2D 平面图 | CAD 线图（SVG + DXF，白底黑线三视图/轴测线稿） | 布局草图 + 简化三视图线图（不标注尺寸公差） |
| 设计图 | AI 渲染效果图 | 效果图/渲染图（外观配色） |
| 精修图 | 商业精修产品图 | 营销视觉物料 |
| 场景融合图 | 产品场景合成图 | 营销视觉物料 |
| 3D 打样 | 3D 数模（STEP + STL + GLB）+ 多视角渲染图 + 配色方案 | 3D 数模，可打样验证；渲染图与数模几何一致 |
| STEP 图 | STEP 文件 | 代工起步标配（STP 3D 数模） |

明确不做的（超出当前范围，列入后续迭代候选）：总装图/BOM、零件工程图 GD&T 公差标注、钣金展开图/排样图、工艺流程图、工装夹具图、模具结构图、电气原理图、Gerber、检验标准图、CPK、PMI 三维标注、拔模斜度分析图。

```ts
export type WorkspaceStepId =
  | 'reference'    // 参考图/文字
  | 'plan2d'       // 2D 平面图
  | 'designImage'  // 设计图
  | 'refineImage'  // 精修图
  | 'fusionImage'  // 场景融合图
  | 'model3d'      // 3D 打样
  | 'stepFile';    // STEP 图
```

- **参考图/文字**：不触发生成，对应输入区与参考资产管理（上传/描述/模型选择）。
- **2D 平面图**：**CAD 线图**（新选项 `generatePlanLine`）——LLM 生成 build123d 代码 → 子进程执行建模 → `Drawing`（HLR 隐藏线消除）投影 → 导出 **SVG + DXF** 线稿（白底黑线，正等轴测 + 可选三视图），几何与 3D 模型严格一致，无需依赖图片模型。
- **设计图**：外部图片模型（NodAPI renderPng / DashScope / Gemini），真实渲染风格（现有 `generateDrawing` 行为不变）。
- **精修图**：`generateRender + enhanceImage`（image2-edit）。
- **场景融合图**：`generateRender`（不 enhance）。
- **3D 打样**：build123d 生成 + STL/GLB 预览 + **多视角渲染图与配色方案**（新选项 `generateRenderViews`）——从同一 3D 数模用 pyrender/OSMesa 软件渲染：正等轴测 + 主视/侧视/俯视 4 视角，金属蓝/阳极黑/拉丝铝/哑光白 4 套配色（顶点着色），几何与数模严格一致。
- **STEP 图**：build123d 生成 + STEP 导出。

步骤与场景的视觉分组：

| 场景 | 步骤 |
|---|---|
| 设计 | 参考图/文字 → 2D 平面图 → 设计图 |
| 宣发 | 精修图 → 场景融合图 |
| 生产 | 3D 打样 → STEP 图 |

分组仅用于顶部导航的视觉着色与标题展示，**不构成流程约束**：任意步骤可独立触发、任意已执行步骤可跳转预览、步骤之间无顺序依赖（执行某步时自动带上当前项目上下文）。

### 2.2 步骤状态机

每步：`idle → running → completed | failed`，重跑时 `completed → running`。

```ts
interface StepState {
  status: 'idle' | 'running' | 'completed' | 'failed';
  taskId?: string;
  outputs?: Record<string, unknown>;
  error?: string;
  startedAt?: string;
  completedAt?: string;
}
```

- 某步 running 时，其它步骤可继续操作（不互斥），但同一步不允许并发（再次点击忽略或提示）；
- 步骤完成后产物持久化到版本快照；重新生成会新建版本条目（沿用现有 `buildCadAiVersionSnapshot` 机制）。

### 2.3 UI 布局

顶部横向步骤条（7 个节点 + 场景分组着色 + 连接线）：
- **idle**：灰白，可点击 → 触发生成；
- **running**：蓝色 + spinner + 当前进度文案（轮询 `getWorkflow`）；
- **completed**：绿色对勾，可点击 → 切到该步骤预览；
- **failed**：红色叹号 + 错误摘要，可点击 → 重新生成。

主体区域不变：左侧主预览（按步骤类型选择渲染器：图片 / SVG 工程图 / ThreeMeshPreview STL / STEP 信息卡），右侧为输入、参数、资产、动作面板。参考图/文字步骤始终可访问（点击时切回输入区）。

### 2.4 与现有状态兼容

- 保留 `activeScenario` / `activeStepIndex` 内部状态，但 `activeStepIndex` 改为全局 7 步索引，不再随场景切换重置；`scenarioTabs` 顶部 tab 可移除或降级为分组标题（设计文档定案：**移除 tab，改为场景分组色块**）；
- `persistWorkspace` 的持久化字段沿用（activeScenario + activeStepIndex 全局化后仍可存）；
- 历史项目（旧三场景快照）读取兼容：按快照中 outputs 的 drawingId/designImageId/renderImageId/modelStlId 等字段映射回 7 步状态。

---

## 三、后端设计

### 3.1 新增：build123d 生成服务（`cad_build123d_service.py`）

服务职责（对标并替换 ForgeCadService 的执行层）：

```
请求（设计需求文本 + 参考资产 + 导出格式）
  → 组装 LLM prompt（system few-shot + 需求 + 参考描述）
  → 调 LLM 兼容通道（DashScope qwen-plus / 本地 Qwen，可配置）
  → 提取代码（剥 markdown 围栏）
  → 子进程执行脚本（超时 300s，工作目录临时目录）
  → 失败则把 stderr 反馈回 LLM 重试（最多 3 轮，温度逐轮降低）
  → 成功则 import 模块取 result 对象
  → 导出 STEP + STL（+ GLB 可选），资产入库（kind: cad / preview）
  → 返回与 ForgeCadGenerateResult 兼容的结果（taskId/status/script/outputAssetId/downloadUrl/snapshot）
```

关键实现点：

1. **prompt 模板**（`CAD_BUILD123D_SYSTEM_PROMPT`，内置常量）：
   - 内容即 POC 验证过的 system prompt：API 白名单、禁用 `.translate()/.rotate()/.sort_by_length()/outer_bound()`、`from math import *`、`result = ...` 结尾约定、1 个 few-shot 完整示例；
   - 需求描述注入：设计文本 + 参考图/资产文件名与解析信息（不传图片二进制，避免 token 爆炸）。
2. **执行沙箱**：`subprocess.run([sys.executable, script], timeout=300)`；脚本写在任务临时目录；执行环境变量限制（不继承敏感 env）；stderr 截断 3000 字符反馈重试。
3. **导出**：`export_step` + `export_stl`（+ `export_gltf(binary=True)` 供 3D 预览），文件名 `cad123d_{task_id}.{ext}`，`content_type` 复用 `_export_content_type` 映射。
4. **资产与快照**：复用 `AssetBlobService.store_bytes` 与 `ForgeCadVersionSnapshot` 结构（script/step/stl/glb 都入库），前端 `buildCadAiVersionSnapshot`/`buildGeneratedAssets` 无需大改。
5. **错误语义**：LLM 空响应 / 脚本空 / 执行超时 / 重试耗尽 → 各自可读错误码（如 `CAD123D_SCRIPT_EMPTY`、`CAD123D_EXEC_FAILED`），写进 diagnostics。

### 3.2 新增：`IndustrialDesignWorkflowOptions` 字段

```python
# 新增（向后兼容，默认值保持旧行为）
generate_plan_line: bool = Field(default=False, alias="generatePlanLine")     # 2D 平面图：CAD 线图（SVG+DXF）
generate_render_views: bool = Field(default=False, alias="generateRenderViews")  # 3D 多视角渲染图 + 配色方案
cad_provider: str | None = Field(default=None, alias="cadProvider")           # "forgecad" | "build123d"，null=按环境
```

- **2D 平面图（CAD 线图）**：`generatePlanLine=true` 时复用 build123d 代码生成执行链路（与 3D 共用引擎），建模完成后投影导出 **SVG + DXF 线稿**，产物落 `planLineSvgAssetId` / `planLineDxfAssetId` / `planLine`（SVG 预览 URL）；与 `generateDrawing`（设计图，真实渲染）互不干扰、可同时提交。
  - 投影实现（HLR 隐藏线消除）：`build123d.exporters.Drawing(part, look_from=..., look_up=..., with_hidden=False)` 投影边集，再经 `ExportSVG`/`ExportDXF` 写出；默认输出正等轴测线稿，可选三视图（front/top/side 三个 look_from 各出一张）；`add_shape` 传边集（edges）而非 Drawing 对象（0.11.1 实测 `'Drawing' object is not iterable`，落地时以 `drawing.edges()` 或等价方式处理）；
  - `generatePlanLine` 也加入 `_external_chain_configured` 的外部链判定（依赖 build123d 可用性，与 generate_cad 同路径）。
- **3D 多视角渲染 + 配色（`generateRenderViews`）**：build123d 建模成功后，把导出的 STL 交给渲染管线（`pyrender` OffscreenRenderer + OSMesa 软渲染，`PYOPENGL_PLATFORM=osmesa`）：
  - 视角：`iso / front / side / top` 4 个 look_at 相机（按模型包围盒自适应距离与 fov）；
  - 配色：`金属蓝 / 阳极黑 / 拉丝铝 / 哑光白` 4 套（vertex colors uint8 顶点着色，禁 MetallicRoughnessMaterial——OSMesa 下渲染全白）；
  - 产物：每视角一张 PNG（900×700）入库，`renderViews` 数组（`[{key, label, assetId, url}]`）+ `renderViewsPreview`（iso×金属蓝 首图 URL）；
  - 渲染失败不阻断 3D 打样主产物（warning 诊断，降级仅出数模）。
- **本地工程图链**（SVG/PDF/DXF，`drawing_service`）：保留不动，作为图片模型未配置时的兜底路径（`image_configured()` 为 False 且请求 drawing 类选项时仍走 `_create_local_workflow`）。
- **3D**：`cad_provider="build123d"` 时，`generate_cad` / `generate_three_preview` 的 ForgeCAD 分支替换为 build123d 服务；环境默认：若 `FORGECAD_BRIDGE_BASE_URL`/`FORGECAD_QWEN_BASE_URL` 均未配置且 build123d 可导入 → 自动回退 build123d。

### 3.3 工作流编排改动（`industrial_design_workflow_service.py`）

- `_external_chain_configured`：新增 `request.options.generate_plan_line` 分支（CAD 线图属 build123d 链路，与 generate_cad 同判定）；
- `_execute_external_workflow`：`generatePlanLine` 分支与 `generate_cad` 分支并列，独立生成、独立 outputs 字段（`planLineSvgAssetId`/`planLineDxfAssetId`/`planLine`/`planLineTaskId`）；
- `_create_local_workflow`：generate_cad 分支新增 build123d 路径（当 provider 为 build123d 时）；
- 3D 产物映射进 outputs：`modelStepAssetId` / `modelStlAssetId` / `modelGlbAssetId` / `modelScriptAssetId` / `modelDownloadUrl`（沿用现有字段名，前端 helpers 已支持）；`generateRenderViews` 的渲染产物进 `renderViews` / `renderViewsPreview`；
- 必要资产校验 `_missing_required_asset_outputs`：加入 `(generatePlanLine, "planLineSvgAssetId", "2D 线图")`（渲染图不列入必选，失败降级）。

### 3.4 部署改动

`cocreation-platform/deploy/Dockerfile`（backend）：

```dockerfile
RUN apt-get update && apt-get install -y --no-install-recommends \
    libgl1 fontconfig libosmesa6 libxrender1 libx11-6 libxext6 libxi6 \
    && rm -rf /var/lib/apt/lists/*
RUN pip install build123d pyrender trimesh "PyOpenGL==3.1.7"
```

compose `.env` 增加（可缺省）：
```
CAD_PROVIDER=build123d
```

LLM 通道配置（`cad_build123d_service.py` 独立于 ForgeCAD 链路，OpenAI 兼容）：
```
BUILD123D_LLM_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1   # 默认云端，可切本地 vLLM
BUILD123D_LLM_API_KEY=<DASHSCOPE key>
BUILD123D_LLM_MODEL=qwen-plus                                               # 本地可切 qwen3-32b
BUILD123D_LLM_TIMEOUT_SECONDS=180
BUILD123D_EXEC_TIMEOUT_SECONDS=300
BUILD123D_MAX_RETRIES=3
```

### 3.5 顺带修复：版本快照 generated_assets 为空

后端真实图片链路（NodAPI renderPng）产物已入库（asset b81219f9-... 等），但 `versionSnapshot.assets` 的 `assetId` 与任务 outputs 的 `designImageAssetId` 等字段对不上，导致前端 `generated_assets`/`generated_image_urls` 空数组。修复点：`buildCadAiVersionSnapshot` 中按 `versionSnapshot.assets[].id` 与 outputs 各 id 字段（designImageAssetId/renderImageAssetId 等）做双向匹配补充 url（前端仅展示层修复，不动后端协议）。

---

## 四、接口协议

前端 → 后端（`POST /api/v1/cad-ai/industrial-design/workflow`）：

```jsonc
{
  "options": {
    "generatePlanLine": true,         // 新增：2D 平面图（CAD 线图）
    "generateDrawing": true,          // 设计图（真实渲染，现有）
    "generateRender": true,
    "enhanceImage": true,
    "generateCad": true,              // 3D 打样 / STEP 图
    "generateThreePreview": true,
    "generateRenderViews": true,      // 新增：3D 多视角渲染图 + 配色方案
    "cadProvider": "build123d"        // 新增：3D 走 build123d
  }
}
```

后端响应 outputs 新增（2D 线图 + build123d 路径 + 渲染视图）：

```jsonc
{
  "planLineSvgAssetId": "...",     // 2D 线图 SVG 资产
  "planLineDxfAssetId": "...",     // 2D 线图 DXF 资产
  "planLine": "...",               // 2D 线图 SVG 预览 URL
  "modelScriptAssetId": "...",     // 生成的 build123d 代码资产
  "modelStepAssetId": "...",       // STEP 导出
  "modelStlAssetId": "...",        // STL 导出（3D 预览）
  "modelGlbAssetId": "...",        // GLB 导出（可选）
  "modelDownloadUrl": "...",
  "renderViews": [                 // 多视角渲染图（iso/front/side/top × 4 配色）
    { "key": "iso_metal_blue", "label": "正等轴测·金属蓝", "assetId": "...", "url": "..." }
  ],
  "renderViewsPreview": "..."      // 首图（iso×金属蓝）预览 URL
}
```

后端 → 前端：轮询接口、版本快照结构不变（复用 ForgeCadGenerateResult 兼容结构）。

---

## 四·五、演进方向（与 GPT 方案对齐后的定位与后续阶段）

> **产品定位**：不单纯做图片/CAD 生成器，而是「AI 工业设计工程师」——输入一句需求/一张参考图，产出可交付的**工程设计包**（设计说明 + 图纸 + 3D 数模 + BOM + 审查报告）。

### 4.5.1 工程设计包（阶段①，当前优先实施）

7 步产物已各自生成，缺的是「打包成可交付工程包」。新增打包能力，最终交付：

```
项目文件夹.zip
├── 设计说明.pdf          # 项目信息 + 需求描述 + 产物清单 + 关键参数
├── 方案图.svg            # 2D 平面图（HLR 线稿）
├── 工程图.dxf            # 2D 工程图（HLR 线稿）
├── 设计图.png            # AI 渲染效果图（如已生成）
├── 精修图.png            # 商业精修图（如已生成）
├── 场景融合图.png        # 场景合成图（如已生成）
├── 三维模型.step         # STEP 数模
├── 三维模型.stl          # STL 网格
├── 三维预览.glb          # GLB 预览
├── 渲染图/               # 多视角 × 配色渲染 PNG
├── BOM.xlsx              # 物料清单（单件：材料/体积/重量/工艺）
└── 设计审查报告.pdf      # DFM 规则检查 + LLM 审查结论
```

- 后端新增 `engineering_package_service.py`：按 taskId 拉取 outputs 关联的资产 → 组装 zip → 入库（kind: archive）→ 返回下载 URL；PDF 用 reportlab（配置系统中文字体），XLSX 用 openpyxl；
- 前端 7 步条最右侧加「导出工程包」按钮（任务 completed 且有模型资产时可用）。

### 4.5.2 设计审查 Agent（阶段②）

`design_review_service.py`：几何规则检查 + LLM 审查报告。

- **几何规则检查**（确定性代码，build123d 分析数模）：
  - 最小壁厚（薄壁告警，`< 0.8mm`）
  - 最小孔间距/孔边距（`< 1.5× 孔径` 告警）
  - 最小圆角半径（`< 0.2mm` 告警）
  - 干涉检查（布尔自交/退化面）
- **LLM 审查报告**：把几何检查结果 + 设计描述 + 材料猜测交给 LLM，生成中文审查报告（PDF）：结构合理度、可制造性、尺寸建议、风险清单；
- 审查结果入库，前端可在 STEP 步骤下看到审查结论卡片。

### 4.5.3 知识库接入（阶段③）

复用产业共享平台（V8）知识体系，**独立向量库 + 数据同步**：

- V8 已有数据：`knowledge_sources`(551) + `knowledge_chunks`(549) + `my_space_knowledge_documents`(600)，Milvus/Neo4j 未部署；
- cocreation-platform 侧部署 **Milvus**（独立），定时从 V8 MySQL 同步知识切片 → DashScope text-embedding-v4 向量化入库；
- `build123d` 生成代码与审查报告时，检索相关工业知识（材料参数/工艺约束/相似案例）注入 prompt，提升生成质量；
- 阶段③不阻塞 ①②。

### 4.5.4 Agent 编排框架（阶段④，远期）

- **LangGraph 为主**：将 `industrial_design_workflow_service` 的单体编排重构为显式状态图（需求理解 → 规划 → 各步 Agent → 审查 → 打包），流程可控、可观测；
- **CrewAI 补充**：复杂/开放任务（如外观多方案发散）用 CrewAI 多 Agent 协作；
- 引入时机：功能稳定后、Agent 数量增多时，避免过早重构。

---

## 五、实施顺序（里程碑）

> 状态标注：✅ 已完成（2026-07-31 服务器实测）| ⏳ 待做

1. ✅ **后端**：`cad_build123d_service.py`（建模+导出+线图+渲染）+ options 字段（generatePlanLine/cadProvider/generateRenderViews）+ 编排接入 + Dockerfile（apt+pip 持久化）→ 服务器部署验证真实 STEP/STL/线稿图/渲染图产出；
2. ✅ **前端 7 步扁平重构**：constants（scenarioConfigs 拆分 + stepMeta）、状态机（stepStates）、顶部步骤条 UI、单步触发（submitIndustrialDesignWorkflow 按 step 组装 options）、预览切换、快照兼容修复（含 generated_assets 空数组问题）；
3. ✅ **联调回归**：全 7 步逐个验证（nginx 44308 全链路）+ 历史版本兼容；
4. ✅ **工程设计包**：`engineering_package_service.py`（zip + PDF + XLSX）+ 前端导出按钮 → 服务器验证可交付工程包（法兰盘 659KB zip，含设计说明/BOM/图纸/数模/16渲染图）；
5. ✅ **设计审查 Agent**：`design_review_service.py`（几何规则检查 + LLM 审查报告）→ 前端审查卡片；审查质量实测优秀（识别孔径歧义、壁厚净距不足、多实体问题）；
6. ✅ **知识库接入**：Milvus standalone 部署（etcd+minio+milvus）+ V8 数据同步（549 切片向量化入库）+ 语义检索 API + RAG 注入 build123d/审查 prompt；
7. ✅ **Agent 编排**：LangGraph 工业设计总师（需求理解→build123d CAD→设计审查 异步节点图），`POST /agent/industrial-design` 验证通过；CrewAI 补充待后续。

---

## 五·五、落地记录（2026-07-31 实测）

### 部署拓扑（ascend-001）
- **cocreation-platform**：backend(8000) + frontend(nginx 44308) + postgres + milvus(19530) + etcd + minio，`deploy_default` 网络；
- **知识库**：Milvus standalone（`/opt/milvus/docker-compose.yml`），backend 已连入 `milvus_default` 与 `cygxpt-docker_default`（同步 V8 MySQL）；
- **镜像固化**：backend 用 `docker commit` 固化（含 build123d/pyrender/reportlab/openpyxl/pymysql/pymilvus/fonts-wqy-zenhei）；Dockerfile 保留慢构建（apt 层可后续分层优化）。

### 关键环境变量（backend）
```
CAD_PROVIDER=build123d
BUILD123D_LLM_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
BUILD123D_LLM_MODEL=qwen-plus
KNOWLEDGE_MILVUS_URI=http://milvus:19530
KNOWLEDGE_SYNC_SOURCE_URL=cygxpt-mysql:3306
KNOWLEDGE_SYNC_SOURCE_DB=cygxszpt
KNOWLEDGE_SYNC_SOURCE_USER=root
KNOWLEDGE_SYNC_SOURCE_PASSWORD=cygxpt_root_password_2404
```

### 新增 API（全部走 nginx /api/v1/industrial-design/）
| 端点 | 说明 |
|---|---|
| `POST /workflows` | 7 步统一工作流（原） |
| `POST /workflows/{id}/engineering-package` | 导出工程设计包 zip |
| `POST /workflows/{id}/design-review` | 设计审查报告 PDF |
| `POST /agent/industrial-design` | LangGraph 总师编排 |
| `GET /knowledge/health` | 知识库健康检查 |
| `GET /knowledge/search?q=` | 语义检索 |
| `POST /knowledge/sync` | V8 数据同步 |

### 踩坑记录
1. FastAPI `Depends(get_db)` 不自动 commit → 写入端点需显式 `db.commit()`；
2. DashScope embedding batch ≤10；
3. Milvus standalone 需配置 `MINIO_ACCESS_KEY_ID`/`MINIO_SECRET_ACCESS_KEY`（默认 minioadmin 不匹配自定义 minio）；
4. LangGraph 节点内 `asyncio.run` 与 uvicorn uvloop 冲突 → 用 async 节点 + `ainvoke`；
5. 编排中 staged 资产读取：审查服务 `_read_asset` 用 `_get_owned_asset(require_available=False)`；
6. docker cp 同步陷阱：本地改完必须 rsync 到服务器再 cp 进容器；
7. reportlab 中文需注册 TTF 字体（fonts-wqy-zenhei 路径 `/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc`）。

---

## 六、风险与兼容性

| 风险 | 对策 |
|---|---|
| LLM 生成 build123d 代码成功率波动 | few-shot + API 禁用清单 + 3 轮错误重试；首轮成功率实测 75%，重试后 100% |
| 子进程执行安全性 | 临时目录隔离、环境变量裁剪、超时 300s、输出截断 |
| build123d 首次 import 慢（OCCT 加载） | 服务启动时预热导入（模块级 import），任务内子进程冷启动可接受 |
| OSMesa 软渲染性能 | 900×700 单帧毫秒级，4 视角×4 配色共 16 帧 <10s；渲染失败降级不阻断主产物 |
| pyrender/PyOpenGL 版本兼容 | 锁 PyOpenGL==3.1.7 + osmesa 平台 + vertex colors 着色（禁用 MetallicRoughnessMaterial），已实测 |
| 本地 Qwen 不可用 | build123d LLM 通道默认 DashScope 云端，`BUILD123D_LLM_*` 可切用户侧昇腾 vLLM（Qwen3-32B，58080） |
| HLR 线图 API 用法未完全验证 | 以 edges 集合方式传入 ExportSVG/ExportDXF（备用：降级三视图边界边投影）；落地时在服务器实测 |
| 历史版本数据兼容 | 前端按快照 outputs 字段映射回 7 步；旧字段全部保留 |
| ForgeCAD 未来恢复 | cadProvider 开关保留双路径，默认按环境自动选择 |
| Fontconfig 警告噪音 | Dockerfile 装 fontconfig，消除 stderr 干扰（该行会混入重试反馈，已确认不影响功能） |
