# syntax=docker/dockerfile:1

FROM node:22-bookworm-slim AS frontend-builder

WORKDIR /build/frontend
COPY src/frontend/package.json src/frontend/package-lock.json ./
RUN npm ci
COPY src/frontend/ ./
RUN npm run build


FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8000 \
    BRNRD_ENABLE_DEV=0 \
    BRNRD_FRONTEND_DIR=/app/frontend

WORKDIR /app

ARG BRNRD_BUILD_COMMIT=""

COPY pyproject.toml README.md LICENSE LICENSE-OVERVIEW.md ./
COPY src/brr ./src/brr
COPY src/brnrd ./src/brnrd
RUN python -m pip install --no-cache-dir ".[backend,postgres]" \
    && rm -rf /app/src

RUN python - <<'PY'
import datetime
import os
from pathlib import Path

import brnrd

commit = os.environ.get("BRNRD_BUILD_COMMIT", "")
built_at = datetime.datetime.now(datetime.timezone.utc).isoformat()
path = Path(brnrd.__file__).with_name("build_info.txt")
path.write_text(f"{commit}\n{built_at}\n", encoding="utf-8")
PY

COPY --from=frontend-builder /build/frontend/build /app/frontend

EXPOSE 8000

# Scaleway supplies PORT from the container's configured port; the default
# keeps the same image directly runnable elsewhere.
CMD ["sh", "-c", "exec uvicorn brnrd:create_app --factory --host 0.0.0.0 --port \"${PORT:-8000}\""]
