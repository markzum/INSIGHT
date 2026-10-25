FROM python:3.12-slim

WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    DEDUP_DB=/var/lib/dedup/dedup.sqlite

COPY requirements.lock pyproject.toml README.md ./
COPY src ./src
COPY schemas ./schemas
COPY data ./data

RUN pip install --no-cache-dir -r requirements.lock \
    && pip install --no-cache-dir --no-deps .

EXPOSE 8001
CMD ["uvicorn", "dedup.api:app", "--host", "0.0.0.0", "--port", "8001", "--workers", "1"]
