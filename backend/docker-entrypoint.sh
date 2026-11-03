#!/bin/sh
set -e

cd /workspace/backend

# 仅 API 进程做迁移；默认超管由 uvicorn lifespan 创建
case "${1:-}" in
  uvicorn|python)
    echo "[entrypoint] alembic upgrade head"
    alembic upgrade head
    ;;
esac

exec "$@"
