FROM python:3.12-slim

ARG INSTALL_DEV=false
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=/app
WORKDIR /app

COPY requirements.txt requirements-dev.txt ./
RUN if [ "$INSTALL_DEV" = "true" ]; then pip install --no-cache-dir -r requirements-dev.txt; \
    else pip install --no-cache-dir -r requirements.txt; fi

COPY core ./core
COPY api ./api
COPY worker ./worker
COPY prompts ./prompts
COPY docs ./docs
COPY alembic ./alembic
COPY alembic.ini ./alembic.ini
RUN useradd -m -u 1000 app && mkdir -p /data && chown app /data
USER app
CMD ["uvicorn", "api.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
