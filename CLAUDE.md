# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Nuvio — Telegram-бот для скачивания видео с YouTube, TikTok и Instagram. Python 3.13+, async-архитектура на python-telegram-bot. Включает WebUI-дашборд аналитики (FastAPI).

## Commands

```bash
# Запуск бота
python main.py

# Docker
docker-compose up --build

# Тесты
pytest                              # все тесты
pytest tests/test_youtube_smoke.py  # один файл
pytest -k "test_name"               # один тест
pytest --run-slow                   # включить медленные тесты
pytest --run-network                # включить сетевые тесты

# Зависимости
pip install -r requirements.txt
```

## Architecture

**Точка входа**: `main.py` — async event-loop, graceful shutdown (SIGINT/SIGTERM), загрузка `.env` по цепочке `.secrets/.env` → `.env.local` → `.env`, scheduled tasks (daily cache cleanup, weekly VACUUM).

**Конфигурация**: `config.py` — парсинг env-переменных с типизацией, пути к секретам в `.secrets/` (fallback на корень).

**Основные модули в `utils/`**:
- `telegram_utils.py` (~120KB) — все хэндлеры бота: команды, callback-кнопки, обработка URL, отправка файлов, error handler с крэш-репортами админам
- `youtube_utils.py` — загрузка YouTube/Shorts через yt-dlp с cookie-поддержкой и smart retry
- `tiktok_instagram_utils.py` — TikTok (множественные API-хосты, exponential backoff) и Instagram (rate-limit aware, cookies для приватных профилей)
- `media_processor.py` — FFmpeg: извлечение аудио (MP3 192k), конвертация WebM→MP4, мерж аудио/видео
- `video_cache.py` — SQLite кэш file_id для мгновенной повторной отправки (WAL mode, TTL 90 дней)
- `analytics_db.py` — SQLite аналитика: таблицы `users`, `events` (WAL mode)
- `ytdlp_runtime.py` — авто-обновление yt-dlp, CLI fallback (`python -m yt_dlp`)
- `gokapi_utils.py` — загрузка файлов >50MB на Gokapi (multipart, API key)
- `cookie_manager.py` / `cookie_health.py` — админский интерфейс загрузки и валидации cookies
- `logger.py` — настройка rotating file handler для логирования
- `cache_commands.py` — команды управления кэшем (очистка, статистика)
- `temp_file_manager.py` — управление временными файлами (автоочистка)

**WebUI** (`web/`): FastAPI + Jinja2 + Uvicorn. Логин, дашборд, список пользователей, детали пользователя. Порт через `WEB_PORT`.

**Поток обработки запроса**: URL → валидация (regex) → получение инфо → кнопки выбора формата → скачивание в ThreadPoolExecutor → проверка кэша (hit → file_id, miss → download & cache) → отправка (<50MB прямая, >50MB → Gokapi).

## Key Patterns

- **Async + ThreadPoolExecutor**: блокирующие операции (yt-dlp) выполняются в пуле потоков (`DOWNLOAD_WORKERS=8`)
- **match-case**: выбор платформы/формата
- **Exception.add_note()**: обогащение ошибок контекстом
- **Коды ошибок**: формат `<PREFIX>-<CATEGORY>-<RANDOM>` (YT/TT/IG/TG/FILE/BOT + ACCESS/NETWORK/TIMEOUT/...)
- **SQLite WAL mode** во всех БД для конкурентного доступа
- **Сообщения**: все user-facing тексты в `messages.py`
- **Логирование**: rotating file handler (10MB, 5 backups) → `logs/bot.log`

## Testing

Маркеры: `syntax`, `unit`, `integration`, `slow`. Fixtures и hooks в `tests/conftest.py`. Тесты YouTube используют мокированный YoutubeDL (без сети). Системные зависимости для тестов: FFmpeg, git.

## Environment

Обязательные переменные: `TELEGRAM_TOKEN`, `ADMIN_IDS`. Опциональные: `GOKAPI_BASE_URL`, `GOKAPI_API_KEY`, `WEB_PORT`, `WEB_PASSWORD`, `YTDLP_*`. Шаблон в `.env.example`.
