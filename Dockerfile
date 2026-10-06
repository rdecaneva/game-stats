FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DB_PATH=/app/data/stats.db

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Run as an unprivileged user. Pass --build-arg UID=$(id -u) if your host uid isn't 1000
# so the mounted ./data directory is writable.
ARG UID=1000
RUN useradd --uid "$UID" --create-home app \
    && mkdir -p /app/data \
    && chown app:app /app/data
USER app

COPY app ./app

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
