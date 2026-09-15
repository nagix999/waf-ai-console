FROM node:22-alpine AS report-build
WORKDIR /source/frontend
COPY frontend/package*.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/src ./src
COPY frontend/scripts/build-report.mjs ./scripts/build-report.mjs
COPY backend/app/assets/fonts /source/backend/app/assets/fonts
RUN npm run build:report

FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PLAYWRIGHT_BROWSERS_PATH=/opt/waf-browsers

WORKDIR /app

COPY backend/pyproject.toml backend/alembic.ini ./
RUN python -c 'import subprocess, sys, tomllib; subprocess.check_call([sys.executable, "-m", "pip", "install", "--no-cache-dir", *tomllib.load(open("pyproject.toml", "rb"))["project"]["dependencies"]])' \
    && python -m playwright install --with-deps chromium
COPY backend/alembic ./alembic
COPY backend/app ./app
COPY --from=report-build /source/backend/app/data/report-ui ./app/data/report-ui

RUN pip install --no-cache-dir --no-deps .

EXPOSE 8000
