FROM python:3.14-slim@sha256:cea0e6040540fb2b965b6e7fb5ffa00871e632eef63719f0ea54bca189ce14a6

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# Часть пакетов доустанавливается не ради их самих, а чтобы забрать из
# репозитория Debian версии свежее тех, что лежат в пришпиленном базовом образе.
# Так закрываются уязвимости, исправленные уже после его сборки: Docker Hub
# пересобирает `python:3.14-slim` не сразу, и на 29 августа 2026 даже последний
# его digest нёс openssl 3.5.6 при доступном в Debian 3.5.7 (CVE-2026-14456,
# Trivy валил проверку тремя HIGH). Обновление digest от Dependabot этот случай
# не лечит — проверено на digest `cae66f2`, там та же 3.5.6.
RUN apt-get update && \
    DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
        ffmpeg \
        openssl \
        libssl3t64 \
        openssl-provider-legacy \
        bsdutils \
        libblkid1 \
        liblastlog2-2 \
        libmount1 \
        libsmartcols1 \
        libuuid1 \
        login \
        mount \
        util-linux && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN mkdir -p logs temp .secrets data

CMD ["python", "main.py"]
