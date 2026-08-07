# AI CoDesign Studio — 阶段一（基础 MVP）实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 搭建「工业设计智能体平台」的 Monorepo 骨架并跑通第一条闭环链路：用户登录 → 创建项目 → 上传需求文字/图片 → AI 提取需求与场景识别 → 生成多套（A/B/C）设计方案 → 保存项目 → 修改方案（版本分支）。

**Architecture:** 全新 Monorepo（frontend/ + backend/ + deploy/），前端 Next.js 15 App Router + shadcn/ui + Tailwind，后端 FastAPI + SQLAlchemy 2 + PostgreSQL，异步任务用 arq + Redis 队列，AI 能力通过统一网关抽象（OpenAI 兼容 + 本地规则引擎兜底），算力调度用资源路由表（ascend_910a / gpu_5090 抽象池）。工作流以持久化节点状态机（workflow_nodes/edges/execution_jobs）实现，支持 DRAFT→QUEUED→RUNNING→SUCCEEDED/FAILED 全状态迁移、WAITING_AUTH 人工授权、节点分支（版本分支）。用户只选择 QUICK/STANDARD/DEEP 执行程度，模型与算力细节对用户不可见。

**Tech Stack:** Next.js 15 + TypeScript + Tailwind + shadcn/ui + framer-motion；FastAPI + SQLAlchemy 2 + asyncpg + alembic + arq + redis + boto3(MinIO) + PyJWT + bcrypt；PostgreSQL 16 (pgvector/pgvector 镜像) + Redis 7 + MinIO；pytest + pytest-asyncio 后端测试，Vitest 前端测试。

**参考源码（只读参考，不复制）：**
- `/Users/pipi/CodeSpace/cocreation-platform/backend/` — 现有工作流/ComfyUI/build123d 服务
- `/Users/pipi/CodeSpace/家具智能设计与造价核算系统/backend/src/services/` — 报价规则引擎与本地生成器兜底模式

---

## 决策点（执行前需用户确认）

1. **新仓库位置**：本计划假设在 `/Users/pipi/CodeSpace/ai-codesign-studio/` 新建独立 Monorepo（git init 新仓库）。现有 cocreation-platform 保留不动，仅作参考。
2. **工作流引擎**：阶段一不用 Temporal（规格书建议但属于重基础设施），用「持久化节点状态机 + arq 队列」实现同样的节点状态语义（9 种状态、分支、失败重试、中断恢复）。Temporal 在阶段三（宣发/生产自由跳转高频期）引入，替换 arq 执行层，节点模型不变。
3. **910A/5090 抽象池**：阶段一的 ascend_910a / gpu_5090 是路由表中的抽象池。本机无昇腾/5090 硬件时全部走 OpenAI 兼容 API 或本地规则引擎兜底。MindIE 实机部署验证是阶段一最后一个工作项（Task 16），需要实际硬件环境。
4. **ComfyUI**：封装为后台渲染服务（参考现有 comfyui_image_service.py），前端只看到「渲染中/完成」状态和图片结果，不暴露节点编辑器。

---

## 计划总览（5 份独立计划）

| 计划 | 范围 | 状态 |
|------|------|------|
| **Plan 1（本文件）** | 阶段一：基础 MVP — 认证/组织/项目/资产/对话/需求提取/场景识别/方案生成/三级执行程度/内部调度/授权/版本 | 本文档 |
| Plan 2 | 阶段二：报价闭环 — 材料/工艺价格库、BOM、成本计算、双报价视图、PDF/Excel 导出、报价审批、自动重算 | 待写 |
| Plan 3 | 阶段三：宣发能力 — 场景融合、宣传图、3D 查看器、爆炸图、文案脚本、Temporal 迁移 | 待写 |
| Plan 4 | 阶段四：生产能力 — 参数化 CAD、BOM 深化、开料清单、CAD 交换、装配树、工艺路线、工程师审核、生产任务包 | 待写 |
| Plan 5 | 阶段五：企业集成 — ERP/MES/PDM 同步、SSO、审批流、供应商接口、成本利润报表 | 待写 |

**Plan 1 验收口径（规格书十四）**：用户只能选择快速/标准/深度；任务失败可重试；每套方案独立版本；付费/外部 API 必须先授权；生产文件未审核标记「AI 生成草案」（阶段一仅方案图，直接标记）。

---

## 目录结构（目标状态）

```
ai-codesign-studio/
├── deploy/
│   ├── docker-compose.yml
│   └── .env.example
├── backend/
│   ├── pyproject.toml
│   ├── app/
│   │   ├── main.py
│   │   ├── config/settings.py
│   │   ├── db/session.py
│   │   ├── models/            # org, project, asset, workflow, design
│   │   ├── schemas/           # pydantic
│   │   ├── api/v1/            # auth, projects, assets, analyze, schemes, workflows, jobs
│   │   ├── services/          # auth_service, ai_gateway, resource_scheduler,
│   │   │                      # workflow_service, executors, asset_service
│   │   └── worker.py          # arq worker
│   ├── scripts/seed.py
│   └── tests/
├── frontend/
│   ├── next.config.ts
│   ├── middleware.ts
│   ├── src/app/(auth)/login|register/page.tsx
│   ├── src/app/(app)/layout.tsx        # 侧边导航
│   ├── src/app/(app)/projects/page.tsx # 项目中心
│   ├── src/app/(app)/projects/[id]/page.tsx        # 对话工作台
│   ├── src/app/(app)/projects/[id]/schemes/page.tsx # 方案对比
│   ├── src/app/(app)/tasks/page.tsx     # 任务与审批中心
│   ├── src/app/(app)/org/page.tsx       # 组织设置
│   ├── src/lib/api.ts, types.ts
│   └── src/components/...   # chat/, workflow/, schemes/, ui/ (shadcn)
└── docs/
```

---

## Task 1: Monorepo 骨架 + docker-compose

**Files:**
- Create: `deploy/docker-compose.yml`
- Create: `deploy/.env.example`
- Create: `docs/ARCHITECTURE.md`
- Create: `README.md`
- Create: `.gitignore`

- [ ] **Step 1: 创建目录并初始化 git**

```bash
mkdir -p /Users/pipi/CodeSpace/ai-codesign-studio/{deploy,backend,frontend,docs}
cd /Users/pipi/CodeSpace/ai-codesign-studio && git init
```

- [ ] **Step 2: 写 deploy/docker-compose.yml**

```yaml
services:
  postgres:
    image: pgvector/pgvector:pg16
    environment:
      POSTGRES_USER: codesign
      POSTGRES_PASSWORD: codesign
      POSTGRES_DB: codesign
    ports: ["5432:5432"]
    volumes: [pgdata:/var/lib/postgresql/data]
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U codesign"]
      interval: 5s
      timeout: 5s
      retries: 10
  redis:
    image: redis:7-alpine
    ports: ["6379:6379"]
  minio:
    image: minio/minio
    command: server /data --console-address ":9001"
    environment:
      MINIO_ROOT_USER: minioadmin
      MINIO_ROOT_PASSWORD: minioadmin
    ports: ["9000:9000", "9001:9001"]
    volumes: [miniodata:/data]
volumes:
  pgdata:
  miniodata:
```

- [ ] **Step 3: 写 deploy/.env.example**

```bash
DATABASE_URL=postgresql+asyncpg://codesign:codesign@localhost:5432/codesign
REDIS_URL=redis://localhost:6379/0
S3_ENDPOINT=http://localhost:9000
S3_ACCESS_KEY=minioadmin
S3_SECRET_KEY=minioadmin
S3_BUCKET=assets
JWT_SECRET=change-me-in-production
AI_BASE_URL=
AI_API_KEY=
```

- [ ] **Step 4: 写 .gitignore**

```gitignore
node_modules/
.next/
__pycache__/
.venv/
dist/
*.pyc
.env
.DS_Store
```

- [ ] **Step 5: 启动基础设施并验证**

```bash
cd /Users/pipi/CodeSpace/ai-codesign-studio/deploy && docker compose up -d
docker compose ps
```

Expected: postgres/redis/minio 三个容器状态 healthy/running。

- [ ] **Step 6: 写 docs/ARCHITECTURE.md 摘要（100 行内）**

内容包含：Monorepo 结构、后端分层（api → service → model）、工作流状态机 9 状态迁移图、资源路由表（requirement/scene/concept → ascend_910a，rendering → gpu_5090）、用户侧只暴露执行程度。此文件后续 Task 每完成一个就同步更新一次。

- [ ] **Step 7: Commit**

```bash
git add -A && git commit -m "chore: monorepo 骨架与基础设施 docker-compose"
```

---

## Task 2: 后端骨架（FastAPI + 配置 + 健康检查）

**Files:**
- Create: `backend/pyproject.toml`
- Create: `backend/app/__init__.py`
- Create: `backend/app/config/__init__.py`
- Create: `backend/app/config/settings.py`
- Create: `backend/app/db/__init__.py`
- Create: `backend/app/db/session.py`
- Create: `backend/app/main.py`
- Create: `backend/app/api/__init__.py`
- Create: `backend/app/api/v1/__init__.py`
- Create: `backend/app/api/v1/router.py`
- Create: `backend/tests/__init__.py`
- Create: `backend/tests/test_health.py`

- [ ] **Step 1: 写失败的测试**

```python
# backend/tests/test_health.py
from httpx import ASGITransport, AsyncClient
from app.main import app


async def test_health():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        res = await client.get("/api/health")
    assert res.status_code == 200
    assert res.json() == {"status": "ok"}
```

- [ ] **Step 2: 写 pyproject.toml**

```toml
[project]
name = "codesign-backend"
version = "0.1.0"
requires-python = ">=3.12"
dependencies = [
  "fastapi>=0.115",
  "uvicorn[standard]>=0.30",
  "sqlalchemy[asyncio]>=2.0",
  "asyncpg>=0.29",
  "alembic>=1.13",
  "pydantic>=2.7",
  "pydantic-settings>=2.3",
  "python-multipart>=0.0.9",
  "httpx>=0.27",
  "boto3>=1.34",
  "redis>=5.0",
  "arq>=0.26",
  "pyjwt>=2.8",
  "bcrypt>=4.1",
  "aiosqlite>=0.20",
  "pgvector>=0.3",
]
[project.optional-dependencies]
dev = ["pytest>=8.0", "pytest-asyncio>=0.23", "ruff>=0.4"]

[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]

[tool.ruff]
line-length = 100
```

- [ ] **Step 3: 写 settings.py**

```python
# backend/app/config/settings.py
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    app_name: str = "codesign-backend"
    database_url: str = "postgresql+asyncpg://codesign:codesign@localhost:5432/codesign"
    redis_url: str = "redis://localhost:6379/0"
    s3_endpoint: str = "http://localhost:9000"
    s3_access_key: str = "minioadmin"
    s3_secret_key: str = "minioadmin"
    s3_bucket: str = "assets"
    jwt_secret: str = "dev-secret-change-me"
    jwt_expire_minutes: int = 60 * 24 * 7
    cors_origins: list[str] = ["http://localhost:3000"]
    ai_base_url: str = ""
    ai_api_key: str = ""
    ai_model_quick: str = "gpt-4o-mini"
    ai_model_standard: str = "gpt-4o-mini"
    ai_model_deep: str = "gpt-4o"
    enable_local_fallback: bool = True
    comfyui_endpoint: str = ""
    storage_local_dir: str = "data/assets"


settings = Settings()
```

- [ ] **Step 4: 写 db/session.py 与 main.py**

```python
# backend/app/db/session.py
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from app.config.settings import settings

engine = create_async_engine(settings.database_url, echo=False)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False)
```

```python
# backend/app/main.py
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.v1.router import api_router
from app.config.settings import settings
from app.db.base import Base
from app.db.session import engine


@asynccontextmanager
async def lifespan(app: FastAPI):
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield


app = FastAPI(title=settings.app_name, lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(api_router, prefix="/api")


@app.get("/api/health")
async def health():
    return {"status": "ok"}
```

- [ ] **Step 5: 写 db/base.py（后续所有模型的基类）**

```python
# backend/app/db/base.py
from sqlalchemy import DateTime, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from datetime import datetime


class Base(DeclarativeBase):
    pass


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
```

- [ ] **Step 6: 写 router.py（空壳，后续任务逐个挂载）**

```python
# backend/app/api/v1/router.py
from fastapi import APIRouter

api_router = APIRouter()
```

- [ ] **Step 7: 安装依赖并跑测试（预期失败：import 错误）**

```bash
cd /Users/pipi/CodeSpace/ai-codesign-studio/backend
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
pytest tests/test_health.py -v
```

Expected: FAIL — `ModuleNotFoundError: No module named 'app'`（因为 main.py 中 import 链缺文件）。补上 `backend/app/api/v1/router.py` 后重跑直到 PASS。测试用 sqlite 内存库覆盖 `database_url`：

```bash
DATABASE_URL=sqlite+aiosqlite:///:memory: pytest tests/test_health.py -v
```

- [ ] **Step 8: Commit**

```bash
git add -A && git commit -m "feat: 后端 FastAPI 骨架与健康检查"
```

---

## Task 3: 组织、用户与认证（JWT + bcrypt）

**Files:**
- Create: `backend/app/models/__init__.py`
- Create: `backend/app/models/org.py`
- Create: `backend/app/schemas/__init__.py`
- Create: `backend/app/schemas/auth.py`
- Create: `backend/app/services/__init__.py`
- Create: `backend/app/services/auth_service.py`
- Create: `backend/app/api/v1/deps.py`
- Create: `backend/app/api/v1/auth.py`
- Create: `backend/tests/test_auth.py`

- [ ] **Step 1: 写失败的测试**

```python
# backend/tests/test_auth.py
from httpx import ASGITransport, AsyncClient
from app.main import app


async def test_register_login_me():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        res = await client.post("/api/auth/register", json={
            "email": "a@b.com", "password": "secret123", "display_name": "Alice",
        })
        assert res.status_code == 200
        user = res.json()
        assert user["email"] == "a@b.com"
        assert user["role"] == "admin"
        assert user["organization_id"] > 0
        assert "access_token" in res.cookies

        me = await client.get("/api/auth/me")
        assert me.status_code == 200
        assert me.json()["email"] == "a@b.com"

        res2 = await client.post("/api/auth/login", json={"email": "a@b.com", "password": "secret123"})
        assert res2.status_code == 200

        res3 = await client.post("/api/auth/login", json={"email": "a@b.com", "password": "wrong"})
        assert res3.status_code == 401


async def test_register_duplicate_email():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.post("/api/auth/register", json={"email": "dup@b.com", "password": "secret123", "display_name": "B"})
        res = await client.post("/api/auth/register", json={"email": "dup@b.com", "password": "secret123", "display_name": "B2"})
        assert res.status_code == 409
```

- [ ] **Step 2: 写模型**

```python
# backend/app/models/org.py
from sqlalchemy import ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin


class Organization(Base, TimestampMixin):
    __tablename__ = "organizations"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120), unique=True, index=True)
    plan: Mapped[str] = mapped_column(String(20), default="free")


class User(Base, TimestampMixin):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(128))
    display_name: Mapped[str] = mapped_column(String(80))
    organization_id: Mapped[int] = mapped_column(ForeignKey("organizations.id"), index=True)
    role: Mapped[str] = mapped_column(String(20), default="designer")  # admin | designer | viewer
```

```python
# backend/app/models/__init__.py
from app.models.org import Organization, User
```

- [ ] **Step 3: 写 schema 与 auth_service**

```python
# backend/app/schemas/auth.py
from pydantic import BaseModel, EmailStr, Field


class RegisterIn(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8)
    display_name: str = Field(min_length=1, max_length=80)


class LoginIn(BaseModel):
    email: EmailStr
    password: str


class UserOut(BaseModel):
    id: int
    email: EmailStr
    display_name: str
    role: str
    organization_id: int

    model_config = {"from_attributes": True}
```

```python
# backend/app/services/auth_service.py
import bcrypt
import jwt
from datetime import datetime, timedelta, timezone

from app.config.settings import settings
from app.db.session import SessionLocal
from app.models.org import Organization, User


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(password: str, password_hash: str) -> bool:
    return bcrypt.checkpw(password.encode(), password_hash.encode())


def create_token(user_id: int) -> str:
    payload = {"sub": str(user_id), "exp": datetime.now(timezone.utc) + timedelta(minutes=settings.jwt_expire_minutes)}
    return jwt.encode(payload, settings.jwt_secret, algorithm="HS256")


def decode_token(token: str) -> int | None:
    try:
        payload = jwt.decode(token, settings.jwt_secret, algorithms=["HS256"])
        return int(payload["sub"])
    except (jwt.PyJWTError, KeyError, ValueError):
        return None


async def register(email: str, password: str, display_name: str) -> User:
    async with SessionLocal() as db:
        existing = await db.scalar(User.__table__.select().where(User.email == email))
        if existing:
            raise ValueError("email_exists")
        org = Organization(name=f"{display_name}的组织")
        db.add(org)
        await db.flush()
        user = User(email=email, password_hash=hash_password(password), display_name=display_name,
                    organization_id=org.id, role="admin")
        db.add(user)
        await db.commit()
        await db.refresh(user)
        return user


async def login(email: str, password: str) -> User:
    async with SessionLocal() as db:
        user = await db.scalar(User.__table__.select().where(User.email == email))
        if not user or not verify_password(password, user.password_hash):
            raise ValueError("invalid_credentials")
        return user
```

- [ ] **Step 4: 写 deps.py 与 auth 路由**

```python
# backend/app/api/v1/deps.py
from fastapi import Depends, HTTPException, Request

from app.models.org import User
from app.services.auth_service import decode_token


async def get_current_user(request: Request) -> User:
    token = request.cookies.get("access_token")
    if not token:
        raise HTTPException(status_code=401, detail="not_authenticated")
    user_id = decode_token(token)
    if user_id is None:
        raise HTTPException(status_code=401, detail="invalid_token")
    from app.db.session import SessionLocal
    async with SessionLocal() as db:
        user = await db.get(User, user_id)
        if not user:
            raise HTTPException(status_code=401, detail="user_not_found")
        return user
```

```python
# backend/app/api/v1/auth.py
from fastapi import APIRouter, Depends, HTTPException, Response

from app.models.org import User
from app.schemas.auth import LoginIn, RegisterIn, UserOut
from app.services.auth_service import create_token, login, register
from app.api.v1.deps import get_current_user

router = APIRouter()


def _set_token(resp: Response, user_id: int) -> None:
    resp.set_cookie("access_token", create_token(user_id), httponly=True, max_age=60 * 60 * 24 * 7,
                    samesite="lax", secure=False)


@router.post("/register", response_model=UserOut)
async def register_api(body: RegisterIn, resp: Response):
    try:
        user = await register(body.email, body.password, body.display_name)
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))
    _set_token(resp, user.id)
    return user


@router.post("/login", response_model=UserOut)
async def login_api(body: LoginIn, resp: Response):
    try:
        user = await login(body.email, body.password)
    except ValueError:
        raise HTTPException(status_code=401, detail="invalid_credentials")
    _set_token(resp, user.id)
    return user


@router.get("/me", response_model=UserOut)
async def me(user: User = Depends(get_current_user)):
    return user
```

- [ ] **Step 5: 挂载路由并跑测试**

```python
# backend/app/api/v1/router.py
from fastapi import APIRouter
from app.api.v1 import auth

api_router = APIRouter()
api_router.include_router(auth.router, prefix="/auth", tags=["auth"])
```

```bash
cd /Users/pipi/CodeSpace/ai-codesign-studio/backend
DATABASE_URL=sqlite+aiosqlite:///:memory: pytest tests/test_auth.py -v
```

Expected: 2 PASS。

- [ ] **Step 6: Commit**

```bash
git add -A && git commit -m "feat: 组织/用户模型与 JWT 认证"
```

---

## Task 4: 项目 CRUD + 成员（组织内）

**Files:**
- Create: `backend/app/models/project.py`
- Create: `backend/app/schemas/project.py`
- Create: `backend/app/api/v1/projects.py`
- Create: `backend/tests/test_projects.py`

- [ ] **Step 1: 写失败的测试**

```python
# backend/tests/test_projects.py
from httpx import ASGITransport, AsyncClient
from app.main import app


async def _authed_client() -> AsyncClient:
    client = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
    await client.post("/api/auth/register", json={"email": "p@b.com", "password": "secret123", "display_name": "P"})
    return client


async def test_project_crud():
    client = await _authed_client()
    res = await client.post("/api/projects", json={"name": "卧室衣柜", "description": "客户新房"})
    assert res.status_code == 200
    project = res.json()
    pid = project["id"]
    assert project["name"] == "卧室衣柜"

    listed = await client.get("/api/projects")
    assert listed.status_code == 200
    assert len(listed.json()) == 1

    updated = await client.patch(f"/api/projects/{pid}", json={"name": "主卧衣柜"})
    assert updated.json()["name"] == "主卧衣柜"

    deleted = await client.delete(f"/api/projects/{pid}")
    assert deleted.status_code == 204
    assert (await client.get("/api/projects")).json() == []


async def test_project_requires_auth():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        res = await client.post("/api/projects", json={"name": "x"})
    assert res.status_code == 401


async def test_project_org_isolation():
    c1 = await _authed_client()
    c2 = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
    await c2.post("/api/auth/register", json={"email": "q@b.com", "password": "secret123", "display_name": "Q"})
    await c1.post("/api/projects", json={"name": "私密项目"})
    listed = await c2.get("/api/projects")
    assert listed.json() == []
```

- [ ] **Step 2: 写模型与 schema**

```python
# backend/app/models/project.py
from sqlalchemy import ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin


class Project(Base, TimestampMixin):
    __tablename__ = "projects"

    id: Mapped[int] = mapped_column(primary_key=True)
    organization_id: Mapped[int] = mapped_column(ForeignKey("organizations.id"), index=True)
    name: Mapped[str] = mapped_column(String(120))
    description: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(20), default="active")  # active | archived
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"))


class ProjectMember(Base, TimestampMixin):
    __tablename__ = "project_members"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    role: Mapped[str] = mapped_column(String(20), default="designer")  # owner | designer | viewer
```

```python
# backend/app/schemas/project.py
from pydantic import BaseModel, Field


class ProjectIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: str = ""


class ProjectUpdateIn(BaseModel):
    name: str | None = None
    description: str | None = None
    status: str | None = None


class ProjectOut(BaseModel):
    id: int
    name: str
    description: str
    status: str
    created_by: int
    created_at: object | None = None

    model_config = {"from_attributes": True}
```

- [ ] **Step 3: 写路由**

```python
# backend/app/api/v1/projects.py
from fastapi import APIRouter, Depends, HTTPException

from app.api.v1.deps import get_current_user
from app.db.session import SessionLocal
from app.models.org import User
from app.models.project import Project, ProjectMember
from app.schemas.project import ProjectIn, ProjectOut, ProjectUpdateIn

router = APIRouter()


@router.get("", response_model=list[ProjectOut])
async def list_projects(user: User = Depends(get_current_user)):
    async with SessionLocal() as db:
        stmt = (Project.__table__.select()
                .where(Project.organization_id == user.organization_id)
                .order_by(Project.id.desc()))
        rows = await db.execute(stmt)
        return rows.scalars().all()


@router.post("", response_model=ProjectOut)
async def create_project(body: ProjectIn, user: User = Depends(get_current_user)):
    async with SessionLocal() as db:
        project = Project(organization_id=user.organization_id, name=body.name,
                          description=body.description, created_by=user.id)
        db.add(project)
        await db.flush()
        db.add(ProjectMember(project_id=project.id, user_id=user.id, role="owner"))
        await db.commit()
        await db.refresh(project)
        return project


async def _get_org_project(db, project_id: int, user: User) -> Project:
    project = await db.get(Project, project_id)
    if not project or project.organization_id != user.organization_id:
        raise HTTPException(status_code=404, detail="project_not_found")
    return project


@router.patch("/{project_id}", response_model=ProjectOut)
async def update_project(project_id: int, body: ProjectUpdateIn, user: User = Depends(get_current_user)):
    async with SessionLocal() as db:
        project = await _get_org_project(db, project_id, user)
        for field, value in body.model_dump(exclude_unset=True).items():
            setattr(project, field, value)
        await db.commit()
        await db.refresh(project)
        return project


@router.delete("/{project_id}", status_code=204)
async def delete_project(project_id: int, user: User = Depends(get_current_user)):
    async with SessionLocal() as db:
        project = await _get_org_project(db, project_id, user)
        await db.delete(project)
        await db.commit()
```

- [ ] **Step 4: 挂载路由并跑测试**

```python
# router.py 追加
from app.api.v1 import auth, projects
api_router.include_router(projects.router, prefix="/projects", tags=["projects"])
```

```bash
DATABASE_URL=sqlite+aiosqlite:///:memory: pytest tests/test_projects.py -v
```

Expected: 3 PASS。

- [ ] **Step 5: Commit**

```bash
git add -A && git commit -m "feat: 项目 CRUD 与组织隔离"
```

---

## Task 5: 资产上传（MinIO 对象存储 + Asset/AssetVersion）

**Files:**
- Create: `backend/app/models/asset.py`
- Create: `backend/app/schemas/asset.py`
- Create: `backend/app/services/asset_service.py`
- Create: `backend/app/api/v1/assets.py`
- Create: `backend/tests/test_assets.py`

- [ ] **Step 1: 写失败的测试**

```python
# backend/tests/test_assets.py
import io

from httpx import ASGITransport, AsyncClient
from app.main import app


async def _authed_client_with_project() -> tuple[AsyncClient, int]:
    client = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
    await client.post("/api/auth/register", json={"email": "a@b.com", "password": "secret123", "display_name": "A"})
    res = await client.post("/api/projects", json={"name": "项目"})
    return client, res.json()["id"]


async def test_upload_and_list_assets():
    client, pid = await _authed_client_with_project()
    files = {"file": ("ref.jpg", io.BytesIO(b"fake-jpeg-bytes"), "image/jpeg")}
    res = await client.post(f"/api/projects/{pid}/assets", files=files)
    assert res.status_code == 200
    asset = res.json()
    assert asset["kind"] == "image"
    assert asset["original_name"] == "ref.jpg"
    assert asset["version_no"] == 1

    listed = await client.get(f"/api/projects/{pid}/assets")
    assert len(listed.json()) == 1


async def test_upload_rejects_non_image_for_analyze_kinds():
    client, pid = await _authed_client_with_project()
    files = {"file": ("a.txt", io.BytesIO(b"hello"), "text/plain")}
    res = await client.post(f"/api/projects/{pid}/assets", files=files)
    assert res.status_code == 200
    assert res.json()["kind"] == "document"
```

- [ ] **Step 2: 写模型**

```python
# backend/app/models/asset.py
from sqlalchemy import BigInteger, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin


class Asset(Base, TimestampMixin):
    __tablename__ = "assets"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), index=True)
    kind: Mapped[str] = mapped_column(String(20), index=True)  # image | document | model | video
    original_name: Mapped[str] = mapped_column(String(255))
    mime_type: Mapped[str] = mapped_column(String(100))
    size_bytes: Mapped[int] = mapped_column(BigInteger, default=0)
    current_version_id: Mapped[int] = mapped_column(default=0)
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"))


class AssetVersion(Base, TimestampMixin):
    __tablename__ = "asset_versions"

    id: Mapped[int] = mapped_column(primary_key=True)
    asset_id: Mapped[int] = mapped_column(ForeignKey("assets.id"), index=True)
    version_no: Mapped[int] = mapped_column(default=1)
    storage_key: Mapped[str] = mapped_column(String(255))
    checksum: Mapped[str] = mapped_column(String(64), default="")
```

- [ ] **Step 3: 写 asset_service（MinIO 上传，测试环境落本地磁盘）**

```python
# backend/app/services/asset_service.py
import asyncio
import hashlib
from pathlib import Path

from app.config.settings import settings

_local_dir = Path(settings.storage_local_dir)


def _use_s3() -> bool:
    return bool(settings.s3_endpoint and settings.s3_access_key)


async def put_object(key: str, data: bytes, content_type: str) -> None:
    if _use_s3():
        import boto3
        from botocore.client import Config

        def _upload():
            client = boto3.client(
                "s3", endpoint_url=settings.s3_endpoint,
                aws_access_key_id=settings.s3_access_key,
                aws_secret_access_key=settings.s3_secret_key,
                config=Config(signature_version="s3v4"), region_name="us-east-1",
            )
            try:
                client.head_bucket(Bucket=settings.s3_bucket)
            except Exception:
                client.create_bucket(Bucket=settings.s3_bucket)
            client.put_object(Bucket=settings.s3_bucket, Key=key, Body=data, ContentType=content_type)

        await asyncio.to_thread(_upload)
    else:
        target = _local_dir / key
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)


async def get_object(key: str) -> bytes:
    if _use_s3():
        import boto3
        from botocore.client import Config

        def _download():
            client = boto3.client(
                "s3", endpoint_url=settings.s3_endpoint,
                aws_access_key_id=settings.s3_access_key,
                aws_secret_access_key=settings.s3_secret_key,
                config=Config(signature_version="s3v4"), region_name="us-east-1",
            )
            obj = client.get_object(Bucket=settings.s3_bucket, Key=key)
            return obj["Body"].read()

        return await asyncio.to_thread(_download)
    return (_local_dir / key).read_bytes()


def make_key(project_id: int, asset_id: int, version_no: int, ext: str) -> str:
    return f"projects/{project_id}/assets/{asset_id}/v{version_no}.{ext}"


def checksum_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()
```

- [ ] **Step 4: 写 schema 与路由**

```python
# backend/app/schemas/asset.py
from pydantic import BaseModel


class AssetOut(BaseModel):
    id: int
    project_id: int
    kind: str
    original_name: str
    mime_type: str
    size_bytes: int
    version_no: int
    created_at: object | None = None

    model_config = {"from_attributes": True}
```

```python
# backend/app/api/v1/assets.py
from fastapi import APIRouter, Depends, File, HTTPException, UploadFile

from app.api.v1.deps import get_current_user
from app.api.v1.projects import _get_org_project
from app.db.session import SessionLocal
from app.models.asset import Asset, AssetVersion
from app.models.org import User
from app.models.project import Project
from app.schemas.asset import AssetOut
from app.services.asset_service import checksum_hex, get_object, make_key, put_object

router = APIRouter()

_KIND_BY_MIME = {"image": ["image/"], "document": ["text/", "application/pdf"], "model": ["model/"], "video": ["video/"]}


def _kind_for(mime: str) -> str:
    for kind, prefixes in _KIND_BY_MIME.items():
        if any(mime.startswith(p) for p in prefixes):
            return kind
    return "document"


@router.get("", response_model=list[AssetOut])
async def list_assets(project_id: int, user: User = Depends(get_current_user)):
    async with SessionLocal() as db:
        await _get_org_project(db, project_id, user)
        rows = await db.execute(Asset.__table__.select().where(Asset.project_id == project_id).order_by(Asset.id.desc()))
        assets = rows.scalars().all()
        return assets


@router.post("", response_model=AssetOut)
async def upload_asset(project_id: int, file: UploadFile = File(...), user: User = Depends(get_current_user)):
    data = await file.read()
    async with SessionLocal() as db:
        await _get_org_project(db, project_id, user)
        asset = Asset(project_id=project_id, kind=_kind_for(file.content_type or ""),
                      original_name=file.filename or "unnamed", mime_type=file.content_type or "",
                      size_bytes=len(data), created_by=user.id)
        db.add(asset)
        await db.flush()
        ext = (file.filename or "bin").rsplit(".", 1)[-1] if "." in (file.filename or "") else "bin"
        key = make_key(project_id, asset.id, 1, ext)
        version = AssetVersion(asset_id=asset.id, version_no=1, storage_key=key, checksum=checksum_hex(data))
        db.add(version)
        asset.current_version_id = version.id
        await db.commit()
        await put_object(key, data, file.content_type or "application/octet-stream")
        await db.refresh(asset)
        return asset
```

- [ ] **Step 5: 挂载并跑测试**

```python
# router.py 追加
from app.api.v1 import auth, projects, assets
api_router.include_router(assets.router, prefix="/projects/{project_id}/assets", tags=["assets"])
```

```bash
DATABASE_URL=sqlite+aiosqlite:///:memory: pytest tests/test_assets.py -v
```

Expected: 2 PASS（本机无 S3 时自动落 `backend/data/assets/` 本地目录）。

- [ ] **Step 6: Commit**

```bash
git add -A && git commit -m "feat: 资产上传与版本化（S3 兼容存储）"
```

---

## Task 6: AI 网关（OpenAI 兼容 + 本地规则引擎兜底）

**Files:**
- Create: `backend/app/services/ai_gateway_service.py`
- Create: `backend/app/services/local_rules.py`
- Create: `backend/tests/test_ai_gateway.py`

- [ ] **Step 1: 写失败的测试**

```python
# backend/tests/test_ai_gateway.py
import pytest

from app.services.ai_gateway_service import run_ai
from app.services.local_rules import LocalRulesProvider
from app.services.resource_scheduler import route_for


async def test_local_fallback_returns_structured_output():
    provider = LocalRulesProvider()
    result = await provider.run("concept_generation", "QUICK", {"text": "北欧风白橡木餐椅"})
    assert result.text
    assert result.structured and len(result.structured["concepts"]) == 3


async def test_run_ai_uses_router_and_fallback():
    route = route_for("concept_generation", "QUICK")
    assert route.pool in ("ascend_910a", "gpu_5090", "local")
    result = await run_ai("concept_generation", "QUICK", {"text": "北欧风白橡木餐椅"}, enable_fallback=True)
    assert result.text != ""
```

- [ ] **Step 2: 写 local_rules.py（家具规则引擎，从参考项目 localGenerator 模式精简）**

```python
# backend/app/services/local_rules.py
from dataclasses import dataclass, field
import re


@dataclass
class AIResult:
    text: str = ""
    images: list[str] = field(default_factory=list)
    structured: dict = field(default_factory=dict)


_FURNITURE_KEYWORDS = {
    "chair": ["椅", "凳子", "stool"],
    "table": ["桌", "台", "茶几"],
    "cabinet": ["柜", "柜子", "收纳"],
    "sofa": ["沙发", "坐具"],
    "bed": ["床"],
}

_DEGREE_PROMPT = {
    "QUICK": "给出 3 个快速概念（名称+一段话+大致尺寸），简洁。",
    "STANDARD": "给出 3 个方案（名称+设计说明+主要材料+尺寸+工艺要点），结构化输出。",
    "DEEP": "给出 3 个深度方案（名称+完整设计说明+材料明细+结构工艺+生产注意），结构化输出。",
}


class LocalRulesProvider:
    """无外部模型时的兜底：按家具关键词规则生成结构化方案，保证接口可跑通。"""

    async def run(self, task_type: str, degree: str, inputs: dict) -> AIResult:
        if task_type == "requirement_analysis":
            return self._requirement(inputs)
        if task_type == "scene_recognition":
            return self._scene(inputs)
        if task_type == "concept_generation":
            return self._concepts(inputs, degree)
        if task_type == "rendering":
            return self._rendering(inputs)
        return AIResult(text="unknown task type")

    def _detect_furniture(self, text: str) -> list[str]:
        found = []
        for name, keywords in _FURNITURE_KEYWORDS.items():
            if any(k in text for k in keywords):
                found.append(name)
        return found or ["chair"]

    def _requirement(self, inputs: dict) -> AIResult:
        text = inputs.get("text", "")
        furnitures = self._detect_furniture(text)
        return AIResult(
            text="已解析需求（本地规则模式）：" + text,
            structured={"furniture_types": furnitures, "style_hints": ["北欧"] if "北欧" in text else [],
                        "extracted_from": "text" if text else "image"},
        )

    def _scene(self, inputs: dict) -> AIResult:
        return AIResult(
            text="场景识别完成（本地规则模式）",
            structured={"objects": [{"label": "房间", "confidence": 0.5, "bbox": [0, 0, 0.5, 0.5]}]},
        )

    def _concepts(self, inputs: dict, degree: str) -> AIResult:
        text = inputs.get("text", "定制家具")
        furnitures = self._detect_furniture(text)
        styles = ["北欧", "现代", "简约"] if "北欧" not in text else ["北欧原木", "北欧白蜡木", "北欧胡桃木"]
        concepts = []
        for i in range(3):
            concepts.append({
                "title": f"{styles[i]}{{'chair':'餐椅','table':'餐桌','cabinet':'收纳柜','sofa':'沙发','bed':'床'}.get(furnitures[0], '家具')}方案{i + 1}",
                "summary": f"以{styles[i]}风格为主，采用白橡木与金属件结合，符合客户「{text[:20]}」需求。",
                "materials": ["白橡木", "金属五金"],
                "dimensions": {"width_cm": 450 + i * 50, "depth_cm": 450, "height_cm": 750 + i * 10},
                "craft": "榫卯 + 五金连接",
            })
        return AIResult(text=f"已生成 3 个{degree}等级概念方案", structured={"concepts": concepts})

    def _rendering(self, inputs: dict) -> AIResult:
        return AIResult(text="渲染完成（本地规则模式：无真实出图）",
                        structured={"mode": "local_placeholder", "images": []})
```

- [ ] **Step 3: 写 resource_scheduler.py（算力路由表）**

```python
# backend/app/services/resource_scheduler.py
from dataclasses import dataclass


@dataclass(frozen=True)
class ResourceRoute:
    pool: str          # ascend_910a | gpu_5090 | local
    provider_kind: str # llm | vision_llm | comfyui | rules
    external: bool = False
    note: str = ""


# 规格书六.2 建议调度规则：
# 需求分析/文档理解/知识检索 → 910A；效果图/图片修改/宣传图 → 5090；3D/视频 → 5090
ROUTE_TABLE = {
    "requirement_analysis": ResourceRoute("ascend_910a", "llm", note="需求分析与文档理解优先 910A 推理"),
    "scene_recognition": ResourceRoute("ascend_910a", "vision_llm", note="图片理解优先 910A"),
    "concept_generation": ResourceRoute("ascend_910a", "llm", note="方案生成 910A"),
    "rendering": ResourceRoute("gpu_5090", "comfyui", note="效果图渲染 5090 生成节点"),
}

_TASK_EXTERNAL_COST = {"rendering": 0.5, "scene_recognition": 0.3}


def route_for(task_type: str, degree: str, priority: str = "normal") -> ResourceRoute:
    base = ROUTE_TABLE.get(task_type, ResourceRoute("ascend_910a", "llm"))
    if priority == "high":
        return ResourceRoute(base.pool, base.provider_kind, True, note=base.note + "（高优先级走外部加速）")
    return base


def needs_auth(task_type: str) -> bool:
    """外部调用（付费/云端 API）需要用户授权。"""
    return route_for(task_type, "STANDARD").external or task_type in _TASK_EXTERNAL_COST


def route_record(task_type: str, degree: str) -> dict:
    """内部路由记录，只进管理员日志，绝不返回给普通用户。"""
    r = route_for(task_type, degree)
    return {"task_type": task_type, "pool": r.pool, "provider": r.provider_kind,
            "external": r.external, "note": r.note}
```

- [ ] **Step 4: 写 ai_gateway_service.py**

```python
# backend/app/services/ai_gateway_service.py
import json

import httpx

from app.config.settings import settings
from app.services.local_rules import AIResult, LocalRulesProvider
from app.services.resource_scheduler import ResourceRoute, route_for

_SYSTEM_PROMPTS = {
    "requirement_analysis": "你是家具行业需求分析师。从用户描述与图片说明中提取结构化需求，输出 JSON：{furniture_types, style_hints, dimensions, notes}。",
    "scene_recognition": "你是场景识别器。从图片说明中识别场景物体，输出 JSON：{objects: [{label, confidence, bbox}]}。",
    "concept_generation": "你是工业设计师。根据需求生成 3 个差异化概念方案，输出 JSON：{concepts: [{title, summary, materials, dimensions, craft}]}。",
    "rendering": "你是产品效果图生成任务。根据方案描述生成效果图任务参数，输出 JSON：{prompt, negative_prompt, steps, width, height}。",
}

_DEGREE_MODEL = {"QUICK": settings.ai_model_quick, "STANDARD": settings.ai_model_standard, "DEEP": settings.ai_model_deep}


async def run_ai(task_type: str, degree: str, inputs: dict, enable_fallback: bool = True) -> AIResult:
    route = route_for(task_type, degree)
    if _is_local_only(route):
        if enable_fallback:
            return await LocalRulesProvider().run(task_type, degree, inputs)
        raise RuntimeError(f"no_provider_for_route:{route.pool}")
    return await _openai_compatible(task_type, degree, inputs)


def _is_local_only(route: ResourceRoute) -> bool:
    if route.pool == "local":
        return True
    if route.provider_kind == "comfyui":
        return not settings.comfyui_endpoint  # 未配置 ComfyUI 则本地兜底
    return not settings.ai_base_url or not settings.ai_api_key


async def _openai_compatible(task_type: str, degree: str, inputs: dict) -> AIResult:
    model = _DEGREE_MODEL.get(degree, settings.ai_model_standard)
    async with httpx.AsyncClient(timeout=120) as client:
        resp = await client.post(
            f"{settings.ai_base_url.rstrip('/')}/chat/completions",
            headers={"Authorization": f"Bearer {settings.ai_api_key}"},
            json={
                "model": model,
                "messages": [
                    {"role": "system", "content": _SYSTEM_PROMPTS[task_type]},
                    {"role": "user", "content": json.dumps(inputs, ensure_ascii=False)},
                ],
                "response_format": {"type": "json_object"},
            },
        )
        resp.raise_for_status()
        content = resp.json()["choices"][0]["message"]["content"]
        try:
            structured = json.loads(content)
        except json.JSONDecodeError:
            structured = {}
        return AIResult(text=content, structured=structured)
```

- [ ] **Step 5: 跑测试**

```bash
DATABASE_URL=sqlite+aiosqlite:///:memory: pytest tests/test_ai_gateway.py -v
```

Expected: 2 PASS（无任何外部 Key 时走本地规则兜底）。

- [ ] **Step 6: Commit**

```bash
git add -A && git commit -m "feat: AI 网关与算力路由表（本地规则兜底）"
```

---

## Task 7: 工作流模型 + 状态机（Node/Edge/Job/Authorization）

**Files:**
- Create: `backend/app/models/workflow.py`
- Create: `backend/app/services/workflow_service.py`
- Create: `backend/tests/test_workflow_state.py`

- [ ] **Step 1: 写失败的测试**

```python
# backend/tests/test_workflow_state.py
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db.session import SessionLocal
from app.services.workflow_service import (
    STATUS_FLOW, branch_node, create_node, decide_authorization, start_node,
)
from app.models.org import Organization, User
from app.models.project import Project
from app.models.workflow import AuthorizationRecord, ExecutionJob, WorkflowNode


async def _seed_user_project():
    async with SessionLocal() as db:
        org = Organization(name=f"org-{id(db)}")
        db.add(org)
        await db.flush()
        user = User(email=f"u{id(db)}@t.com", password_hash="x", display_name="U", organization_id=org.id, role="admin")
        db.add(user)
        await db.flush()
        project = Project(organization_id=org.id, name="p", created_by=user.id)
        db.add(project)
        await db.commit()
        return org.id, user.id, project.id


async def test_node_state_machine_transitions():
    org_id, user_id, project_id = await _seed_user_project()
    async with SessionLocal() as db:
        node = await create_node(db, project_id, "concept_generation", "STANDARD", {}, user_id)
        assert node.status == "DRAFT"
        await start_node(db, node.id)
        await db.refresh(node)
        assert node.status == "QUEUED"
        await db.execute(
            ExecutionJob.__table__.update().values(status="RUNNING").where(ExecutionJob.workflow_node_id == node.id))
        await db.commit()


async def test_succeed_and_branch_creates_new_version():
    org_id, user_id, project_id = await _seed_user_project()
    async with SessionLocal() as db:
        node = await create_node(db, project_id, "concept_generation", "STANDARD", {}, user_id)
        await start_node(db, node.id)
        await db.execute(
            ExecutionJob.__table__.update().values(status="RUNNING").where(ExecutionJob.workflow_node_id == node.id))
        node.status = "SUCCEEDED"
        await db.commit()
        child = await branch_node(db, node.id, "revision", user_id, {"instruction": "改圆角"})
        assert child.status == "DRAFT"
        assert child.branch_of == node.id
        assert child.branch_type == "revision"


async def test_authorization_flow():
    org_id, user_id, project_id = await _seed_user_project()
    async with SessionLocal() as db:
        node = await create_node(db, project_id, "rendering", "STANDARD", {}, user_id, auth_required=True)
        await start_node(db, node.id)
        await db.refresh(node)
        assert node.status == "WAITING_AUTH"
        record = await decide_authorization(db, node.id, user_id, approve=True, reason="外部渲染费用可接受")
        assert record.action == "approve"
        await db.refresh(node)
        assert node.status == "QUEUED"
```

- [ ] **Step 2: 写模型**

```python
# backend/app/models/workflow.py
from sqlalchemy import JSON, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin

# 状态机：DRAFT, WAITING_INPUT, WAITING_AUTH, QUEUED, RUNNING,
#        SUCCEEDED, FAILED, CANCELLED, NEEDS_REVIEW


class WorkflowNode(Base, TimestampMixin):
    __tablename__ = "workflow_nodes"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), index=True)
    scheme_id: Mapped[int] = mapped_column(ForeignKey("design_schemes.id"), nullable=True, index=True)
    type: Mapped[str] = mapped_column(String(40), index=True)  # requirement_analysis | scene_recognition | concept_generation | rendering | ...
    status: Mapped[str] = mapped_column(String(20), default="DRAFT", index=True)
    degree: Mapped[str] = mapped_column(String(10), default="STANDARD")  # QUICK | STANDARD | DEEP
    input_json: Mapped[dict] = mapped_column(JSON, default=dict)
    output_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    summary: Mapped[str] = mapped_column(Text, default="")
    branch_of: Mapped[int | None] = mapped_column(ForeignKey("workflow_nodes.id"), nullable=True)
    branch_type: Mapped[str] = mapped_column(String(20), default="")  # revision | publicity | production | retry
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"))


class WorkflowEdge(Base, TimestampMixin):
    __tablename__ = "workflow_edges"

    id: Mapped[int] = mapped_column(primary_key=True)
    from_node_id: Mapped[int] = mapped_column(ForeignKey("workflow_nodes.id"), index=True)
    to_node_id: Mapped[int] = mapped_column(ForeignKey("workflow_nodes.id"), index=True)
    kind: Mapped[str] = mapped_column(String(20), default="success")  # success | failure | branch


class ExecutionJob(Base, TimestampMixin):
    __tablename__ = "execution_jobs"

    id: Mapped[int] = mapped_column(primary_key=True)
    workflow_node_id: Mapped[int] = mapped_column(ForeignKey("workflow_nodes.id"), index=True)
    task_type: Mapped[str] = mapped_column(String(40), index=True)
    degree: Mapped[str] = mapped_column(String(10))
    status: Mapped[str] = mapped_column(String(20), default="QUEUED", index=True)
    progress: Mapped[float] = mapped_column(Float, default=0.0)
    error_message: Mapped[str] = mapped_column(Text, default="")
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    auth_required: Mapped[bool] = mapped_column(default=False)
    internal_route_json: Mapped[dict] = mapped_column(JSON, default=dict)  # 仅管理员可见
    external_api_used: Mapped[bool] = mapped_column(default=False)
    started_at: Mapped[object | None] = mapped_column(nullable=True)
    finished_at: Mapped[object | None] = mapped_column(nullable=True)


class AuthorizationRecord(Base, TimestampMixin):
    __tablename__ = "authorization_records"

    id: Mapped[int] = mapped_column(primary_key=True)
    execution_job_id: Mapped[int] = mapped_column(ForeignKey("execution_jobs.id"), index=True)
    workflow_node_id: Mapped[int] = mapped_column(ForeignKey("workflow_nodes.id"), index=True)
    requested_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    decided_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    action: Mapped[str] = mapped_column(String(20))  # approve | reject
    reason: Mapped[str] = mapped_column(Text, default="")
    external_api: Mapped[bool] = mapped_column(default=False)
    cost_estimate_json: Mapped[dict] = mapped_column(JSON, default=dict)
```

- [ ] **Step 3: 写 workflow_service.py（状态机 + 分支 + 授权）**

```python
# backend/app/services/workflow_service.py
from datetime import datetime, timezone

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.workflow import AuthorizationRecord, ExecutionJob, WorkflowEdge, WorkflowNode
from app.services.resource_scheduler import needs_auth, route_record

# 节点状态迁移表：{当前状态: {动作: [允许的目标状态]}}
STATUS_FLOW = {
    "DRAFT": {"start": ["QUEUED", "WAITING_AUTH"], "cancel": ["CANCELLED"]},
    "WAITING_INPUT": {"start": ["QUEUED"], "cancel": ["CANCELLED"]},
    "WAITING_AUTH": {"authorize": ["QUEUED"], "cancel": ["CANCELLED"]},
    "QUEUED": {"run": ["RUNNING"], "fail": ["FAILED"], "cancel": ["CANCELLED"]},
    "RUNNING": {"succeed": ["SUCCEEDED", "NEEDS_REVIEW"], "fail": ["FAILED", "WAITING_AUTH"], "cancel": ["CANCELLED"]},
    "SUCCEEDED": {"branch": ["DRAFT"], "rerun": ["QUEUED"]},
    "FAILED": {"retry": ["QUEUED"], "cancel": ["CANCELLED"]},
    "NEEDS_REVIEW": {"approve": ["SUCCEEDED"], "revise": ["DRAFT"]},
    "CANCELLED": {},
}

_AUTH_TASK_TYPES = {"rendering", "scene_recognition"}


async def create_node(db: AsyncSession, project_id: int, node_type: str, degree: str,
                      input_json: dict, user_id: int, scheme_id: int | None = None,
                      auth_required: bool | None = None) -> WorkflowNode:
    node = WorkflowNode(project_id=project_id, scheme_id=scheme_id, type=node_type, degree=degree,
                        input_json=input_json, created_by=user_id, status="DRAFT")
    db.add(node)
    await db.flush()
    auth_required = needs_auth(node_type) if auth_required is None else auth_required
    job = ExecutionJob(workflow_node_id=node.id, task_type=node_type, degree=degree,
                       status="QUEUED", auth_required=auth_required,
                       internal_route_json=route_record(node_type, degree))
    db.add(job)
    await db.commit()
    await db.refresh(node)
    return node


async def start_node(db: AsyncSession, node_id: int) -> None:
    node = await db.get(WorkflowNode, node_id)
    if not node:
        raise HTTPException(404, "node_not_found")
    targets = STATUS_FLOW.get(node.status, {}).get("start", [])
    job = await db.scalar(
        ExecutionJob.__table__.select().where(ExecutionJob.workflow_node_id == node_id).limit(1))
    if job and job.auth_required and job.status == "QUEUED" and not job.external_api_used:
        _transition(db, node, "WAITING_AUTH")
        await db.commit()
        return
    if "QUEUED" not in targets and "WAITING_AUTH" not in targets:
        raise HTTPException(409, f"invalid_transition:{node.status}->start")
    _transition(db, node, "WAITING_AUTH" if (job and job.auth_required) else "QUEUED")
    await db.commit()


async def branch_node(db: AsyncSession, node_id: int, branch_type: str, user_id: int,
                      input_json: dict | None = None, degree: str | None = None) -> WorkflowNode:
    parent = await db.get(WorkflowNode, node_id)
    if not parent:
        raise HTTPException(404, "node_not_found")
    targets = STATUS_FLOW.get(parent.status, {}).get("branch", [])
    if "DRAFT" not in targets and parent.status != "SUCCEEDED":
        raise HTTPException(409, f"cannot_branch_from:{parent.status}")
    child = await create_node(db, parent.project_id, parent.type,
                              degree or parent.degree, input_json or parent.input_json,
                              user_id, scheme_id=parent.scheme_id,
                              auth_required=needs_auth(parent.type))
    child.branch_of = node_id
    child.branch_type = branch_type
    db.add(WorkflowEdge(from_node_id=node_id, to_node_id=child.id, kind="branch"))
    await db.commit()
    await db.refresh(child)
    return child


async def decide_authorization(db: AsyncSession, node_id: int, user_id: int,
                               approve: bool, reason: str) -> AuthorizationRecord:
    node = await db.get(WorkflowNode, node_id)
    if not node or node.status != "WAITING_AUTH":
        raise HTTPException(409, "node_not_waiting_auth")
    job = await db.scalar(
        ExecutionJob.__table__.select().where(ExecutionJob.workflow_node_id == node_id).limit(1))
    if not job:
        raise HTTPException(404, "job_not_found")
    record = AuthorizationRecord(
        execution_job_id=job.id, workflow_node_id=node_id, requested_by=node.created_by,
        decided_by=user_id, action="approve" if approve else "reject",
        reason=reason, external_api=job.external_api_used, cost_estimate_json={},
    )
    db.add(record)
    if approve:
        _transition(db, node, "QUEUED")
        job.status = "QUEUED"
    else:
        _transition(db, node, "CANCELLED")
        job.status = "CANCELLED"
    await db.commit()
    return record


def _transition(db: AsyncSession, node: WorkflowNode, target: str) -> None:
    allowed = STATUS_FLOW.get(node.status, {}).values()
    flat = {t for targets in allowed for t in targets}
    if target not in flat:
        raise HTTPException(409, f"invalid_transition:{node.status}->{target}")
    node.status = target


def now_utc() -> datetime:
    return datetime.now(timezone.utc)
```

- [ ] **Step 4: 更新 models/__init__.py 并跑测试**

```python
# backend/app/models/__init__.py
from app.models.org import Organization, User
from app.models.project import Project, ProjectMember
from app.models.asset import Asset, AssetVersion
from app.models.workflow import AuthorizationRecord, ExecutionJob, WorkflowEdge, WorkflowNode
```

```bash
DATABASE_URL=sqlite+aiosqlite:///:memory: pytest tests/test_workflow_state.py -v
```

Expected: 3 PASS。

- [ ] **Step 5: Commit**

```bash
git add -A && git commit -m "feat: 工作流节点状态机、分支与授权"
```

---

## Task 8: 需求提取与场景识别（analyze API + executors）

**Files:**
- Create: `backend/app/models/design.py`
- Create: `backend/app/services/executors.py`
- Create: `backend/app/api/v1/analyze.py`
- Create: `backend/tests/test_analyze.py`

- [ ] **Step 1: 写失败的测试**

```python
# backend/tests/test_analyze.py
from httpx import ASGITransport, AsyncClient
from app.main import app


async def _client_with_project() -> tuple[AsyncClient, int]:
    client = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
    await client.post("/api/auth/register", json={"email": "an@b.com", "password": "secret123", "display_name": "AN"})
    res = await client.post("/api/projects", json={"name": "分析项目"})
    return client, res.json()["id"]


async def test_analyze_creates_requirement_and_nodes():
    client, pid = await _client_with_project()
    res = await client.post(f"/api/projects/{pid}/analyze", json={"text": "北欧风白橡木餐椅，适合小户型"})
    assert res.status_code == 200
    data = res.json()
    assert data["requirement_id"] > 0
    assert data["nodes"] and len(data["nodes"]) >= 2


async def test_analyze_stores_requirement_row():
    client, pid = await _client_with_project()
    res = await client.post(f"/api/projects/{pid}/analyze", json={"text": "需要一组客厅沙发"})
    requirement_id = res.json()["requirement_id"]
    got = await client.get(f"/api/projects/{pid}/requirements/{requirement_id}")
    assert got.status_code == 200
    assert "沙发" in got.json()["raw_text"] or got.json()["extracted"]["furniture_types"]
```

- [ ] **Step 2: 写 design.py 模型（Requirement/SceneObject，方案模型 Task 9 加）**

```python
# backend/app/models/design.py
from sqlalchemy import JSON, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin


class Requirement(Base, TimestampMixin):
    __tablename__ = "requirements"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), index=True)
    raw_text: Mapped[str] = mapped_column(Text, default="")
    source_asset_ids: Mapped[list] = mapped_column(JSON, default=list)
    extracted: Mapped[dict] = mapped_column(JSON, default=dict)  # AI 结构化需求
    status: Mapped[str] = mapped_column(String(20), default="done")


class SceneObject(Base, TimestampMixin):
    __tablename__ = "scene_objects"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), index=True)
    requirement_id: Mapped[int] = mapped_column(ForeignKey("requirements.id"), index=True)
    label: Mapped[str] = mapped_column(String(120))
    confidence: Mapped[float] = mapped_column(default=0.0)
    bbox_json: Mapped[dict] = mapped_column(JSON, default=dict)
    image_asset_id: Mapped[int | None] = mapped_column(ForeignKey("assets.id"), nullable=True)
```

- [ ] **Step 3: 写 executors.py（执行器注册表，worker 用；幂等：output_json 已存在则跳过）**

```python
# backend/app/services/executors.py
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.workflow import ExecutionJob, WorkflowNode
from app.services.ai_gateway_service import run_ai


async def _update_job(db: AsyncSession, job: ExecutionJob, status: str, progress: float,
                      error: str = "") -> None:
    job.status = status
    job.progress = progress
    job.error_message = error
    await db.commit()


async def requirement_executor(db: AsyncSession, node: WorkflowNode, job: ExecutionJob) -> None:
    if node.output_json:  # 幂等：已执行过直接成功
        await _update_job(db, job, "SUCCEEDED", 1.0)
        return
    await _update_job(db, job, "RUNNING", 0.2)
    result = await run_ai("requirement_analysis", node.degree, node.input_json)
    await _update_job(db, job, "RUNNING", 0.8)
    from app.models.design import Requirement
    # 更新 analyze 路由已建的空需求行；找不到则新建（幂等 upsert）
    requirement = await db.scalar(
        Requirement.__table__.select().where(Requirement.project_id == node.project_id)
        .order_by(Requirement.id.desc()).limit(1))
    if requirement is None or requirement.extracted:
        requirement = Requirement(project_id=node.project_id,
                                  raw_text=node.input_json.get("text", ""),
                                  source_asset_ids=node.input_json.get("asset_ids", []))
        db.add(requirement)
        await db.flush()
    requirement.extracted = result.structured
    requirement.status = "done"
    node.output_json = result.structured
    node.summary = result.text[:200]
    await _update_job(db, job, "SUCCEEDED", 1.0)
    node.status = "SUCCEEDED"
    await db.commit()


async def scene_executor(db: AsyncSession, node: WorkflowNode, job: ExecutionJob) -> None:
    if node.output_json:
        await _update_job(db, job, "SUCCEEDED", 1.0)
        return
    await _update_job(db, job, "RUNNING", 0.2)
    result = await run_ai("scene_recognition", node.degree, node.input_json)
    await _update_job(db, job, "RUNNING", 0.8)
    objects = result.structured.get("objects", [])
    node.output_json = result.structured
    node.summary = result.text[:200]
    await _update_job(db, job, "SUCCEEDED", 1.0)
    node.status = "SUCCEEDED"
    await db.commit()


EXECUTORS = {
    "requirement_analysis": requirement_executor,
    "scene_recognition": scene_executor,
}
```

- [ ] **Step 4: 写 analyze 路由**

```python
# backend/app/api/v1/analyze.py
from fastapi import APIRouter, Depends

from app.api.v1.deps import get_current_user
from app.api.v1.projects import _get_org_project
from app.db.session import SessionLocal
from app.models.org import User
from app.services.workflow_service import create_node

router = APIRouter()


@router.post("/analyze")
async def analyze_project(project_id: int, body: dict, user: User = Depends(get_current_user)):
    text = body.get("text", "")
    asset_ids = body.get("asset_ids", [])
    degree = body.get("degree", "STANDARD")
    async with SessionLocal() as db:
        await _get_org_project(db, project_id, user)
        req_node = await create_node(db, project_id, "requirement_analysis", degree,
                                     {"text": text, "asset_ids": asset_ids}, user.id)
        scene_node = await create_node(db, project_id, "scene_recognition", degree,
                                       {"text": text, "asset_ids": asset_ids}, user.id)
        from app.models.design import Requirement
        requirement = Requirement(project_id=project_id, raw_text=text, source_asset_ids=asset_ids)
        db.add(requirement)
        await db.flush()
        requirement_id = requirement.id
        await db.commit()
        return {"requirement_id": requirement_id, "nodes": [req_node.id, scene_node.id]}
```

- [ ] **Step 5: 挂载路由（带 body 参数校验 schema 简化为 dict）+ 需求查询接口**

```python
# router.py 追加
from app.api.v1 import auth, projects, assets, analyze
api_router.include_router(analyze.router, prefix="/projects/{project_id}", tags=["analyze"])


@router.get("/requirements/{requirement_id}")  # 加到 analyze.py
async def get_requirement(project_id: int, requirement_id: int, user: User = Depends(get_current_user)):
    from app.models.design import Requirement
    async with SessionLocal() as db:
        await _get_org_project(db, project_id, user)
        requirement = await db.get(Requirement, requirement_id)
        return {"id": requirement.id, "raw_text": requirement.raw_text,
                "extracted": requirement.extracted, "source_asset_ids": requirement.source_asset_ids}
```

- [ ] **Step 6: 跑测试**

```bash
DATABASE_URL=sqlite+aiosqlite:///:memory: pytest tests/test_analyze.py -v
```

Expected: 2 PASS。

- [ ] **Step 7: Commit**

```bash
git add -A && git commit -m "feat: 需求提取与场景识别执行器"
```

---

## Task 9: 方案生成（A/B/C 多方案 + 版本 + revise 分支）

**Files:**
- Modify: `backend/app/models/design.py`
- Create: `backend/app/services/scheme_service.py`
- Create: `backend/app/api/v1/schemes.py`
- Create: `backend/tests/test_schemes.py`

- [ ] **Step 1: 追加方案模型**

```python
# backend/app/models/design.py 追加
class DesignScheme(Base, TimestampMixin):
    __tablename__ = "design_schemes"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), index=True)
    name: Mapped[str] = mapped_column(String(120))
    status: Mapped[str] = mapped_column(String(20), default="generating")  # generating | ready | failed
    degree: Mapped[str] = mapped_column(String(10), default="STANDARD")
    current_version_id: Mapped[int] = mapped_column(default=0)


class DesignSchemeVersion(Base, TimestampMixin):
    __tablename__ = "design_scheme_versions"

    id: Mapped[int] = mapped_column(primary_key=True)
    scheme_id: Mapped[int] = mapped_column(ForeignKey("design_schemes.id"), index=True)
    version_no: Mapped[int] = mapped_column(default=1)
    title: Mapped[str] = mapped_column(String(120))
    description: Mapped[str] = mapped_column(Text, default="")
    materials: Mapped[list] = mapped_column(JSON, default=list)
    dimensions: Mapped[dict] = mapped_column(JSON, default=dict)
    craft: Mapped[str] = mapped_column(Text, default="")
    image_asset_id: Mapped[int | None] = mapped_column(ForeignKey("assets.id"), nullable=True)
    payload_json: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(20), default="ai_draft")  # ai_draft | confirmed | rejected
```

- [ ] **Step 2: 写失败的测试**

```python
# backend/tests/test_schemes.py
from httpx import ASGITransport, AsyncClient
from app.main import app


async def _client_with_project() -> tuple[AsyncClient, int]:
    client = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
    await client.post("/api/auth/register", json={"email": "s@b.com", "password": "secret123", "display_name": "S"})
    res = await client.post("/api/projects", json={"name": "方案项目"})
    return client, res.json()["id"]


async def test_generate_three_schemes():
    client, pid = await _client_with_project()
    res = await client.post(f"/api/projects/{pid}/schemes/generate",
                            json={"degree": "QUICK", "count": 3, "text": "北欧风白橡木餐椅"})
    assert res.status_code == 200
    data = res.json()
    assert len(data["scheme_ids"]) == 3
    assert len(data["node_ids"]) == 3


async def test_scheme_versions_and_revise():
    client, pid = await _client_with_project()
    res = await client.post(f"/api/projects/{pid}/schemes/generate",
                            json={"degree": "QUICK", "count": 1, "text": "一张书桌"})
    scheme_id = res.json()["scheme_ids"][0]
    listed = await client.get(f"/api/projects/{pid}/schemes")
    assert listed.status_code == 200
    scheme = next(s for s in listed.json() if s["id"] == scheme_id)
    assert scheme["versions"][0]["version_no"] == 1
    assert scheme["versions"][0]["status"] == "ai_draft"

    revised = await client.post(f"/api/projects/{pid}/schemes/{scheme_id}/revise", json={"instruction": "改成圆角桌面"})
    assert revised.status_code == 200
    revised_scheme = next(s for s in revised.json() if s["id"] == scheme_id)
    assert len(revised_scheme["versions"]) == 2
    assert revised_scheme["versions"][0]["version_no"] == 2  # 降序：V2 在前
```

- [ ] **Step 3: 写 scheme_service.py**

```python
# backend/app/services/scheme_service.py
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import SessionLocal
from app.models.design import DesignScheme, DesignSchemeVersion
from app.models.workflow import WorkflowNode
from app.services.workflow_service import create_node


async def generate_schemes(db: AsyncSession, project_id: int, degree: str, count: int,
                           text: str, asset_ids: list[int], user_id: int) -> dict:
    """为每个候选方案建 concept_generation 节点并同步执行（节点驱动，版本由 executor 创建）。"""
    count = max(1, min(count, 5))
    scheme_ids, node_ids = [], []
    for i in range(count):
        scheme = DesignScheme(project_id=project_id, name=f"方案 {i + 1}",
                              degree=degree, status="generating")
        db.add(scheme)
        await db.flush()
        node = await create_node(db, project_id, "concept_generation", degree,
                                 {"text": text, "asset_ids": asset_ids, "scheme_index": i}, user_id,
                                 scheme_id=scheme.id)
        scheme_ids.append(scheme.id)
        node_ids.append(node.id)
    await db.commit()
    # 同步执行：Phase 1 生成即出结果；节点/作业状态由 executor 写为 SUCCEEDED
    from app.models.workflow import ExecutionJob
    from app.services.executors import concept_executor
    for nid in node_ids:
        async with SessionLocal() as db2:
            node = await db2.get(WorkflowNode, nid)
            job = await db2.scalar(
                ExecutionJob.__table__.select().where(ExecutionJob.workflow_node_id == nid).limit(1))
            await concept_executor(db2, node, job)
    return {"scheme_ids": scheme_ids, "node_ids": node_ids}


async def list_schemes(db: AsyncSession, project_id: int) -> list[dict]:
    schemes = (await db.execute(
        DesignScheme.__table__.select().where(DesignScheme.project_id == project_id)
        .order_by(DesignScheme.id.desc()))).scalars().all()
    out = []
    for scheme in schemes:
        versions = (await db.execute(
            DesignSchemeVersion.__table__.select()
            .where(DesignSchemeVersion.scheme_id == scheme.id)
            .order_by(DesignSchemeVersion.version_no.desc()))).scalars().all()
        out.append({"id": scheme.id, "name": scheme.name, "status": scheme.status,
                    "degree": scheme.degree, "current_version_id": scheme.current_version_id,
                    "versions": [{"id": v.id, "version_no": v.version_no, "title": v.title,
                                  "description": v.description, "materials": v.materials,
                                  "dimensions": v.dimensions, "craft": v.craft,
                                  "status": v.status, "image_asset_id": v.image_asset_id}
                                 for v in versions]})
    return out


async def revise_scheme(db: AsyncSession, scheme_id: int, instruction: str, user_id: int) -> DesignScheme:
    scheme = await db.get(DesignScheme, scheme_id)
    if not scheme:
        raise HTTPException(404, "scheme_not_found")
    # 版本分支：从最新 concept 节点创建 revision 分支，inline 执行出 V2
    parent_node = await db.scalar(
        WorkflowNode.__table__.select()
        .where(WorkflowNode.scheme_id == scheme_id, WorkflowNode.type == "concept_generation")
        .order_by(WorkflowNode.id.desc()).limit(1))
    if not parent_node:
        raise HTTPException(409, "scheme_has_no_concept_node")
    from app.models.workflow import ExecutionJob
    from app.services.executors import concept_executor
    from app.services.workflow_service import branch_node
    child = await branch_node(db, parent_node.id, "revision", user_id,
                              {"instruction": instruction}, degree=scheme.degree)
    await db.commit()
    async with SessionLocal() as db2:
        node = await db2.get(WorkflowNode, child.id)
        job = await db2.scalar(
            ExecutionJob.__table__.select().where(ExecutionJob.workflow_node_id == child.id).limit(1))
        await concept_executor(db2, node, job)
    return scheme
```

- [ ] **Step 4: 写 schemes 路由**

```python
# backend/app/api/v1/schemes.py
from fastapi import APIRouter, Depends

from app.api.v1.deps import get_current_user
from app.api.v1.projects import _get_org_project
from app.db.session import SessionLocal
from app.models.org import User
from app.services.scheme_service import generate_schemes, list_schemes, revise_scheme

router = APIRouter()


@router.post("/generate")
async def generate(project_id: int, body: dict, user: User = Depends(get_current_user)):
    async with SessionLocal() as db:
        await _get_org_project(db, project_id, user)
        return await generate_schemes(db, project_id, body.get("degree", "STANDARD"),
                                      body.get("count", 3), body.get("text", ""),
                                      body.get("asset_ids", []), user.id)


@router.get("")
async def list_schemes_api(project_id: int, user: User = Depends(get_current_user)):
    async with SessionLocal() as db:
        await _get_org_project(db, project_id, user)
        return await list_schemes(db, project_id)


@router.post("/{scheme_id}/revise")
async def revise(project_id: int, scheme_id: int, body: dict, user: User = Depends(get_current_user)):
    async with SessionLocal() as db:
        await _get_org_project(db, project_id, user)
        scheme = await revise_scheme(db, scheme_id, body.get("instruction", ""), user.id)
        return await list_schemes(db, scheme.project_id)
```

- [ ] **Step 5: 挂载路由**

```python
# router.py 追加
from app.api.v1 import auth, projects, assets, analyze, schemes
api_router.include_router(schemes.router, prefix="/projects/{project_id}/schemes", tags=["schemes"])
```

- [ ] **Step 6: 跑测试**

```bash
DATABASE_URL=sqlite+aiosqlite:///:memory: pytest tests/test_schemes.py -v
```

Expected: 2 PASS。

- [ ] **Step 7: Commit**

```bash
git add -A && git commit -m "feat: A/B/C 多方案生成与版本分支"
```

---

## Task 10: 渲染执行器（ComfyUI 封装 + 本地占位兜底）

**Files:**
- Create: `backend/app/services/comfyui_service.py`
- Create: `backend/tests/test_comfyui_service.py`

- [ ] **Step 1: 写失败的测试**

```python
# backend/tests/test_comfyui_service.py
from app.services.comfyui_service import build_render_payload, is_comfyui_configured


def test_build_payload_uses_degree():
    payload = build_render_payload("STANDARD", {"title": "北欧餐椅", "prompt": "x"})
    assert payload["width"] == 1024
    assert payload["steps"] == 30


def test_not_configured_returns_false():
    assert is_comfyui_configured() is False  # 默认配置无 endpoint
```

- [ ] **Step 2: 写 comfyui_service.py（参考现有 comfyui_image_service.py 的调用模式，仅保留队列提交+轮询）**

```python
# backend/app/services/comfyui_service.py
import asyncio
import json

import httpx

from app.config.settings import settings

_DEGREE_STEPS = {"QUICK": 20, "STANDARD": 30, "DEEP": 50}
_DEGREE_SIZES = {"QUICK": (768, 768), "STANDARD": (1024, 1024), "DEEP": (1344, 1344)}


def is_comfyui_configured() -> bool:
    return bool(settings.comfyui_endpoint)


def build_render_payload(degree: str, inputs: dict) -> dict:
    steps = _DEGREE_STEPS.get(degree, 30)
    width, height = _DEGREE_SIZES.get(degree, (1024, 1024))
    prompt = inputs.get("prompt") or f"{inputs.get('title', '产品')}效果图"
    return {"prompt": prompt, "negative_prompt": "low quality, blurry, watermark",
            "steps": steps, "width": width, "height": height, "seed": inputs.get("seed", 0)}


async def submit_render(payload: dict) -> str:
    """提交渲染任务到 ComfyUI /prompt，返回 prompt_id。"""
    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.post(
            f"{settings.comfyui_endpoint.rstrip('/')}/prompt",
            json={"prompt": {"workflow": payload}},  # 简化：真实环境需完整 workflow 模板
        )
        resp.raise_for_status()
        return resp.json()["prompt_id"]


async def poll_render(prompt_id: str, timeout_seconds: float = 600.0) -> dict:
    """轮询 ComfyUI /history 直到任务完成，返回 images 列表。"""
    async with httpx.AsyncClient(timeout=60) as client:
        deadline = asyncio.get_event_loop().time() + timeout_seconds
        while asyncio.get_event_loop().time() < deadline:
            resp = await client.get(f"{settings.comfyui_endpoint.rstrip('/')}/history/{prompt_id}")
            resp.raise_for_status()
            history = resp.json()
            if prompt_id in history:
                outputs = history[prompt_id].get("outputs", {})
                images = []
                for node_out in outputs.values():
                    for img in node_out.get("images", []):
                        images.append({"filename": img["filename"], "subfolder": img.get("subfolder", ""),
                                       "type": img.get("type", "output")})
                if images:
                    return {"images": images}
            await asyncio.sleep(3)
        raise TimeoutError("render_timeout")


async def download_image(meta: dict) -> bytes:
    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.get(
            f"{settings.comfyui_endpoint.rstrip('/')}/view",
            params={"filename": meta["filename"], "subfolder": meta.get("subfolder", ""),
                    "type": meta.get("type", "output")},
        )
        resp.raise_for_status()
        return resp.content
```

- [ ] **Step 3: 在 executors.py 追加 rendering executor（ComfyUI 未配置 → 本地占位 SVG 资产）**

```python
# backend/app/services/executors.py 追加
from app.services.comfyui_service import (build_render_payload, download_image,
                                          is_comfyui_configured, poll_render, submit_render)
from app.services.asset_service import make_key, put_object
from app.models.asset import Asset, AssetVersion


async def rendering_executor(db: AsyncSession, node: WorkflowNode, job: ExecutionJob) -> None:
    from app.models.design import DesignScheme, DesignSchemeVersion
    from app.services.ai_gateway_service import run_ai
    await _update_job(db, job, "RUNNING", 0.1)
    scheme_id = node.scheme_id
    version = None
    if scheme_id:
        version = (await db.execute(
            DesignSchemeVersion.__table__.select()
            .where(DesignSchemeVersion.scheme_id == scheme_id)
            .order_by(DesignSchemeVersion.version_no.desc()).limit(1))).scalar_one_or_none()
    inputs = {"title": version.title if version else "产品", "text": node.input_json.get("text", "")}
    if is_comfyui_configured():
        await _update_job(db, job, "RUNNING", 0.3)
        payload = build_render_payload(node.degree, inputs)
        prompt_id = await submit_render(payload)
        await _update_job(db, job, "RUNNING", 0.6)
        result = await poll_render(prompt_id)
        image_meta = result["images"][0]
        data = await download_image(image_meta)
        ext = image_meta["filename"].rsplit(".", 1)[-1]
    else:
        await _update_job(db, job, "RUNNING", 0.5)
        svg = (f'<svg xmlns="http://www.w3.org/2000/svg" width="800" height="600">'
               f'<rect width="800" height="600" fill="#f5f5f4"/>'
               f'<text x="400" y="290" text-anchor="middle" font-size="28" fill="#444">{inputs["title"]}</text>'
               f'<text x="400" y="330" text-anchor="middle" font-size="16" fill="#888">AI 生成草案（本地占位，未配置渲染服务）</text>'
               f'</svg>')
        data = svg.encode()
        ext = "svg"
    asset = Asset(project_id=node.project_id, kind="image", original_name=f"render_v{version.version_no if version else 1}.{ext}",
                  mime_type="image/svg+xml" if ext == "svg" else "image/png", size_bytes=len(data), created_by=node.created_by)
    db.add(asset)
    await db.flush()
    key = make_key(node.project_id, asset.id, 1, ext)
    db.add(AssetVersion(asset_id=asset.id, version_no=1, storage_key=key))
    asset.current_version_id = asset.id
    if version:
        version.image_asset_id = asset.id
        version.status = "ai_draft"
    node.output_json = {"image_asset_id": asset.id}
    node.summary = f"渲染完成：{inputs['title']}"
    await put_object(key, data, asset.mime_type)
    await _update_job(db, job, "SUCCEEDED", 1.0)
    node.status = "SUCCEEDED"
    await db.commit()


EXECUTORS = {
    "requirement_analysis": requirement_executor,
    "scene_recognition": scene_executor,
    "concept_generation": concept_executor,
    "rendering": rendering_executor,
}
```

- [ ] **Step 4: 补 concept_executor（幂等 + 版本号取 latest+1 + 支持修订分支）**

```python
# backend/app/services/executors.py 追加
async def concept_executor(db: AsyncSession, node: WorkflowNode, job: ExecutionJob) -> None:
    if node.output_json:  # 幂等：分支节点是新节点，原节点不会重复执行
        await _update_job(db, job, "SUCCEEDED", 1.0)
        return
    from app.models.design import DesignScheme, DesignSchemeVersion
    await _update_job(db, job, "RUNNING", 0.2)
    result = await run_ai("concept_generation", node.degree, node.input_json)
    concepts = result.structured.get("concepts", [])
    scheme = await db.get(DesignScheme, node.scheme_id) if node.scheme_id else None
    if scheme and concepts:
        latest = await db.scalar(
            DesignSchemeVersion.__table__.select()
            .where(DesignSchemeVersion.scheme_id == scheme.id)
            .order_by(DesignSchemeVersion.version_no.desc()).limit(1))
        version_no = (latest.version_no + 1) if latest else 1
        concept = concepts[node.input_json.get("scheme_index", 0) % len(concepts)]
        version = DesignSchemeVersion(scheme_id=scheme.id, version_no=version_no, title=concept["title"],
                                      description=concept.get("summary", ""),
                                      materials=concept.get("materials", []),
                                      dimensions=concept.get("dimensions", {}),
                                      craft=concept.get("craft", ""), payload_json=concept,
                                      status="ai_draft")
        db.add(version)
        await db.flush()
        scheme.current_version_id = version.id
        scheme.status = "ready"
    node.output_json = result.structured
    node.summary = result.text[:200]
    await _update_job(db, job, "SUCCEEDED", 1.0)
    node.status = "SUCCEEDED"
    await db.commit()
```

- [ ] **Step 5: 跑测试**

```bash
DATABASE_URL=sqlite+aiosqlite:///:memory: pytest tests/test_comfyui_service.py -v
```

Expected: 2 PASS。

- [ ] **Step 6: Commit**

```bash
git add -A && git commit -m "feat: ComfyUI 渲染封装与本地占位兜底"
```

---

## Task 11: arq Worker + 任务执行循环 + 失败重试

**Files:**
- Create: `backend/app/worker.py`
- Modify: `backend/app/services/executors.py`（如需要）
- Create: `backend/tests/test_worker_flow.py`

- [ ] **Step 1: 写失败的测试（执行器分发逻辑）**

```python
# backend/tests/test_worker_flow.py
import pytest

from app.services.executors import EXECUTORS, dispatch
from app.services.workflow_service import STATUS_FLOW


async def test_all_node_types_have_executors():
    assert "requirement_analysis" in EXECUTORS
    assert "scene_recognition" in EXECUTORS
    assert "concept_generation" in EXECUTORS
    assert "rendering" in EXECUTORS


def test_status_flow_covers_all_required_states():
    states = set(STATUS_FLOW.keys())
    required = {"DRAFT", "WAITING_INPUT", "WAITING_AUTH", "QUEUED", "RUNNING",
                "SUCCEEDED", "FAILED", "CANCELLED", "NEEDS_REVIEW"}
    assert required.issubset(states)


async def test_failed_job_can_retry():
    from app.models.workflow import ExecutionJob
    from app.services.workflow_service import create_node, start_node
    from app.db.session import SessionLocal
    from app.models.org import Organization, User
    from app.models.project import Project

    async with SessionLocal() as db:
        org = Organization(name="wf-org")
        db.add(org)
        await db.flush()
        user = User(email="wf@t.com", password_hash="x", display_name="W", organization_id=org.id, role="admin")
        db.add(user)
        await db.flush()
        project = Project(organization_id=org.id, name="p", created_by=user.id)
        db.add(project)
        await db.commit()
        node = await create_node(db, project.id, "requirement_analysis", "QUICK", {}, user.id)
        job = await db.scalar(ExecutionJob.__table__.select().where(ExecutionJob.workflow_node_id == node.id))
        job.status = "FAILED"
        job.error_message = "boom"
        await db.commit()
        await dispatch(db, node.id)
        await db.refresh(node)
        assert node.status == "QUEUED" or node.status == "SUCCEEDED"
        job = await db.scalar(ExecutionJob.__table__.select().where(ExecutionJob.workflow_node_id == node.id))
        assert job.attempts == 1
```

- [ ] **Step 2: 写 dispatch 与 worker.py**

```python
# backend/app/services/executors.py 追加
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.workflow import ExecutionJob, WorkflowNode


async def dispatch(db: AsyncSession, node_id: int) -> None:
    """从 FAILED 重试或从 QUEUED 启动。失败重试次数 < 3。"""
    node = await db.get(WorkflowNode, node_id)
    job = await db.scalar(ExecutionJob.__table__.select().where(ExecutionJob.workflow_node_id == node_id).limit(1))
    if not node or not job:
        return
    if job.status == "FAILED":
        if job.attempts >= 3:
            return
        job.attempts += 1
        job.error_message = ""
        node.status = "QUEUED"
        job.status = "QUEUED"
        await db.commit()
    if node.status not in ("QUEUED", "RUNNING"):
        return
    executor = EXECUTORS.get(node.type)
    if not executor:
        job.status = "FAILED"
        job.error_message = f"no_executor:{node.type}"
        node.status = "FAILED"
        await db.commit()
        return
    try:
        await executor(db, node, job)
    except Exception as exc:  # noqa: BLE001
        node.status = "FAILED"
        job.status = "FAILED"
        job.error_message = str(exc)[:500]
        await db.commit()
```

```python
# backend/app/worker.py
from arq import cron  # noqa: F401
from arq.connections import RedisSettings
from sqlalchemy.ext.asyncio import AsyncSession

from app.config.settings import settings
from app.db.session import SessionLocal
from app.models.workflow import ExecutionJob, WorkflowNode
from app.services.executors import dispatch


async def run_job(ctx: dict, node_id: int) -> None:
    async with SessionLocal() as db:
        await dispatch(db, node_id)


async def enqueue(node_id: int) -> None:
    from arq import create_pool
    pool = await create_pool(RedisSettings.from_dsn(settings.redis_url))
    await pool.enqueue_job("run_job", node_id)


class WorkerSettings:
    functions = [run_job]
    redis_settings = RedisSettings.from_dsn(settings.redis_url)
    max_jobs = 8
    job_timeout = 1800
```

- [ ] **Step 3: 在 analyze/schemes API 里入队（enqueue 失败不阻塞接口）**

```python
# backend/app/api/v1/analyze.py 追加 import 与调用
from app.worker import enqueue
# analyze 创建节点后：
for nid in (req_node.id, scene_node.id):
    try:
        await enqueue(nid)
    except Exception:
        pass  # 本地开发无 Redis 时静默，任务靠手动触发
```

```python
# backend/app/api/v1/schemes.py 追加
from app.worker import enqueue
# generate 返回前：
for nid in result["node_ids"]:
    try:
        await enqueue(nid)
    except Exception:
        pass
```

- [ ] **Step 4: 跑测试**

```bash
DATABASE_URL=sqlite+aiosqlite:///:memory: pytest tests/test_worker_flow.py -v
```

Expected: 3 PASS。

- [ ] **Step 5: 启动 worker 冒烟（Redis 在 docker compose 里）**

```bash
cd /Users/pipi/CodeSpace/ai-codesign-studio/backend
source .venv/bin/activate
python -m arq app.worker.WorkerSettings &
curl -s http://localhost:8000/api/health && kill %1
```

- [ ] **Step 6: Commit**

```bash
git add -A && git commit -m "feat: arq worker 任务执行循环与失败重试"
```

---

## Task 12: 工作流状态 API + 审批中心 API + SSE 进度

**Files:**
- Create: `backend/app/api/v1/workflows.py`
- Create: `backend/app/api/v1/jobs.py`
- Create: `backend/tests/test_workflow_api.py`

- [ ] **Step 1: 写失败的测试**

```python
# backend/tests/test_workflow_api.py
from httpx import ASGITransport, AsyncClient
from app.main import app


async def _client_with_project() -> tuple[AsyncClient, int]:
    client = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
    await client.post("/api/auth/register", json={"email": "wf@b.com", "password": "secret123", "display_name": "W"})
    res = await client.post("/api/projects", json={"name": "wf 项目"})
    return client, res.json()["id"]


async def test_workflow_status_endpoint():
    client, pid = await _client_with_project()
    res = await client.post(f"/api/projects/{pid}/analyze", json={"text": "一张餐桌"})
    nodes = res.json()["nodes"]
    status = await client.get(f"/api/projects/{pid}/workflows/status")
    assert status.status_code == 200
    assert len(status.json()["nodes"]) >= 2


async def test_jobs_center_lists_waiting_auth():
    client, pid = await _client_with_project()
    jobs = await client.get("/api/jobs?status=WAITING_AUTH")
    assert jobs.status_code == 200
    assert isinstance(jobs.json(), list)


async def test_authorize_endpoint():
    client, pid = await _client_with_project()
    await client.post(f"/api/projects/{pid}/analyze", json={"text": "一张餐桌"})
    jobs = (await client.get("/api/jobs?status=WAITING_AUTH")).json()
    if jobs:
        res = await client.post(f"/api/jobs/{jobs[0]['id']}/authorize", json={"approve": True, "reason": "ok"})
        assert res.status_code == 200
```

- [ ] **Step 2: 写 workflows.py**

```python
# backend/app/api/v1/workflows.py
from fastapi import APIRouter, Depends

from app.api.v1.deps import get_current_user
from app.api.v1.projects import _get_org_project
from app.db.session import SessionLocal
from app.models.org import User
from app.models.workflow import ExecutionJob, WorkflowEdge, WorkflowNode

router = APIRouter()


@router.get("/workflows/status")
async def workflow_status(project_id: int, user: User = Depends(get_current_user)):
    async with SessionLocal() as db:
        await _get_org_project(db, project_id, user)
        nodes = (await db.execute(
            WorkflowNode.__table__.select().where(WorkflowNode.project_id == project_id)
            .order_by(WorkflowNode.id.asc()))).scalars().all()
        edges = (await db.execute(
            WorkflowEdge.__table__.select()
            .join(WorkflowNode, WorkflowEdge.from_node_id == WorkflowNode.id)
            .where(WorkflowNode.project_id == project_id))).scalars().all()
        node_ids = [n.id for n in nodes]
        jobs = {}
        if node_ids:
            rows = await db.execute(
                ExecutionJob.__table__.select()
                .where(ExecutionJob.workflow_node_id.in_(node_ids)))
            for job in rows.scalars().all():
                jobs[job.workflow_node_id] = job
        return {"nodes": [
            {"id": n.id, "type": n.type, "status": n.status, "degree": n.degree,
             "summary": n.summary, "branch_of": n.branch_of, "branch_type": n.branch_type,
             "scheme_id": n.scheme_id, "created_at": str(n.created_at),
             "job": {"id": jobs[n.id].id, "status": jobs[n.id].status,
                     "progress": jobs[n.id].progress, "auth_required": jobs[n.id].auth_required}
             if n.id in jobs else None}
            for n in nodes],
            "edges": [{"from": e.from_node_id, "to": e.to_node_id, "kind": e.kind}
                      for e in edges]}
```

- [ ] **Step 3: 写 jobs.py（审批中心 + 授权 + 任务重试）**

```python
# backend/app/api/v1/jobs.py
from fastapi import APIRouter, Depends, HTTPException

from app.api.v1.deps import get_current_user
from app.db.session import SessionLocal
from app.models.org import User
from app.models.workflow import ExecutionJob, WorkflowNode
from app.services.workflow_service import decide_authorization

router = APIRouter()


@router.get("/jobs")
async def list_jobs(status: str | None = None, user: User = Depends(get_current_user)):
    async with SessionLocal() as db:
        stmt = ExecutionJob.__table__.select().order_by(ExecutionJob.id.desc()).limit(200)
        if status:
            stmt = stmt.where(ExecutionJob.status == status)
        rows = await db.execute(stmt)
        jobs = rows.scalars().all()
        out = []
        for job in jobs:
            node = await db.get(WorkflowNode, job.workflow_node_id)
            out.append({
                "id": job.id, "node_id": job.workflow_node_id, "task_type": job.task_type,
                "status": job.status, "progress": job.progress,
                "error_message": job.error_message, "attempts": job.attempts,
                "auth_required": job.auth_required, "created_at": str(job.created_at),
                "node_type": node.type if node else None, "node_status": node.status if node else None,
                "project_id": node.project_id if node else None,
            })
        return out


@router.get("/jobs/{job_id}")
async def job_detail(job_id: int, user: User = Depends(get_current_user)):
    async with SessionLocal() as db:
        job = await db.get(ExecutionJob, job_id)
        if not job:
            raise HTTPException(404, "job_not_found")
        # 规格书：内部路由只对管理员可见
        if user.role == "admin":
            internal = job.internal_route_json
        else:
            internal = {"restricted": True}
        return {"id": job.id, "status": job.status, "progress": job.progress,
                "task_type": job.task_type, "error_message": job.error_message,
                "internal_route": internal, "external_api_used": job.external_api_used,
                "created_at": str(job.created_at)}


@router.post("/jobs/{job_id}/authorize")
async def authorize(job_id: int, body: dict, user: User = Depends(get_current_user)):
    async with SessionLocal() as db:
        job = await db.get(ExecutionJob, job_id)
        if not job:
            raise HTTPException(404, "job_not_found")
        record = await decide_authorization(db, job.workflow_node_id, user.id,
                                            body.get("approve", False), body.get("reason", ""))
        return {"id": record.id, "action": record.action, "node_id": record.workflow_node_id}


@router.post("/jobs/{job_id}/retry")
async def retry_job(job_id: int, user: User = Depends(get_current_user)):
    from app.services.executors import dispatch
    async with SessionLocal() as db:
        job = await db.get(ExecutionJob, job_id)
        if not job:
            raise HTTPException(404, "job_not_found")
        await dispatch(db, job.workflow_node_id)
        return {"id": job.id, "status": "retried"}
```

- [ ] **Step 4: 挂载并跑测试**

```python
# router.py 追加
from app.api.v1 import auth, projects, assets, analyze, schemes, workflows, jobs
api_router.include_router(workflows.router, prefix="/projects/{project_id}", tags=["workflows"])
api_router.include_router(jobs.router, prefix="", tags=["jobs"])
```

```bash
DATABASE_URL=sqlite+aiosqlite:///:memory: pytest tests/test_workflow_api.py -v
```

Expected: 3 PASS。

- [ ] **Step 5: Commit**

```bash
git add -A && git commit -m "feat: 工作流状态/审批中心/任务重试 API"
```

---

## Task 13: 前端脚手架（Next.js 15 + shadcn/ui + 布局导航）

**Files:**
- Create: `frontend/`（脚手架生成）
- Create: `frontend/src/lib/api.ts`
- Create: `frontend/src/lib/types.ts`
- Create: `frontend/src/middleware.ts`
- Create: `frontend/src/app/(app)/layout.tsx`
- Create: `frontend/src/components/app-sidebar.tsx`
- Create: `frontend/src/components/ui/*`（shadcn 生成）

- [ ] **Step 1: 创建 Next.js 项目**

```bash
cd /Users/pipi/CodeSpace/ai-codesign-studio
npx create-next-app@latest frontend --typescript --tailwind --eslint --app --use-npm --yes
cd frontend && npx shadcn@latest init -d
npx shadcn@latest add button card input textarea dialog select badge avatar tabs separator sonner skeleton
```

- [ ] **Step 2: 写 types.ts**

```ts
// frontend/src/lib/types.ts
export type Degree = "QUICK" | "STANDARD" | "DEEP";

export type NodeStatus =
  | "DRAFT" | "WAITING_INPUT" | "WAITING_AUTH" | "QUEUED" | "RUNNING"
  | "SUCCEEDED" | "FAILED" | "CANCELLED" | "NEEDS_REVIEW";

export interface User { id: number; email: string; display_name: string; role: string; organization_id: number; }
export interface Project { id: number; name: string; description: string; status: string; created_at: string; }
export interface Asset { id: number; kind: string; original_name: string; size_bytes: number; created_at: string; }
export interface Job {
  id: number; node_id: number; task_type: string; status: string; progress: number;
  error_message: string; auth_required: boolean; node_type: string | null;
  node_status: string | null; project_id: number | null;
}
export interface WorkflowNode {
  id: number; type: string; status: NodeStatus; degree: Degree; summary: string | null;
  scheme_id: number | null; branch_of: number | null; branch_type: string;
  job: { id: number; status: string; progress: number; auth_required: boolean } | null;
}
export interface WorkflowStatus { nodes: WorkflowNode[]; edges: { from: number; to: number; kind: string }[]; }
export interface SchemeVersion {
  id: number; version_no: number; title: string; description: string;
  materials: string[]; dimensions: Record<string, number>; craft: string; status: string;
}
export interface Scheme { id: number; name: string; status: string; degree: Degree; versions: SchemeVersion[]; }
```

- [ ] **Step 3: 写 api.ts（带 JWT cookie）**

```ts
// frontend/src/lib/api.ts
import type { Asset, Job, Project, Scheme, User, WorkflowStatus } from "./types";

const BASE = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000/api";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    ...init,
    credentials: "include",
    headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
  });
  if (!res.ok) {
    const body = await res.json().catch(() => null);
    throw new Error(body?.detail ?? `HTTP ${res.status}`);
  }
  return res.json() as Promise<T>;
}

export const Api = {
  register: (body: { email: string; password: string; display_name: string }) =>
    request<User>("/auth/register", { method: "POST", body: JSON.stringify(body) }),
  login: (body: { email: string; password: string }) =>
    request<User>("/auth/login", { method: "POST", body: JSON.stringify(body) }),
  me: () => request<User>("/auth/me"),
  listProjects: () => request<Project[]>("/projects"),
  createProject: (body: { name: string; description: string }) =>
    request<Project>("/projects", { method: "POST", body: JSON.stringify(body) }),
  listAssets: (projectId: number) => request<Asset[]>(`/projects/${projectId}/assets`),
  uploadAsset: async (projectId: number, file: File) => {
    const fd = new FormData();
    fd.append("file", file);
    const res = await fetch(`${BASE}/projects/${projectId}/assets`, {
      method: "POST", body: fd, credentials: "include",
    });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    return res.json() as Promise<Asset>;
  },
  analyze: (projectId: number, body: { text: string; asset_ids: number[]; degree: Degree }) =>
    request<{ requirement_id: number; nodes: number[] }>(`/projects/${projectId}/analyze`, {
      method: "POST", body: JSON.stringify(body),
    }),
  generateSchemes: (projectId: number, body: { degree: Degree; count: number; text: string; asset_ids: number[] }) =>
    request<{ scheme_ids: number[]; node_ids: number[] }>(`/projects/${projectId}/schemes/generate`, {
      method: "POST", body: JSON.stringify(body),
    }),
  listSchemes: (projectId: number) => request<Scheme[]>(`/projects/${projectId}/schemes`),
  reviseScheme: (projectId: number, schemeId: number, instruction: string) =>
    request<Scheme[]>(`/projects/${projectId}/schemes/${schemeId}/revise`, {
      method: "POST", body: JSON.stringify({ instruction }),
    }),
  workflowStatus: (projectId: number) => request<WorkflowStatus>(`/projects/${projectId}/workflows/status`),
  listJobs: (status?: string) => request<Job[]>(`/jobs${status ? `?status=${status}` : ""}`),
  authorizeJob: (jobId: number, approve: boolean, reason: string) =>
    request<{ id: number; action: string }>(`/jobs/${jobId}/authorize`, {
      method: "POST", body: JSON.stringify({ approve, reason }),
    }),
  retryJob: (jobId: number) => request<{ id: number }>(`/jobs/${jobId}/retry`, { method: "POST" }),
};
```

- [ ] **Step 4: 写 middleware.ts（路由保护）**

```ts
// frontend/src/middleware.ts
import { NextResponse } from "next/server";
import type { NextRequest } from "next/server";

export function middleware(req: NextRequest) {
  const token = req.cookies.get("access_token");
  const { pathname } = req.nextUrl;
  const authPaths = ["/login", "/register"];
  if (!token && !authPaths.includes(pathname)) {
    return NextResponse.redirect(new URL("/login", req.url));
  }
  if (token && authPaths.includes(pathname)) {
    return NextResponse.redirect(new URL("/projects", req.url));
  }
  return NextResponse.next();
}

export const config = { matcher: ["/((?!_next|api|.*\\..*).*)"] };
```

- [ ] **Step 5: 写 app-sidebar + (app) 布局（导航合并：工作台/项目/任务审批/组织）**

```tsx
// frontend/src/components/app-sidebar.tsx
"use client";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { FolderKanban, LayoutGrid, ShieldCheck, Building2, GitBranch } from "lucide-react";
import { cn } from "@/lib/utils";

const NAV = [
  { href: "/projects", label: "项目中心", icon: FolderKanban },
  { href: "/tasks", label: "任务与审批", icon: ShieldCheck },
  { href: "/org", label: "组织设置", icon: Building2 },
];

export function AppSidebar() {
  const pathname = usePathname();
  return (
    <aside className="flex h-full w-56 flex-col border-r bg-muted/30 px-3 py-4">
      <div className="mb-6 flex items-center gap-2 px-2">
        <GitBranch className="h-5 w-5 text-primary" />
        <span className="text-sm font-semibold">AI CoDesign Studio</span>
      </div>
      <nav className="flex flex-col gap-1">
        {NAV.map((item) => {
          const active = pathname.startsWith(item.href);
          return (
            <Link key={item.href} href={item.href}
              className={cn("flex items-center gap-2 rounded-md px-3 py-2 text-sm text-muted-foreground hover:bg-muted hover:text-foreground",
                active && "bg-primary/10 text-primary font-medium")}>
              <item.icon className="h-4 w-4" />
              {item.label}
            </Link>
          );
        })}
      </nav>
    </aside>
  );
}
```

```tsx
// frontend/src/app/(app)/layout.tsx
import { AppSidebar } from "@/components/app-sidebar";

export default function AppLayout({ children }: { children: React.ReactNode }) {
  return (
    <div className="flex h-screen">
      <AppSidebar />
      <main className="flex-1 overflow-auto">{children}</main>
    </div>
  );
}
```

- [ ] **Step 6: 启动验证**

```bash
cd /Users/pipi/CodeSpace/ai-codesign-studio/frontend
npm run dev
# 访问 http://localhost:3000/login，预期被 middleware 放行；访问 /projects 预期重定向到 /login
```

Expected: 页面能渲染，路由保护生效（未登录访问 /projects 会跳 /login）。

- [ ] **Step 7: Commit**

```bash
git add -A && git commit -m "feat: Next.js 15 前端骨架与布局导航"
```

---

## Task 14: 前端页面（登录/项目中心/对话工作台）

**Files:**
- Create: `frontend/src/app/(auth)/login/page.tsx`
- Create: `frontend/src/app/(auth)/register/page.tsx`
- Create: `frontend/src/app/(app)/projects/page.tsx`
- Create: `frontend/src/app/(app)/projects/[id]/page.tsx`
- Create: `frontend/src/components/chat/chat-panel.tsx`
- Create: `frontend/src/components/workflow/workflow-grid.tsx`
- Create: `frontend/src/components/workflow/degree-selector.tsx`
- Create: `frontend/src/components/workflow/status-meta.ts`
- Create: `frontend/src/components/schemes/scheme-card.tsx`
- Create: `frontend/vitest.config.ts` + `frontend/src/lib/api.test.ts`

- [ ] **Step 1: 写前端单元测试（api client + status 映射）**

```ts
// frontend/src/lib/status-meta.ts
import type { NodeStatus } from "@/lib/types";

export const STATUS_META: Record<NodeStatus, { label: string; className: string }> = {
  DRAFT: { label: "草稿", className: "bg-muted text-muted-foreground" },
  WAITING_INPUT: { label: "等待补充", className: "bg-amber-100 text-amber-800" },
  WAITING_AUTH: { label: "等待授权", className: "bg-orange-100 text-orange-800" },
  QUEUED: { label: "排队中", className: "bg-blue-100 text-blue-800" },
  RUNNING: { label: "执行中", className: "bg-blue-100 text-blue-800" },
  SUCCEEDED: { label: "已完成", className: "bg-green-100 text-green-800" },
  FAILED: { label: "失败", className: "bg-red-100 text-red-800" },
  CANCELLED: { label: "已取消", className: "bg-muted text-muted-foreground" },
  NEEDS_REVIEW: { label: "待人工审核", className: "bg-purple-100 text-purple-800" },
};

export const NODE_TYPE_LABEL: Record<string, string> = {
  requirement_analysis: "需求分析",
  scene_recognition: "场景识别",
  concept_generation: "概念方案",
  rendering: "效果图",
};
```

```ts
// frontend/src/lib/status-meta.test.ts
import { describe, expect, it } from "vitest";
import { STATUS_META, NODE_TYPE_LABEL } from "./status-meta";

describe("status meta", () => {
  it("covers all 9 node statuses", () => {
    expect(Object.keys(STATUS_META)).toHaveLength(9);
  });
  it("maps node types to Chinese labels", () => {
    expect(NODE_TYPE_LABEL.concept_generation).toBe("概念方案");
  });
});
```

```bash
cd /Users/pipi/CodeSpace/ai-codesign-studio/frontend
npm i -D vitest
npx vitest run src/lib/status-meta.test.ts
```

Expected: 1 PASS。

- [ ] **Step 2: 登录/注册页（React Hook Form 免装，用受控组件）**

```tsx
// frontend/src/app/(auth)/login/page.tsx
"use client";
import { useState } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import { Api } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/card";

export default function LoginPage() {
  const router = useRouter();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    try {
      await Api.login({ email, password });
      router.push("/projects");
    } catch (err) {
      setError(err instanceof Error ? err.message : "登录失败");
    }
  }

  return (
    <div className="flex h-screen items-center justify-center bg-muted/30">
      <Card className="w-96">
        <CardHeader>
          <CardTitle>登录 AI CoDesign Studio</CardTitle>
          <CardDescription>工业设计智能体平台</CardDescription>
        </CardHeader>
        <CardContent>
          <form onSubmit={onSubmit} className="flex flex-col gap-3">
            <Input type="email" placeholder="邮箱" value={email}
                   onChange={(e) => setEmail(e.target.value)} required />
            <Input type="password" placeholder="密码" value={password}
                   onChange={(e) => setPassword(e.target.value)} required />
            {error && <p className="text-sm text-red-600">{error}</p>}
            <Button type="submit">登录</Button>
            <Link href="/register" className="text-sm text-muted-foreground hover:underline">
              没有账号？注册
            </Link>
          </form>
        </CardContent>
      </Card>
    </div>
  );
}
```

（register 页同构，调用 `Api.register` 后 `router.push("/projects")`。）

- [ ] **Step 3: 项目中心页（卡片网格 + 新建对话框）**

```tsx
// frontend/src/app/(app)/projects/page.tsx
"use client";
import { useEffect, useState } from "react";
import Link from "next/link";
import { Api } from "@/lib/api";
import type { Project } from "@/lib/types";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Card } from "@/components/ui/card";
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogTrigger } from "@/components/ui/dialog";
import { Plus, FolderKanban } from "lucide-react";

export default function ProjectsPage() {
  const [projects, setProjects] = useState<Project[]>([]);
  const [name, setName] = useState("");
  const [desc, setDesc] = useState("");

  async function refresh() {
    setProjects(await Api.listProjects());
  }

  async function create() {
    await Api.createProject({ name, description: desc });
    setName(""); setDesc("");
    await refresh();
  }

  useEffect(() => { refresh().catch(console.error); }, []);

  return (
    <div className="p-6">
      <div className="mb-6 flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-semibold">项目中心</h1>
          <p className="text-sm text-muted-foreground">管理你的设计项目</p>
        </div>
        <Dialog>
          <DialogTrigger asChild>
            <Button><Plus className="mr-1 h-4 w-4" />新建项目</Button>
          </DialogTrigger>
          <DialogContent>
            <DialogHeader><DialogTitle>新建项目</DialogTitle></DialogHeader>
            <div className="flex flex-col gap-3">
              <Input placeholder="项目名称，如：卧室衣柜" value={name} onChange={(e) => setName(e.target.value)} />
              <Input placeholder="项目描述（可选）" value={desc} onChange={(e) => setDesc(e.target.value)} />
              <Button onClick={create}>创建</Button>
            </div>
          </DialogContent>
        </Dialog>
      </div>
      <div className="grid grid-cols-1 gap-4 md:grid-cols-2 lg:grid-cols-3">
        {projects.map((p) => (
          <Link key={p.id} href={`/projects/${p.id}`}>
            <Card className="cursor-pointer p-5 transition-shadow hover:shadow-md">
              <div className="flex items-center gap-2">
                <FolderKanban className="h-5 w-5 text-primary" />
                <h3 className="font-medium">{p.name}</h3>
              </div>
              <p className="mt-2 line-clamp-2 text-sm text-muted-foreground">{p.description}</p>
            </Card>
          </Link>
        ))}
      </div>
    </div>
  );
}
```

- [ ] **Step 4: 对话工作台（左聊天 + 右上方案操作 + 右下工作流卡片网格）**

```tsx
// frontend/src/components/chat/chat-panel.tsx
"use client";
import { useEffect, useRef, useState } from "react";
import { Api } from "@/lib/api";
import type { Asset, Degree } from "@/lib/types";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Paperclip, Send } from "lucide-react";

interface Message { role: "user" | "assistant"; content: string; }

export function ChatPanel({ projectId, onAnalyzed }: {
  projectId: number;
  onAnalyzed: () => void;
}) {
  const [messages, setMessages] = useState<Message[]>([
    { role: "assistant", content: "描述你的设计需求，或上传参考图。可选快速/标准/深度执行程度。" },
  ]);
  const [input, setInput] = useState("");
  const [degree, setDegree] = useState<Degree>("STANDARD");
  const [assets, setAssets] = useState<Asset[]>([]);
  const [busy, setBusy] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);

  async function refreshAssets() {
    setAssets(await Api.listAssets(projectId));
  }

  useEffect(() => { refreshAssets().catch(console.error); }, [projectId]);

  async function send() {
    const text = input.trim();
    if (!text || busy) return;
    setInput("");
    setMessages((m) => [...m, { role: "user", content: text }]);
    setBusy(true);
    try {
      const assetIds = assets.map((a) => a.id);
      await Api.analyze(projectId, { text, asset_ids: assetIds, degree });
      setMessages((m) => [...m, { role: "assistant", content: `已启动需求分析（${degree}），即将生成方案。` }]);
      onAnalyzed();
    } catch (err) {
      setMessages((m) => [...m, { role: "assistant", content: `出错了：${err instanceof Error ? err.message : "未知"}` }]);
    } finally {
      setBusy(false);
    }
  }

  async function onFile(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    if (!file) return;
    try {
      await Api.uploadAsset(projectId, file);
      await refreshAssets();
    } catch (err) { console.error(err); }
    if (fileRef.current) fileRef.current.value = "";
  }

  return (
    <div className="flex h-full flex-col">
      <div className="flex-1 space-y-3 overflow-auto p-4">
        {messages.map((m, i) => (
          <div key={i} className={`flex ${m.role === "user" ? "justify-end" : "justify-start"}`}>
            <div className={`max-w-[80%] rounded-lg px-3 py-2 text-sm ${
              m.role === "user" ? "bg-primary text-primary-foreground" : "bg-muted"}`}>
              {m.content}
            </div>
          </div>
        ))}
      </div>
      <div className="flex flex-col gap-2 border-t p-3">
        <div className="flex items-center gap-2">
          {assets.map((a) => (
            <span key={a.id} className="rounded bg-muted px-2 py-0.5 text-xs">{a.original_name}</span>
          ))}
        </div>
        <Textarea placeholder="描述需求，如：北欧风白橡木餐椅，适合小户型…" value={input}
                  onChange={(e) => setInput(e.target.value)} rows={2} />
        <div className="flex items-center gap-2">
          <DegreeSelector value={degree} onChange={setDegree} />
          <input ref={fileRef} type="file" hidden onChange={onFile} />
          <Button variant="outline" size="icon" onClick={() => fileRef.current?.click()}>
            <Paperclip className="h-4 w-4" />
          </Button>
          <Button onClick={send} disabled={busy || !input.trim()} className="ml-auto">
            <Send className="mr-1 h-4 w-4" />发送
          </Button>
        </div>
      </div>
    </div>
  );
}
```

```tsx
// frontend/src/components/workflow/degree-selector.tsx
"use client";
import type { Degree } from "@/lib/types";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

const OPTIONS: { value: Degree; label: string; hint: string }[] = [
  { value: "QUICK", label: "快速", hint: "估算区间" },
  { value: "STANDARD", label: "标准", hint: "标准方案" },
  { value: "DEEP", label: "深度", hint: "精确到零件" },
];

export function DegreeSelector({ value, onChange }: { value: Degree; onChange: (d: Degree) => void }) {
  return (
    <div className="flex items-center gap-1">
      {OPTIONS.map((o) => (
        <Button key={o.value} size="sm" variant={value === o.value ? "default" : "outline"}
                onClick={() => onChange(o.value)} title={o.hint}>
          {o.label}
        </Button>
      ))}
    </div>
  );
}
```

- [ ] **Step 5: 工作流卡片网格（默认视图）+ 流程视图切换（混合方案 C）**

```tsx
// frontend/src/components/workflow/workflow-grid.tsx
"use client";
import { useState } from "react";
import type { WorkflowStatus, WorkflowNode } from "@/lib/types";
import { Card } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { STATUS_META, NODE_TYPE_LABEL } from "@/lib/status-meta";
import { GitBranch, RotateCcw, CheckCircle2, Loader2, AlertTriangle } from "lucide-react";
import { cn } from "@/lib/utils";

export function WorkflowGrid({ status, onBranch, onRetry }: {
  status: WorkflowStatus;
  onBranch: (node: WorkflowNode, instruction: string) => void;
  onRetry: (jobId: number) => void;
}) {
  return (
    <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
      {status.nodes.map((node) => (
        <WorkflowCard key={node.id} node={node} onBranch={onBranch} onRetry={onRetry} />
      ))}
    </div>
  );
}

function WorkflowCard({ node, onBranch, onRetry }: {
  node: WorkflowNode;
  onBranch: (node: WorkflowNode, instruction: string) => void;
  onRetry: (jobId: number) => void;
}) {
  const meta = STATUS_META[node.status];
  const running = node.job?.status === "RUNNING" || node.job?.status === "QUEUED";
  return (
    <Card className={cn("p-4", node.branch_of && "border-dashed")}>
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <h4 className="text-sm font-medium">{NODE_TYPE_LABEL[node.type] ?? node.type}</h4>
          {node.branch_type && (
            <Badge variant="outline" className="text-[10px]">{node.branch_type}</Badge>
          )}
        </div>
        <Badge className={meta.className}>
          {running ? <Loader2 className="mr-1 h-3 w-3 animate-spin" /> : null}
          {meta.label}
        </Badge>
      </div>
      {node.summary && <p className="mt-2 line-clamp-2 text-xs text-muted-foreground">{node.summary}</p>}
      {node.job && (
        <div className="mt-2 h-1.5 w-full overflow-hidden rounded bg-muted">
          <div className="h-full bg-primary transition-all"
               style={{ width: `${Math.round((node.job.progress ?? 0) * 100)}%` }} />
        </div>
      )}
      <div className="mt-3 flex items-center gap-2">
        {node.status === "SUCCEEDED" && (
          <>
            <Button size="sm" variant="outline" onClick={() => onBranch(node, prompt("修改意见，如：改成圆角") ?? "")}>
              <GitBranch className="mr-1 h-3 w-3" />分支修改
            </Button>
          </>
        )}
        {node.job && node.job.status === "FAILED" && (
          <Button size="sm" variant="outline" onClick={() => onRetry(node.job!.id)}>
            <RotateCcw className="mr-1 h-3 w-3" />重试
          </Button>
        )}
        {node.job?.auth_required && node.status === "WAITING_AUTH" && (
          <span className="text-xs text-orange-600">待审批</span>
        )}
      </div>
    </Card>
  );
}
```

```tsx
// frontend/src/components/workflow/flow-view.tsx  —— 混合方案 C 的「流程视图」
"use client";
import type { WorkflowStatus } from "@/lib/types";
import { STATUS_META, NODE_TYPE_LABEL } from "@/lib/status-meta";
import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";

export function FlowView({ status }: { status: WorkflowStatus }) {
  const nodes = status.nodes;
  return (
    <div className="space-y-0">
      {nodes.map((node, i) => {
        const meta = STATUS_META[node.status];
        const children = status.edges.filter((e) => e.from === node.id);
        return (
          <div key={node.id}>
            <div className="flex items-center gap-3">
              <div className={cn("flex h-8 w-8 items-center justify-center rounded-full text-xs font-medium",
                node.status === "SUCCEEDED" ? "bg-green-100 text-green-800" : "bg-muted text-muted-foreground")}>
                {i + 1}
              </div>
              <span className="text-sm">{NODE_TYPE_LABEL[node.type] ?? node.type}</span>
              <Badge className={meta.className}>{meta.label}</Badge>
              {node.branch_type && <span className="text-xs text-muted-foreground">← {node.branch_type}</span>}
            </div>
            {children.length > 0 && <div className="ml-4 h-4 border-l-2 border-dashed" />}
          </div>
        );
      })}
    </div>
  );
}
```

- [ ] **Step 6: 工作台主页（组合 ChatPanel + WorkflowGrid + 方案生成）**

```tsx
// frontend/src/app/(app)/projects/[id]/page.tsx
"use client";
import { useCallback, useEffect, useState } from "react";
import { useParams } from "next/navigation";
import { Api } from "@/lib/api";
import type { Degree, Scheme, WorkflowNode, WorkflowStatus } from "@/lib/types";
import { ChatPanel } from "@/components/chat/chat-panel";
import { WorkflowGrid } from "@/components/workflow/workflow-grid";
import { FlowView } from "@/components/workflow/flow-view";
import { DegreeSelector } from "@/components/workflow/degree-selector";
import { SchemeCard } from "@/components/schemes/scheme-card";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Tabs, TabsList, TabsTrigger, TabsContent } from "@/components/ui/tabs";
import { Sparkles, GitBranch } from "lucide-react";

export default function WorkspacePage() {
  const { id } = useParams<{ id: string }>();
  const projectId = Number(id);
  const [status, setStatus] = useState<WorkflowStatus>({ nodes: [], edges: [] });
  const [schemes, setSchemes] = useState<Scheme[]>([]);
  const [degree, setDegree] = useState<Degree>("STANDARD");
  const [generating, setGenerating] = useState(false);
  const [lastText, setLastText] = useState("");

  const refresh = useCallback(async () => {
    const [wf, sc] = await Promise.all([
      Api.workflowStatus(projectId),
      Api.listSchemes(projectId),
    ]);
    setStatus(wf);
    setSchemes(sc);
  }, [projectId]);

  useEffect(() => {
    refresh().catch(console.error);
    const timer = setInterval(() => { refresh().catch(() => {}); }, 5000);
    return () => clearInterval(timer);
  }, [refresh]);

  async function generate() {
    setGenerating(true);
    try {
      const res = await Api.generateSchemes(projectId, { degree, count: 3, text: lastText, asset_ids: [] });
      if (res.scheme_ids.length > 0) await refresh();
    } finally {
      setGenerating(false);
    }
  }

  async function branchNode(node: WorkflowNode, instruction: string) {
    if (!instruction) return;
    await Api.reviseScheme(projectId, node.scheme_id ?? 0, instruction).catch(() => {});
    await refresh();
  }

  async function retryJob(jobId: number) {
    await Api.retryJob(jobId);
    await refresh();
  }

  return (
    <div className="flex h-full flex-col p-6">
      <div className="mb-4 flex items-center justify-between">
        <h1 className="text-xl font-semibold">设计工作台</h1>
        <div className="flex items-center gap-2">
          <DegreeSelector value={degree} onChange={setDegree} />
          <Button onClick={generate} disabled={generating}>
            <Sparkles className="mr-1 h-4 w-4" />生成方案 A/B/C
          </Button>
        </div>
      </div>
      <Tabs defaultValue="workflow" className="flex-1">
        <TabsList>
          <TabsTrigger value="workflow">工作流</TabsTrigger>
          <TabsTrigger value="schemes">方案对比</TabsTrigger>
        </TabsList>
        <TabsContent value="workflow" className="h-full">
          <div className="grid h-full grid-cols-2 gap-4">
            <Card className="overflow-hidden">
              <ChatPanel projectId={projectId} onAnalyzed={() => refresh()} />
            </Card>
            <Card className="overflow-auto p-4">
              <Tabs defaultValue="grid">
                <TabsList>
                  <TabsTrigger value="grid">卡片视图</TabsTrigger>
                  <TabsTrigger value="flow">流程视图</TabsTrigger>
                </TabsList>
                <TabsContent value="grid" className="pt-3">
                  <WorkflowGrid status={status} onBranch={branchNode} onRetry={retryJob} />
                </TabsContent>
                <TabsContent value="flow" className="pt-3">
                  <FlowView status={status} />
                </TabsContent>
              </Tabs>
            </Card>
          </div>
        </TabsContent>
        <TabsContent value="schemes">
          <div className="grid grid-cols-1 gap-4 md:grid-cols-3">
            {schemes.map((s) => (
              <SchemeCard key={s.id} scheme={s} projectId={projectId} onRevised={() => refresh()} />
            ))}
          </div>
        </TabsContent>
      </Tabs>
    </div>
  );
}
```

- [ ] **Step 7: 方案卡片（展示最新版本 + 版本列表 + 修改按钮）**

```tsx
// frontend/src/components/schemes/scheme-card.tsx
"use client";
import { useState } from "react";
import type { Scheme } from "@/lib/types";
import { Api } from "@/lib/api";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogTrigger } from "@/components/ui/dialog";
import { GitBranch } from "lucide-react";

export function SchemeCard({ scheme, projectId, onRevised }: {
  scheme: Scheme;
  projectId: number;
  onRevised: () => void;
}) {
  const [instruction, setInstruction] = useState("");
  const latest = scheme.versions[0];

  async function revise() {
    await Api.reviseScheme(projectId, scheme.id, instruction);
    setInstruction("");
    onRevised();
  }

  return (
    <Card>
      <CardHeader>
        <div className="flex items-center justify-between">
          <CardTitle className="text-base">{latest?.title ?? scheme.name}</CardTitle>
          <Badge variant="outline">{scheme.degree}</Badge>
        </div>
      </CardHeader>
      <CardContent>
        {latest?.description && (
          <p className="line-clamp-3 text-sm text-muted-foreground">{latest.description}</p>
        )}
        <div className="mt-2 flex flex-wrap gap-1">
          {(latest?.materials ?? []).map((m) => <Badge key={m} variant="secondary">{m}</Badge>)}
        </div>
        <div className="mt-3 text-xs text-muted-foreground">
          尺寸：{latest ? `${latest.dimensions.width_cm ?? "-"}×${latest.dimensions.depth_cm ?? "-"}×${latest.dimensions.height_cm ?? "-"} cm` : "-"}
          <span className="ml-2">工艺：{latest?.craft ?? "-"}</span>
        </div>
        <div className="mt-3 border-t pt-2 text-xs text-muted-foreground">
          {scheme.versions.map((v) => (
            <div key={v.id} className="flex items-center justify-between py-0.5">
              <span>V{v.version_no}</span>
              <Badge variant="outline" className="text-[10px]">{v.status === "ai_draft" ? "AI 生成草案" : v.status}</Badge>
            </div>
          ))}
        </div>
        <div className="mt-3 flex gap-2">
          <Dialog>
            <DialogTrigger asChild>
              <Button size="sm" variant="outline" className="flex-1">
                <GitBranch className="mr-1 h-3 w-3" />修改方案
              </Button>
            </DialogTrigger>
            <DialogContent>
              <DialogHeader><DialogTitle>修改方案</DialogTitle></DialogHeader>
              <Input placeholder="如：改成圆角桌面，加厚台面" value={instruction}
                     onChange={(e) => setInstruction(e.target.value)} />
              <Button onClick={revise}>生成新版本</Button>
            </DialogContent>
          </Dialog>
        </div>
      </CardContent>
    </Card>
  );
}
```

- [ ] **Step 8: 任务与审批中心页**

```tsx
// frontend/src/app/(app)/tasks/page.tsx
"use client";
import { useEffect, useState } from "react";
import { Api } from "@/lib/api";
import type { Job } from "@/lib/types";
import { Card } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";

export default function TasksPage() {
  const [jobs, setJobs] = useState<Job[]>([]);

  async function refresh() {
    setJobs(await Api.listJobs());
  }

  useEffect(() => {
    refresh().catch(console.error);
    const timer = setInterval(() => refresh().catch(() => {}), 5000);
    return () => clearInterval(timer);
  }, []);

  async function authorize(job: Job, approve: boolean) {
    await Api.authorizeJob(job.id, approve, approve ? "同意" : "拒绝");
    await refresh();
  }

  return (
    <div className="p-6">
      <h1 className="mb-4 text-2xl font-semibold">任务与审批中心</h1>
      <div className="space-y-2">
        {jobs.map((job) => (
          <Card key={job.id} className="flex items-center justify-between p-4">
            <div>
              <div className="flex items-center gap-2">
                <span className="text-sm font-medium">{job.task_type}</span>
                <Badge variant={job.status === "FAILED" ? "destructive" : "outline"}>{job.status}</Badge>
                {job.auth_required && <Badge className="bg-orange-100 text-orange-800">需授权</Badge>}
              </div>
              <p className="mt-1 text-xs text-muted-foreground">
                任务 #{job.id} · 节点 #{job.node_id} · 重试 {job.attempts} 次
                {job.error_message ? ` · 错误：${job.error_message}` : ""}
              </p>
            </div>
            <div className="flex items-center gap-2">
              {job.status === "WAITING_AUTH" ? (
                <>
                  <Button size="sm" onClick={() => authorize(job, true)}>批准</Button>
                  <Button size="sm" variant="outline" onClick={() => authorize(job, false)}>拒绝</Button>
                </>
              ) : null}
              {job.status === "FAILED" ? (
                <Button size="sm" variant="outline" onClick={() => Api.retryJob(job.id).then(refresh)}>重试</Button>
              ) : null}
            </div>
          </Card>
        ))}
        {jobs.length === 0 && <p className="text-sm text-muted-foreground">暂无任务</p>}
      </div>
    </div>
  );
}
```

- [ ] **Step 9: 跑前端测试 + 冒烟**

```bash
cd /Users/pipi/CodeSpace/ai-codesign-studio/frontend
npx vitest run
npm run build
```

Expected: vitest 1 PASS，build 无 TS 错误。

- [ ] **Step 10: Commit**

```bash
git add -A && git commit -m "feat: 登录/项目中心/对话工作台/审批中心页面"
```

---

## Task 15: 端到端集成测试 + Seed + README

**Files:**
- Create: `backend/tests/test_e2e_flow.py`
- Create: `backend/scripts/seed.py`
- Modify: `README.md`

- [ ] **Step 1: 写 E2E 测试（完整闭环，无 Redis 时跳过入队部分）**

```python
# backend/tests/test_e2e_flow.py
from httpx import ASGITransport, AsyncClient
from app.main import app


async def test_full_loop_register_project_analyze_generate():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.post("/api/auth/register", json={
            "email": "e2e@b.com", "password": "secret123", "display_name": "E2E"})
        assert r.status_code == 200

        r = await client.post("/api/projects", json={"name": "E2E 餐椅项目", "description": "闭环测试"})
        pid = r.json()["id"]

        r = await client.post(f"/api/projects/{pid}/analyze",
                              json={"text": "北欧风白橡木餐椅，适合小户型", "degree": "QUICK"})
        assert r.status_code == 200
        requirement_id = r.json()["requirement_id"]

        r = await client.get(f"/api/projects/{pid}/requirements/{requirement_id}")
        assert r.status_code == 200
        assert r.json()["extracted"]["furniture_types"]

        r = await client.post(f"/api/projects/{pid}/schemes/generate",
                              json={"degree": "QUICK", "count": 3, "text": "北欧风白橡木餐椅"})
        data = r.json()
        assert len(data["scheme_ids"]) == 3

        r = await client.get(f"/api/projects/{pid}/schemes")
        assert len(r.json()) == 3
        first = r.json()[0]
        assert first["versions"][0]["status"] == "ai_draft"

        r = await client.post(f"/api/projects/{pid}/schemes/{first['id']}/revise",
                              json={"instruction": "改成圆角"})
        assert r.status_code == 200
        revised = next(s for s in r.json() if s["id"] == first["id"])
        assert len(revised["versions"]) == 2
```

- [ ] **Step 2: 写 seed 脚本（演示数据）**

```python
# backend/scripts/seed.py
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db.session import SessionLocal
from app.models.org import Organization, User
from app.models.project import Project, ProjectMember
from app.services.auth_service import hash_password


async def main() -> None:
    async with SessionLocal() as db:
        org = Organization(name="示例家具企业")
        db.add(org)
        await db.flush()
        user = User(email="demo@codesign.dev", password_hash=hash_password("demo1234"),
                    display_name="演示管理员", organization_id=org.id, role="admin")
        db.add(user)
        await db.flush()
        project = Project(organization_id=org.id, name="示例项目：北欧餐椅",
                          description="演示用项目，包含完整闭环", created_by=user.id)
        db.add(project)
        await db.flush()
        db.add(ProjectMember(project_id=project.id, user_id=user.id, role="owner"))
        await db.commit()
        print(f"seeded: org={org.id} user={user.id} project={project.id}")
        print("login: demo@codesign.dev / demo1234")


if __name__ == "__main__":
    asyncio.run(main())
```

- [ ] **Step 3: 跑 E2E + seed**

```bash
cd /Users/pipi/CodeSpace/ai-codesign-studio/backend
DATABASE_URL=sqlite+aiosqlite:///:memory: pytest tests/test_e2e_flow.py -v
source .venv/bin/activate && python scripts/seed.py
```

Expected: E2E PASS，seed 输出演示账号。

- [ ] **Step 4: 更新 README（快速启动文档）**

```markdown
# AI CoDesign Studio

工业设计智能体平台 —— 以项目为核心、以对话和图片为入口，支持设计/宣发/生产自由跳转，按客户材料价格库自动报价。

## 快速启动

1. 基础设施：`cd deploy && docker compose up -d`
2. 后端：`cd backend && python3 -m venv .venv && source .venv/bin/activate && pip install -e ".[dev]" && uvicorn app.main:app --reload --port 8000`
3. Worker：`cd backend && source .venv/bin/activate && python -m arq app.worker.WorkerSettings`
4. 前端：`cd frontend && npm install && npm run dev`
5. 访问 http://localhost:3000，注册账号或使用 seed 演示账号 demo@codesign.dev / demo1234

## 阶段一闭环

用户上传需求 → AI 提取需求与场景识别 → 选择快速/标准/深度 → 生成 A/B/C 方案 → 保存项目 → 修改方案（版本分支）

## 计划
- docs/superpowers/plans/2026-08-05-codesign-studio-phase1-mvp.md（本计划）
- Plan 2-5：报价闭环 / 宣发 / 生产 / 企业集成（待写）
```

- [ ] **Step 5: Commit**

```bash
git add -A && git commit -m "feat: 端到端闭环测试与演示种子数据"
```

---

## Task 16: MindIE / 昇腾 910A 实机探测（可选，需硬件）

**Files:**
- Create: `docs/MINDIE_PROBE.md`
- Create: `backend/scripts/probe_910a.py`

- [ ] **Step 1: 写探测脚本（在 910A 服务器上运行）**

```python
# backend/scripts/probe_910a.py
"""昇腾 910A 探测：检查 CANN 环境、驱动、MindIE 可用性与模型兼容性。

在昇腾服务器上运行：python probe_910a.py
"""
import shutil
import subprocess
import sys


def run(cmd: list[str]) -> str:
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=60).stdout.strip()
    except Exception as exc:  # noqa: BLE001
        return f"ERROR: {exc}"


def main() -> None:
    print("== 昇腾 910A 探测 ==")
    print(f"npu-smi: {run(['npu-smi', 'info']) or 'NOT FOUND'}")
    print(f"CANN 目录: {run(['ls', '/usr/local/Ascend']) or 'NOT FOUND'}")
    print(f"MindIE 目录: {run(['ls', '/usr/local/Ascend/mindie']) or 'NOT FOUND (需手动安装 MindIE)'}")
    print(f"mindie python: {run([sys.executable, '-c', 'import mindie; print(mindie.__version__)']) or 'NOT INSTALLED'}")
    print(f"torch_npu: {run([sys.executable, '-c', 'import torch_npu; print(torch_npu.__version__)']) or 'NOT INSTALLED'}")
    print("\n结论写入 docs/MINDIE_PROBE.md 后提交，决定 910A 路线是否可行。")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: 写 docs/MINDIE_PROBE.md 模板**

```markdown
# MindIE / 昇腾 910A 探测报告

- 日期：
- 服务器：
- 探测人：

## 结果
- [ ] npu-smi 可见 910A 卡
- [ ] CANN 已安装（版本：）
- [ ] MindIE 已安装（版本：）
- [ ] torch_npu 可用
- [ ] 选定模型（如 Qwen2.5-7B）在 910A 上部署成功，首 token 延迟 < 2s
- [ ] 与现有 CANN/驱动/模型格式兼容

## 结论
- [ ] 可行：910A 推理节点进入阶段二排期
- [ ] 不可行：全部走 5090 / 云端 API，内部调度仍记录 ascend 路由但实际落 5090
```

- [ ] **Step 3: 有硬件时执行并记录结果，无硬件时标记待办**

```bash
# 在昇腾服务器上：
scp backend/scripts/probe_910a.py asc-server:/tmp/ && ssh asc-server "python3 /tmp/probe_910a.py"
```

- [ ] **Step 4: Commit**

```bash
git add -A && git commit -m "docs: MindIE/910A 探测脚本与报告模板"
```

---

## Self-Review 记录

**规格覆盖检查：**
- ✅ 用户/组织/权限（Task 3）、项目中心（Task 4）、对话框+上传（Task 5/14）、需求提取（Task 8）、场景识别（Task 8）、方案生成（Task 9）、三级执行程度（Task 6/9/14）、910A/5090 内部调度（Task 6 路由表）、任务授权（Task 7/12）、版本管理（Task 9 版本分支）
- ✅ 第一版范围控制（规格书十五）：只做设计链路，报价/宣发/生产在 Plan 2-5
- ✅ 用户只选执行程度、内部路由只对管理员可见（Task 12 job_detail）、AI 生成草案标记（Task 10/14）
- ⏳ 失败恢复/重试：Task 11 dispatch 重试逻辑 + 前端重试按钮
- ⏳ 外部 API 先授权：Task 7 needs_auth + WAITING_AUTH 流转

**设计修正记录（自检发现并修复）：**
- 方案版本生成统一为「节点驱动」：generate/revise 创建 concept 节点后 inline 执行 concept_executor（幂等 output_json 守卫 + 版本号 latest+1），消除「API 同步建 V1 + worker 再建 V1」的重复版本缺陷（Task 9/10）
- analyze 路由建的需求空行由 requirement_executor 幂等 upsert，不重复建行（Task 8）
- revise 统一走分支节点（branch_node + revision），与规格书「分支版本可追溯」一致（Task 9）
- API 路径统一为 `/projects/{project_id}/schemes/{scheme_id}/revise`（前端/E2E/后端一致，Task 9/13/15）

**遗留（后续计划）：**
- pgvector 知识检索 → Plan 3
- Temporal 工作流引擎 → Plan 3
- 报价体系全部 → Plan 2
- ComfyUI 真实工作流模板 → Plan 3（Task 10 是接口封装）；rendering 节点接线也在 Plan 3（Phase 1 闭环不含效果图，符合规格书十五的 MVP 链路）
- MindIE/910A 实机验证 → Task 16

**类型一致性：** `EXECUTORS` 注册表、`STATUS_FLOW` 状态、`WorkflowNode.status` 字符串值、前端 `NodeStatus` 联合类型已对齐。
