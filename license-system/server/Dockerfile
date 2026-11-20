# --- AutoRoll license server --------------------------------------------------
# Сборка:  docker build -t autoroll-license .
# Запуск:  docker run --rm -p 8000:8000 --env-file .env autoroll-license
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY bot ./bot

RUN mkdir -p /app/data
EXPOSE 8000

# Render/Railway передают порт в $PORT
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
