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

Dockerfile основан на `python:3.13-slim` с установленным FFmpeg. В `docker-compose.yml` определены два сервиса:

- **bot** (`nuvio-bot`) -- запускает `main.py`, монтирует `logs/`, `.secrets/` (read-only) и общий volume `bot-data`
- **web** (`nuvio-web`) -- запускает `python -m web.app`, открывает порт `WEB_PORT` (по умолчанию 8080)

```bash
docker-compose up --build
```

Перед запуском создайте файл `.env` в корне проекта (Docker Compose читает `env_file: .env`):

```bash
cp .env.example .env
# отредактируйте .env -- заполните TELEGRAM_TOKEN, ADMIN_IDS и другие переменные
```

**Volumes:**

- `bot-data` -- общий volume между сервисами bot и web, используется для базы данных аналитики
- `./logs` -- логи бота (bind mount)
- `./.secrets` -- секреты и cookies (bind mount, read-only)

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

## WebUI

Веб-дашборд аналитики доступен по адресу `http://<host>:<WEB_PORT>` (по умолчанию порт 8080).

- **Логин:** задается через `WEB_USERNAME` / `WEB_PASSWORD` (по умолчанию `admin` / `changeme`)
- **Обязательно смените пароль перед развертыванием в продакшене!**
- Дашборд отображает аналитику: статистику скачиваний, список пользователей, популярные видео

## Обновление

- **Systemd:** скрипт `init_env.sh` автоматически подтягивает обновления при каждом перезапуске сервиса -- выполняет `git fetch` + `git reset --hard` до актуального состояния удаленной ветки, затем обновляет зависимости
- **yt-dlp:** автоматически обновляется до канала nightly при запуске бота (настраивается через `YTDLP_AUTO_UPDATE` и `YTDLP_RELEASE_CHANNEL`)
- **Docker:** пересоберите образы для получения обновлений:

```bash
git pull
docker-compose up --build
```
