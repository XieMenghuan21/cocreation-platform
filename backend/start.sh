#!/usr/bin/env bash
# 共创智能体独立平台后端启动脚本
set -e
cd "$(dirname "$0")"
echo "正在启动共创智能体后端服务..."
uv run uvicorn app.main:app --host 0.0.0.0 --port 8001 --reload
