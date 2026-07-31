# 共创智能体独立平台后端 API 服务

## 简介

基于 FastAPI 构建的共创智能体平台后端服务,提供工业设计、CAD 处理、AI 图像生成等功能。

## 技术栈

- FastAPI
- Python 3.12+
- uvicorn (ASGI 服务器)
- SQLAlchemy (ORM)
- Pydantic (数据验证)

## 启动方式

```bash
cd backend
bash start.sh
```

服务将运行在 http://localhost:8001

## API 文档

启动后可访问:
- Swagger UI: http://localhost:8001/docs
- ReDoc: http://localhost:8001/redoc

## 数据库 Cutover

1. 创建并配置 PostgreSQL `DATABASE_URL`。
2. 执行数据库迁移:

```bash
cd backend
./.venv/bin/python -m alembic upgrade head
```

3. 先做旧数据 dry-run，确认报告没有缺失文件和校验异常:

```bash
cd backend
./.venv/bin/python -m app.migrations.import_legacy_storage \
  --sqlite-path ./cocreation.db \
  --storage-root ./uploads \
  --storage-root ./storage \
  --report ./legacy-import-report.json \
  --dry-run
```

4. 执行正式导入:

```bash
cd backend
./.venv/bin/python -m app.migrations.import_legacy_storage \
  --sqlite-path ./cocreation.db \
  --storage-root ./uploads \
  --storage-root ./storage \
  --report ./legacy-import-report.json
```

5. 审核 `legacy-import-report.json`，确认 `missing_files` 和 `checksum_failures` 为 `0` 后再启动应用。
6. 回滚方式：保留旧 SQLite 和旧文件目录，不删除源数据；若新库验证失败，恢复旧部署并停止新实例。
