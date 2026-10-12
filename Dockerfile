FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
# exact versions, hash-checked (regenerate with: uv pip compile requirements.txt --python-version 3.12 --generate-hashes -o requirements.lock)
COPY requirements.lock .
RUN pip install --no-cache-dir --require-hashes -r requirements.lock
COPY synth ./synth
# the volume for SQLite (and its backups) is mounted at /data; Railway mounts it root-owned, so the app runs as root
RUN mkdir -p /data
ENV SYNTH_DB=/data/synth.db SYNTH_BACKUP_DIR=/data/backups
CMD ["python", "-m", "synth", "serve"]
