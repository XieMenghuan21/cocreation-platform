"""ForgeCAD 建模接口 Schema。"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

ForgeCadExportFormat = Literal["none", "step", "stl", "brep"]
ForgeCadTaskStatus = Literal["script_generated", "completed"]
ForgeCadSnapshotAction = Literal["create", "structure", "appearance", "derive", "concept"]


class ForgeCadImportAsset(BaseModel):
    """用户导入的 CAD 或图纸参考文件。"""

    model_config = ConfigDict(populate_by_name=True)

    asset_id: str = Field(alias="assetId")
    filename: str
    extension: str
    content_type: str = Field(alias="contentType")
    size_bytes: int = Field(alias="sizeBytes")
    storage_path: None = Field(default=None, alias="storagePath")
    created_at: str = Field(alias="createdAt")
    parse_status: str = Field(alias="parseStatus")
    parse_message: str = Field(alias="parseMessage")
    parse_features: list["ForgeCadImportFeature"] = Field(default_factory=list, alias="parseFeatures")
    preview_kind: str = Field(default="none", alias="previewKind")
    preview_asset_id: str | None = Field(default=None, alias="previewAssetId")
    preview_asset_path: None = Field(default=None, alias="previewAssetPath")
    preview_asset_format: str | None = Field(default=None, alias="previewAssetFormat")
    preview_asset_url: str | None = Field(default=None, alias="previewAssetUrl")
    conversion_status: str | None = Field(default=None, alias="conversionStatus")
    conversion_message: str | None = Field(default=None, alias="conversionMessage")
    preview_entities: list["ForgeCadPreviewEntity"] = Field(default_factory=list, alias="previewEntities")
    bom_items: list["ForgeCadBomItem"] = Field(default_factory=list, alias="bomItems")
    explosion_steps: list["ForgeCadExplosionStep"] = Field(default_factory=list, alias="explosionSteps")


class ForgeCadImportFeature(BaseModel):
    """导入文件轻量解析出的结构特征。"""

    label: str
    value: str


class ForgeCadPreviewEntity(BaseModel):
    """CAD 在线预览使用的轻量几何实体。"""

    entity_type: str = Field(alias="entityType")
    points: list[list[float]] = Field(default_factory=list)
    center: list[float] | None = None
    radius: float | None = None
    start_angle: float | None = Field(default=None, alias="startAngle")
    end_angle: float | None = Field(default=None, alias="endAngle")


class ForgeCadBomItem(BaseModel):
    """从导入或生成结果中提取的轻量 BOM 项。"""

    name: str
    material: str | None = None
    quantity: int = 1
    size: str | None = None
    source: str


class ForgeCadExplosionStep(BaseModel):
    """爆炸图展示步骤。"""

    step: int
    name: str
    offset: list[float] = Field(default_factory=list)
    description: str


class ForgeCadModelObject(BaseModel):
    """ForgeCAD CLI 日志中返回的模型对象。"""

    name: str
    volume: str | None = None
    bbox: str | None = None
    geometry: str | None = None


class ForgeCadParameter(BaseModel):
    """ForgeCAD 脚本或 CLI 日志中返回的参数。"""

    name: str
    default_value: str | None = Field(default=None, alias="defaultValue")


class ForgeCadGeneratedAsset(BaseModel):
    """ForgeCAD 任务生成的真实资产。"""

    name: str
    asset_id: str | None = Field(default=None, alias="assetId")
    asset_type: str = Field(alias="assetType")
    path: None = None
    download_url: str | None = Field(default=None, alias="downloadUrl")
    status: str


class ForgeCadDiagnostic(BaseModel):
    """基于 ForgeCAD 返回结果推导的工作台诊断。"""

    level: Literal["info", "warning", "error"]
    title: str
    detail: str


class ForgeCadVersionSnapshot(BaseModel):
    """面向工业设计工作台展示的版本快照信息。"""

    model_config = ConfigDict(populate_by_name=True)

    task_id: str = Field(alias="taskId")
    change_type: str = Field(alias="changeType")
    source_object: str = Field(alias="sourceObject")
    script_asset_id: str | None = Field(default=None, alias="scriptAssetId")
    output_asset_id: str | None = Field(default=None, alias="outputAssetId")
    script_path: None = Field(default=None, alias="scriptPath")
    work_dir: None = Field(default=None, alias="workDir")
    output_path: None = Field(default=None, alias="outputPath")
    download_url: str | None = Field(default=None, alias="downloadUrl")
    execution_summary: str = Field(alias="executionSummary")
    created_at: str = Field(alias="createdAt")
    status_label: str = Field(alias="statusLabel")
    cli_executed: bool = Field(alias="cliExecuted")
    export_format: ForgeCadExportFormat = Field(alias="exportFormat")
    model_objects: list[ForgeCadModelObject] = Field(default_factory=list, alias="modelObjects")
    parameters: list[ForgeCadParameter] = Field(default_factory=list)
    generated_assets: list[ForgeCadGeneratedAsset] = Field(default_factory=list, alias="generatedAssets")
    diagnostics: list[ForgeCadDiagnostic] = Field(default_factory=list)


class ForgeCadGenerateRequest(BaseModel):
    """AI 生成 ForgeCAD 脚本请求。"""

    model_config = ConfigDict(populate_by_name=True)

    prompt: str = Field(..., min_length=4, max_length=8000, description="自然语言建模需求")
    export_format: ForgeCadExportFormat = Field(
        default="none",
        alias="exportFormat",
        description="预留导出格式；none 表示仅生成并校验脚本",
    )
    run_cli: bool = Field(
        default=False,
        alias="runCli",
        description="是否执行本机 ForgeCAD CLI；默认只生成脚本，避免依赖运行环境",
    )
    temperature: float = Field(default=0.2, ge=0, le=1.5, description="Qwen3 生成温度")
    max_tokens: int = Field(default=2400, ge=256, le=12000, alias="maxTokens")
    action: ForgeCadSnapshotAction = Field(
        default="create",
        description="工作台提交入口，用于生成版本快照的修改类型",
    )
    source_object: str = Field(
        default="当前设计项目",
        alias="sourceObject",
        max_length=240,
        description="本次生成或修改的来源对象",
    )


class ForgeCadGenerateResult(BaseModel):
    """AI 生成 ForgeCAD 脚本结果。"""

    model_config = ConfigDict(populate_by_name=True)

    task_id: str = Field(alias="taskId")
    status: ForgeCadTaskStatus
    script: str
    script_asset_id: str | None = Field(default=None, alias="scriptAssetId")
    output_asset_id: str | None = Field(default=None, alias="outputAssetId")
    script_path: None = Field(default=None, alias="scriptPath")
    work_dir: None = Field(default=None, alias="workDir")
    output_path: None = Field(default=None, alias="outputPath")
    download_url: str | None = Field(default=None, alias="downloadUrl")
    logs: str
    cli_executed: bool = Field(alias="cliExecuted")
    export_format: ForgeCadExportFormat = Field(alias="exportFormat")
    snapshot: ForgeCadVersionSnapshot | None = None
    model_objects: list[ForgeCadModelObject] = Field(default_factory=list, alias="modelObjects")
    parameters: list[ForgeCadParameter] = Field(default_factory=list)
    generated_assets: list[ForgeCadGeneratedAsset] = Field(default_factory=list, alias="generatedAssets")
    diagnostics: list[ForgeCadDiagnostic] = Field(default_factory=list)


class ForgeCadErrorDetail(BaseModel):
    """ForgeCAD 错误详情。"""

    task_id: str | None = Field(default=None, alias="taskId")
    stdout: str | None = None
    stderr: str | None = None
    script_path: str | None = Field(default=None, alias="scriptPath")
