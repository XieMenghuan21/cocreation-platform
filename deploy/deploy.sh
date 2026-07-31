#!/usr/bin/env bash
set -e

DEPLOY_DIR="/opt/cocreation-platform"
SERVER="ascend-001"

POSTGRES_PASSWORD="${POSTGRES_PASSWORD:-$(openssl rand -hex 16)}"
JWT_SECRET_KEY="${JWT_SECRET_KEY:-$(openssl rand -hex 32)}"
NODAPI_API_KEY="${NODAPI_API_KEY:-}"
NODAPI_BASE_URL="${NODAPI_BASE_URL:-https://www.nodapi.com}"
DASHSCOPE_API_KEY="${DASHSCOPE_API_KEY:-}"
CAD_AI_TIMEOUT_SECONDS="${CAD_AI_TIMEOUT_SECONDS:-240}"

echo "=== 生成部署配置 ==="
cat > /tmp/cocreation-env << ENDOFENV
POSTGRES_PASSWORD=${POSTGRES_PASSWORD}
JWT_SECRET_KEY=${JWT_SECRET_KEY}
NODAPI_API_KEY=${NODAPI_API_KEY}
NODAPI_BASE_URL=${NODAPI_BASE_URL}
DASHSCOPE_API_KEY=${DASHSCOPE_API_KEY}
CAD_AI_TIMEOUT_SECONDS=${CAD_AI_TIMEOUT_SECONDS}
ENDOFENV

echo "=== 同步项目文件到服务器 ==="
rsync -avz --delete \
  --exclude '.git' \
  --exclude '.DS_Store' \
  --exclude 'backend/.venv' \
  --exclude 'backend/__pycache__' \
  --exclude 'backend/.pytest_cache' \
  --exclude 'backend/cocreation.db' \
  --exclude 'backend/storage' \
  --exclude 'frontend/node_modules' \
  --exclude 'frontend/src' \
  --exclude 'frontend/public' \
  --exclude 'frontend/tests' \
  --exclude 'docs' \
  --exclude 'screenshot.png' \
  /Users/pipi/CodeSpace/cocreation-platform/ \
  "${SERVER}:${DEPLOY_DIR}/"

echo "=== 写入环境变量 ==="
ssh "${SERVER}" "cat > ${DEPLOY_DIR}/.env" < /tmp/cocreation-env

echo "=== 在服务器上构建并启动 ==="
ssh "${SERVER}" "cd ${DEPLOY_DIR} && \
  docker-compose -f deploy/docker-compose.yml down --remove-orphans 2>/dev/null || true; \
  docker-compose -f deploy/docker-compose.yml build --no-cache backend && \
  docker-compose -f deploy/docker-compose.yml build frontend && \
  docker-compose -f deploy/docker-compose.yml up -d"

echo "=== 部署完成 ==="
echo "应用地址: http://106.8.105.18:44308"
