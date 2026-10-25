FROM python:3.12-slim

WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PARSER_HOME=/app \
    PARSER_OFFLINE=1 \
    PARSER_DB=/var/lib/parser/parser.sqlite

COPY requirements.lock pyproject.toml README.md ./
COPY src ./src
COPY config ./config
COPY fixtures ./fixtures
COPY schemas ./schemas

RUN pip install --no-cache-dir -r requirements.lock \
    && pip install --no-cache-dir --no-deps .

EXPOSE 8002
CMD ["uvicorn", "parser.api:app", "--host", "0.0.0.0", "--port", "8002", "--workers", "1"]
