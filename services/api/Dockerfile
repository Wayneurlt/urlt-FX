FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app

COPY pyproject.toml ./
COPY trading ./trading
RUN pip install .
COPY services/api ./services/api
COPY migrations ./migrations
COPY alembic.ini ./
COPY scripts ./scripts
COPY data/sample_events.json ./data/sample_events.json

RUN useradd --create-home --uid 10001 fx && mkdir -p /app/data/datasets && chown -R fx /app/data
USER fx
WORKDIR /app/services/api
EXPOSE 8000
HEALTHCHECK --interval=15s --timeout=5s --retries=5 CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:8000/api/v1/health').status==200 else 1)"
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
