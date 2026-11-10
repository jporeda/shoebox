# syntax=docker/dockerfile:1

# ---- web UI ----
FROM node:22-bookworm-slim AS frontend
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
# Hashed lock of dependencies + faces + dev extras (README: "Regenerating the Python lock").
COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir --require-hashes -r /tmp/requirements.txt

# ---- face models (buffalo_l: non-commercial license; do not publish this image) ----
FROM python:3.12-slim AS models
ARG BUFFALO_SHA256=80ffe37d8a5940d59a7384c201a2a38d4741f2f3c51eef46ebb28218a7b0ca2f
ADD https://github.com/deepinsight/insightface/releases/download/v0.7/buffalo_l.zip /tmp/buffalo_l.zip
RUN echo "${BUFFALO_SHA256}  /tmp/buffalo_l.zip" | sha256sum -c - \
    && mkdir -p /models/models/buffalo_l \
    && python -c "import zipfile; z=zipfile.ZipFile('/tmp/buffalo_l.zip'); [z.extract(n, '/models/models/buffalo_l') for n in ('det_10g.onnx','w600k_r50.onnx','genderage.onnx')]"
# Face ages: ViT trained on FairFace (dima806/fairface_age_image_detection, Apache-2.0), ONNX export
# by onnx-community, pinned to one revision and checksum. See faces.AGE_MODEL.
ARG FAIRFACE_REV=fd04cddf04982f6acc1ac76fb1a31b44c9285596
ARG FAIRFACE_SHA256=7f28fc7e890b4c21bf48f65d9985cefaea3430c28df1391e4f91293c0d89555a
ADD https://huggingface.co/onnx-community/fairface_age_image_detection-ONNX/resolve/${FAIRFACE_REV}/onnx/model.onnx /tmp/fairface.onnx
RUN echo "${FAIRFACE_SHA256}  /tmp/fairface.onnx" | sha256sum -c - \
    && mkdir -p /models/fairface && mv /tmp/fairface.onnx /models/fairface/model.onnx

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
