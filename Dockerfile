FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 BUILDERP_DATA_DIR=/app/data
WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt && useradd --create-home --uid 10001 builderp && mkdir -p /app/data && chown builderp:builderp /app/data
COPY --chown=builderp:builderp builderp ./builderp
COPY --chown=builderp:builderp run.py migrate.py ./
COPY --chown=builderp:builderp public ./public
USER builderp
EXPOSE 8000
# Sync workers isolate PyMuPDF operations in separate processes.
CMD ["gunicorn", "--bind", "0.0.0.0:8000", "--workers", "2", "--worker-class", "sync", "--timeout", "300", "--access-logfile", "-", "run:app"]
