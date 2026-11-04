FROM python:3.12-slim
# Inter for the charts (synth/chart.py); they fall back to DejaVu Sans without it
RUN apt-get update && apt-get install -y --no-install-recommends fonts-inter && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY synth ./synth
ENV SYNTH_DB=/data/synth.db
CMD ["python", "-m", "synth", "serve"]
