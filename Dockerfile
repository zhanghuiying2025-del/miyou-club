FROM node:22-bookworm-slim AS frontend
WORKDIR /build

COPY frontend/customer/package*.json ./frontend/customer/
COPY frontend/boss/package*.json ./frontend/boss/
RUN npm --prefix frontend/customer ci && npm --prefix frontend/boss ci

COPY frontend ./frontend
RUN npm --prefix frontend/customer run build && npm --prefix frontend/boss run build

FROM python:3.12-slim-bookworm
WORKDIR /app
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY src ./src
COPY assets ./assets
COPY --from=frontend /build/frontend/customer/dist/client ./frontend/customer/dist/client
COPY --from=frontend /build/frontend/boss/dist/client ./frontend/boss/dist/client
RUN mkdir -p /app/data

EXPOSE 8000
CMD ["sh", "-c", "exec uvicorn src.web_api:app --host 0.0.0.0 --port \"${PORT:-8000}\" --workers 1"]
