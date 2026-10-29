# syntax=docker/dockerfile:1

# ---- web UI ----
FROM node:20-bookworm-slim AS frontend
WORKDIR /fe
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

# ---- python deps (insightface has no arm64 wheel, so compile here) ----
FROM python:3.12-slim AS builder
RUN apt-get update && apt-get install -y --no-install-recommends build-essential g++ \
    && rm -rf /var/lib/apt/lists/*
RUN python -m venv /venv
ENV PATH=/venv/bin:$PATH
COPY pyproject.toml /tmp/pyproject.toml
RUN python - <<'PY' > /tmp/requirements.txt
import tomllib
p = tomllib.load(open("/tmp/pyproject.toml", "rb"))["project"]
print("\n".join(p["dependencies"] + p["optional-dependencies"]["faces"] + p["optional-dependencies"]["dev"]))
PY
RUN pip install --no-cache-dir -r /tmp/requirements.txt

# ---- face models (buffalo_l: non-commercial license; do not publish this image) ----
FROM python:3.12-slim AS models
ARG BUFFALO_SHA256=80ffe37d8a5940d59a7384c201a2a38d4741f2f3c51eef46ebb28218a7b0ca2f
ADD https://github.com/deepinsight/insightface/releases/download/v0.7/buffalo_l.zip /tmp/buffalo_l.zip
RUN echo "${BUFFALO_SHA256}  /tmp/buffalo_l.zip" | sha256sum -c - \
    && mkdir -p /models/models/buffalo_l \
    && python -c "import zipfile; z=zipfile.ZipFile('/tmp/buffalo_l.zip'); [z.extract(n, '/models/models/buffalo_l') for n in ('det_10g.onnx','w600k_r50.onnx','genderage.onnx')]"

# ---- runtime ----
FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends \
      libimage-exiftool-perl libjpeg-turbo-progs libgomp1 tesseract-ocr \
    && rm -rf /var/lib/apt/lists/*
COPY --from=builder /venv /venv
COPY --from=models /models /models
ENV PATH=/venv/bin:$PATH \
    PYTHONPATH=/app/backend \
    PYTHONUNBUFFERED=1 \
    SHOEBOX_FRONTEND=/app/frontend/dist
WORKDIR /app
COPY pyproject.toml alembic.ini ./
COPY backend/ backend/
COPY tests/ tests/
COPY --from=frontend /fe/dist frontend/dist
EXPOSE 8000
CMD ["python", "-m", "shoebox.cli", "serve"]
