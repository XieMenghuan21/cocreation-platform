"""共创智能体独立平台 FastAPI 启动入口"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI

from app.config import settings
from app.core.exceptions import setup_exception_handlers
from app.core.middleware import setup_middleware
from app.api.v1.router import router as api_router
from app.services.industrial_design_workflow_service import industrial_design_workflow_service

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期管理"""
    _ = app
    await industrial_design_workflow_service.recover_pending_workflows()
    try:
        yield
    finally:
        await industrial_design_workflow_service.shutdown()


# 配置日志
logging.basicConfig(level=settings.LOG_LEVEL, format=settings.LOG_FORMAT)

# 确保上传目录存在
upload_root = Path(settings.UPLOAD_PATH)
upload_root.mkdir(parents=True, exist_ok=True)

# 创建 FastAPI 应用
app = FastAPI(
    title=settings.PROJECT_NAME,
    version=settings.VERSION,
    description="共创智能体独立平台后端 API 服务",
    docs_url=f"{settings.API_V1_PREFIX}/docs",
    redoc_url=f"{settings.API_V1_PREFIX}/redoc",
    openapi_url=f"{settings.API_V1_PREFIX}/openapi.json",
    lifespan=lifespan,
)

# 设置中间件
setup_middleware(app)

# 设置全局异常处理
setup_exception_handlers(app)

# 注册 API 路由
app.include_router(api_router, prefix=settings.API_V1_PREFIX)


@app.get("/")
async def root():
    return {
        "message": "欢迎使用共创智能体平台 API",
        "version": settings.VERSION,
        "docs": f"{settings.API_V1_PREFIX}/docs",
    }


@app.get("/ping")
async def ping():
    return {"pong": True}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        app,
        host=settings.HOST,
        port=settings.PORT,
        reload=settings.DEBUG,
    )
