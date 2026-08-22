# Single image: Vite builds the React bundle, FastAPI serves it alongside the
# API. One deploy target, one port, no CORS, no second service to keep alive.

FROM node:20-alpine AS web
WORKDIR /web
COPY web/package.json web/package-lock.json* ./
RUN npm install
COPY web/ ./
RUN npm run build

FROM python:3.11-slim
WORKDIR /app

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PARCELPILOT_DB=/data/parcelpilot.db

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY app/ ./app/
COPY scripts/ ./scripts/
COPY tests/ ./tests/
COPY data/ ./data/
COPY --from=web /web/dist ./web/dist

# File-backed so actions and the audit log survive a restart mid-demo.
RUN mkdir -p /data
VOLUME ["/data"]

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
