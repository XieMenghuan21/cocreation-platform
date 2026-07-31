"""API v1 路由注册"""
from fastapi import APIRouter

from app.api.v1.aggregation_workbench import router as aggregation_workbench_router
from app.api.v1.assets import router as assets_router
from app.api.v1.auth import router as auth_router
from app.api.v1.cocreation_history import router as cocreation_history_router
from app.api.v1.forgecad import router as forgecad_router
from app.api.v1.industrial_design import router as industrial_design_router
from app.api.v1.workspace import router as workspace_router

router = APIRouter()
router.include_router(auth_router, tags=["认证与SSO"])
router.include_router(cocreation_history_router, tags=["共创历史"])
router.include_router(aggregation_workbench_router)
router.include_router(forgecad_router, tags=["ForgeCAD 共创智能体"])
router.include_router(industrial_design_router, tags=["工业品设计"])
router.include_router(workspace_router, tags=["工作区"])
router.include_router(assets_router, tags=["资产"])
