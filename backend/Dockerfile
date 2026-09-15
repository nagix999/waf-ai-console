FROM node:22-bookworm-slim AS report-build
WORKDIR /source/frontend
COPY frontend/package*.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/src ./src
COPY frontend/scripts/build-report.mjs ./scripts/build-report.mjs
RUN npm run build:report

FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# A local text formatter, not a Node server. Both stages use Debian/glibc.
COPY --from=report-build /usr/local/bin/node /usr/local/bin/node
RUN node --version

WORKDIR /app

COPY backend/pyproject.toml backend/alembic.ini ./
RUN python -c 'import subprocess, sys, tomllib; subprocess.check_call([sys.executable, "-m", "pip", "install", "--no-cache-dir", *tomllib.load(open("pyproject.toml", "rb"))["project"]["dependencies"]])'
COPY backend/alembic ./alembic
COPY backend/app ./app
COPY --from=report-build /source/backend/app/data/report-document.cjs ./app/data/report-document.cjs

RUN pip install --no-cache-dir --no-deps .

EXPOSE 8000
