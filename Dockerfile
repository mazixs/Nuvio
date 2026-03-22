FROM python:3.13-slim

RUN apt-get update && \
    apt-get install -y --no-install-recommends ffmpeg && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN mkdir -p logs temp .secrets

VOLUME ["/app/logs", "/app/temp", "/app/.secrets", "/app/data"]

CMD ["python", "main.py"]
