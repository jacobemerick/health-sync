FROM python:3.14-slim
WORKDIR /app
COPY health_ingest/requirements.txt ./health_ingest/
RUN pip install --no-cache-dir -r health_ingest/requirements.txt flask
COPY health_ingest/ ./health_ingest/
COPY local_server.py .
CMD ["python", "local_server.py"]
