# Руководство по развертыванию Nuvio

## Требования

- **Python 3.13+** (протестировано на 3.14)
- **FFmpeg** (установленный в системе)
- **Git**
- **Токен Telegram-бота** -- получить у [@BotFather](https://t.me/BotFather)

## Локальная установка

```bash
git clone https://github.com/mazixs/Nuvio.git
cd Nuvio
python -m venv .venv
source .venv/bin/activate  # Linux/macOS
# .venv\Scripts\activate   # Windows
pip install -r requirements.txt
```

## Настройка окружения

Скопируйте файл `.env.example` в `.secrets/.env` и заполните необходимые переменные:

```bash
mkdir -p .secrets
cp .env.example .secrets/.env
```

**Обязательные переменные:**

- `TELEGRAM_TOKEN` -- токен бота от @BotFather
- `ADMIN_IDS` -- ID администраторов через запятую (узнать свой ID можно у @userinfobot)

**Опциональные переменные:**

- `GOKAPI_BASE_URL`, `GOKAPI_API_KEY` -- для отправки файлов больше лимита Telegram (50 MB)
- `WEB_USERNAME`, `WEB_PASSWORD`, `WEB_PORT`, `WEB_SECRET_KEY` -- настройки веб-дашборда
- `YTDLP_AUTO_UPDATE`, `YTDLP_RELEASE_CHANNEL`, `YTDLP_CLI_FALLBACK` -- управление yt-dlp

**Цепочка загрузки .env:** `.secrets/.env` -> `.env.local` -> `.env`. Используется первый найденный файл, последующие не перезаписывают уже загруженные значения.

## Настройка cookies

Cookies необходимы для доступа к контенту, требующему авторизации. Файлы должны быть в формате Netscape и размещаться в директории `.secrets/`:

| Платформа | Путь к файлу | Переменная окружения (альтернатива) |
|-----------|-------------|--------------------------------------|
| YouTube   | `.secrets/www.youtube.com_cookies.txt` | `YOUTUBE_COOKIES_FILE` |
| Instagram | `.secrets/www.instagram.com_cookies.txt` | -- |
| TikTok    | `.secrets/www.tiktok.com_cookies.txt` | -- |

Администраторы бота также могут загружать и обновлять cookies напрямую через Telegram, используя команду `/admin`.

## Запуск

```bash
python main.py
```

Бот запустится с async event-loop, graceful shutdown по SIGINT/SIGTERM, а также запланированными задачами (ежедневная очистка кэша, еженедельный VACUUM).

## Docker

### Архитектура

Dockerfile основан на `python:3.13-slim` с установленным FFmpeg. Docker Compose поднимает **два сервиса** из одного образа:

| Сервис | Контейнер | Что делает | Порты |
|--------|-----------|------------|-------|
| `bot` | `nuvio-bot` | Telegram-бот (`main.py`) | нет |
| `web` | `nuvio-web` | WebUI-дашборд (`python -m web.app`) | `WEB_PORT` (8080) |

Оба сервиса используют общий Docker volume `bot-data` для базы данных аналитики — бот пишет данные, дашборд их читает.

### Пошаговая настройка

**1. Создайте `.env` из шаблона:**

```bash
cp .env.example .env
```

**2. Отредактируйте `.env` — заполните обязательные и настройте опциональные переменные:**

```env
# ── ОБЯЗАТЕЛЬНЫЕ ──────────────────────────────────────
TELEGRAM_TOKEN=1234567890:ABCdefGHIjklMNOpqrsTUVwxyz
ADMIN_IDS=123456789,987654321

# ── WEB UI (дашборд аналитики) ────────────────────────
WEB_USERNAME=admin          # логин для входа в дашборд
WEB_PASSWORD=MyStr0ngPass!  # ⚠️ ОБЯЗАТЕЛЬНО сменить!
WEB_PORT=8080               # порт дашборда (внутри контейнера и снаружи)
# WEB_SECRET_KEY=random_string  # ключ сессий (генерируется автоматически)

# ── GOKAPI (для файлов > 50MB) ───────────────────────
# GOKAPI_BASE_URL=https://gokapi.example.com/api/
# GOKAPI_API_KEY=your_api_key

# ── YT-DLP ────────────────────────────────────────────
# YTDLP_AUTO_UPDATE=true
# YTDLP_RELEASE_CHANNEL=nightly
```

**3. (Опционально) Настройте cookies для платформ:**

```bash
mkdir -p .secrets
# Положите файлы cookies в формате Netscape:
#   .secrets/www.youtube.com_cookies.txt
#   .secrets/www.instagram.com_cookies.txt
#   .secrets/www.tiktok.com_cookies.txt
```

**4. Запуск:**

```bash
# Локальная сборка (разработка)
docker compose up --build

# Или из GHCR по конкретной версии (продакшен)
TAG=1.0.0 docker compose -f docker-compose.prod.yml up -d
```

### Доступ к дашборду

После запуска дашборд доступен по адресу:

```
http://<ip-сервера>:<WEB_PORT>
```

Например, `http://localhost:8080`. Логин и пароль — из переменных `WEB_USERNAME` / `WEB_PASSWORD`.

### Смена порта

Порт задаётся переменной `WEB_PORT` в `.env`. Она используется и для маппинга портов в Docker Compose, и для привязки внутри контейнера:

```env
WEB_PORT=3000  # дашборд будет на http://localhost:3000
```

### Volumes

| Volume/Mount | Тип | Описание |
|--------------|-----|----------|
| `bot-data` | Docker volume | Общая БД аналитики между bot и web |
| `./logs` | Bind mount | Логи бота (rotating, 10MB × 5) |
| `./.secrets` | Bind mount (ro) | Cookies и секреты (read-only) |

### Docker Compose файлы

| Файл | Назначение |
|------|-----------|
| `docker-compose.yml` | Локальная разработка — собирает из исходников (`build: .`) |
| `docker-compose.prod.yml` | Продакшен — тянет готовый образ из `ghcr.io/mazixs/nuvio` |

Для продакшена:
```bash
# Скачать только compose-файл
wget https://raw.githubusercontent.com/mazixs/Nuvio/main/docker-compose.prod.yml

# Настроить .env (см. выше)

# Запустить конкретную версию
TAG=1.2.0 docker compose -f docker-compose.prod.yml up -d

# Или latest
docker compose -f docker-compose.prod.yml up -d
```

### Обновление Docker-контейнеров

```bash
# Локальная сборка
git pull
docker compose up --build -d

# Из GHCR
TAG=1.3.0 docker compose -f docker-compose.prod.yml pull
TAG=1.3.0 docker compose -f docker-compose.prod.yml up -d
```

## Systemd (Linux-сервер)

Для запуска на выделенном сервере рекомендуется использовать systemd. Скрипт `init_env.sh` выполняется как `ExecStartPre` и выполняет следующие действия:

1. Создает необходимые директории (`.secrets`, `downloads`, `logs`, `temp`)
2. Мигрирует legacy-файлы секретов в `.secrets/`
3. Выполняет best-effort обновление из git (`git fetch` + `git reset --hard`)
4. Устанавливает/обновляет зависимости из `requirements.txt`
5. Валидирует критические импорты (`telegram`, `yt_dlp`)

Пример unit-файла:

```ini
[Unit]
Description=Nuvio Telegram Bot
After=network.target

[Service]
Type=simple
User=nuvio
WorkingDirectory=/opt/nuvio
ExecStartPre=/opt/nuvio/init_env.sh
ExecStart=/opt/nuvio/.venv/bin/python main.py
Restart=on-failure
RestartSec=10

[Install]
WantedBy=multi-user.target
```

Установка сервиса:

```bash
sudo cp nuvio.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now nuvio
```

## WebUI (дашборд аналитики)

Дашборд доступен по адресу `http://<host>:<WEB_PORT>` (по умолчанию `http://localhost:8080`).

### Переменные окружения для WebUI

| Переменная | По умолчанию | Описание |
|---|---|---|
| `WEB_USERNAME` | `admin` | Логин для входа |
| `WEB_PASSWORD` | `changeme` | Пароль для входа (**обязательно сменить!**) |
| `WEB_PORT` | `8080` | Порт дашборда |
| `WEB_SECRET_KEY` | авто | Ключ подписи сессий |
| `FAIL2BAN_RETRIES` | `5` | Попыток логина до блокировки IP |
| `FAIL2BAN_TIME` | `10m` | Время блокировки (`15m`, `1h`, `300`) |

### WEB_SECRET_KEY

Ключ используется для HMAC-подписи сессионных cookie. Без него невозможно подделать сессию.

- **Не задан** — генерируется случайный при каждом старте. После рестарта все сессии сбрасываются.
- **Задан** — сессии переживают рестарты контейнера.

Генерация ключа:

```bash
python -c "import secrets; print(secrets.token_hex(32))"
```

Формат: любая строка, рекомендуется 64 hex-символа (32 байта энтропии).

### Безопасность дашборда

- **SQL-инъекции** — невозможны, все запросы параметризованы
- **Brute-force** — fail2ban: настраивается через `FAIL2BAN_RETRIES` (попыток) и `FAIL2BAN_TIME` (блокировка). Формат времени: `15m`, `1h`, `300`
- **Timing-атаки** — `hmac.compare_digest` для сравнения логина/пароля
- **XSS** — Jinja2 автоэкранирование
- **Длина ввода** — макс. 128 символов на логин/пароль
- **Swagger/ReDoc** — отключены, API-схема скрыта

> **Важно:** Пароль хранится как SHA256-хеш и проверяется при каждом логине. Если `WEB_SECRET_KEY` не задан, при перезапуске контейнера все сессии сбрасываются.

### Что показывает дашборд

- **Пользователи:** всего, новые/активные за 1/7/30 дней
- **Удержание и отток:** retention D3/D7/D30, churn 30д
- **Графики:** скачивания и новые пользователи по дням, трафик по платформам
- **Топ видео:** 10 самых скачиваемых URL
- **Страница пользователей:** список, поиск, детальная карточка с историей событий

## Обновление

| Метод | Как обновить |
|-------|-------------|
| **Docker (GHCR)** | `TAG=1.3.0 docker compose -f docker-compose.prod.yml pull && ... up -d` |
| **Docker (сборка)** | `git pull && docker compose up --build -d` |
| **Systemd** | `init_env.sh` при каждом рестарте делает `git fetch` + `git reset --hard` + `pip install` |
| **yt-dlp** | Автообновление при старте бота (`YTDLP_AUTO_UPDATE=true`) |
