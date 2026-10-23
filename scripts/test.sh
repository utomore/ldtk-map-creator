#!/usr/bin/env bash
# 與 CI 的測試同一道（P-004）
set -uo pipefail
cd "$(dirname "$0")/.."
uv run pytest "$@"
code=$?
# 還沒有測試時 pytest 回 5（沒收集到測試），視為通過
if [ "$code" -eq 5 ]; then exit 0; fi
exit "$code"
