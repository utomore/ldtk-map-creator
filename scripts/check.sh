#!/usr/bin/env bash
# 與 CI 的檢查同一道（P-004）
set -euo pipefail
cd "$(dirname "$0")/.."
uv run ruff check .
uv run ruff format --check .
uv run pyright
