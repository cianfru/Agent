FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY synth ./synth
ENV SYNTH_DB=/data/synth.db
CMD ["python", "-m", "synth", "serve"]
