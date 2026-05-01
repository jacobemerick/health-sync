FROM python:3.14-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt flask
COPY health_ingest/ ./health_ingest/
COPY local_server.py .
CMD ["python", "local_server.py"]
