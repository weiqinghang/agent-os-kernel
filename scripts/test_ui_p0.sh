#!/usr/bin/env bash
# UI P0′：Gherkin 行为 + ui/chat 行覆盖 ≥ 90%（c8）。
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
if [ ! -d node_modules/jsdom ] || [ ! -d node_modules/c8 ]; then
  npm install
fi
uv run pytest
