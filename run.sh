#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
if ! command -v uv >/dev/null 2>&1; then
  printf '%s\n' 'Install uv (https://docs.astral.sh/uv/getting-started/installation/) or follow the pip instructions in README.md.' >&2
  exit 1
fi
uv sync --frozen --python 3.12
if [[ -f .env ]]; then
  exec uv run --frozen --env-file .env python -m uvicorn app.main:app --host 127.0.0.1 --port "${FLOOD_LENS_PORT:-8000}"
fi
exec .venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port "${FLOOD_LENS_PORT:-8000}"
