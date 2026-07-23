# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Nuvio — Telegram-бот для скачивания медиа с YouTube, TikTok, Instagram,
Rutube и VK Video. Python 3.14+, async-архитектура на python-telegram-bot.
Включает WebUI-дашборд аналитики и локальный Telegram Bot API.

## Commands

```bash
# Запуск бота
python main.py

# Docker
docker compose --env-file .secrets/.env -f compose.yaml -f compose.dev.yaml up --build

# Тесты
pytest                              # все тесты
pytest tests/test_youtube_smoke.py  # один файл
pytest -k "test_name"               # один тест
coverage run --branch -m pytest tests/
coverage report --fail-under=40

# Зависимости
pip install -r requirements.txt
```

Прямой запуск использует облачный Bot API с лимитом 50 МБ. Полный
Docker-стек отправляет файлы до 2 ГБ через локальный Bot API.

## Architecture

**Точка входа**: `main.py` — async event-loop, graceful shutdown
(SIGINT/SIGTERM), настройки из `.secrets/.env`, периодические задачи очистки
кэша и `VACUUM`. `.env.local` и корневой `.env` поддерживаются только для
обратной совместимости.

**Конфигурация**: `config.py` — парсинг env-переменных с типизацией, пути к секретам в `.secrets/` (fallback на корень).

**Основные модули в `utils/`**:
- `telegram_utils.py` — Telegram-хэндлеры и координация пользовательского потока
- `callback_fsm.py` — разбор callback-событий и ограниченное хранилище сессий
- `platform_actions.py` — чистые решения платформенных действий и ключей кэша
- `file_delivery.py` — выбор способа доставки по виду медиа
- `public_errors.py` — безопасная классификация пользовательских ошибок
- `youtube_utils.py` — загрузка YouTube/Shorts через yt-dlp с cookie-поддержкой и smart retry
- `tiktok_instagram_utils.py` — TikTok (множественные API-хосты, exponential backoff) и Instagram (rate-limit aware, cookies для приватных профилей)
- `media_processor.py` — FFmpeg: извлечение аудио (MP3 192k), конвертация WebM→MP4, мерж аудио/видео
- `video_cache.py` — SQLite кэш file_id для мгновенной повторной отправки (WAL mode, TTL 90 дней)
- `analytics_db.py` — SQLite аналитика: таблицы `users`, `events` (WAL mode)
- `ytdlp_runtime.py` — авто-обновление yt-dlp, CLI fallback (`python -m yt_dlp`)
- `cookie_manager.py` / `cookie_health.py` — админский интерфейс загрузки и валидации cookies
- `logger.py` — настройка rotating file handler для логирования
- `cache_commands.py` — команды управления кэшем (очистка, статистика)
- `temp_file_manager.py` — управление временными файлами (автоочистка)

**WebUI** (`web/`): FastAPI + Jinja2 + Uvicorn. Логин, дашборд, список пользователей, детали пользователя. Порт через `WEB_PORT`.

**Поток обработки запроса**: URL → валидация (regex) → получение инфо → кнопки выбора формата → скачивание в ThreadPoolExecutor → проверка кэша (hit → file_id, miss → download & cache) → отправка через локальный Telegram Bot API → удаление временного медиа.

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

Обязательные переменные Docker: `TELEGRAM_TOKEN`, `ADMIN_IDS`, `TELEGRAM_API_ID`, `TELEGRAM_API_HASH`. Опциональные: `WEB_PORT`, `WEB_PASSWORD`, `YTDLP_*`. Шаблон в `.env.example`.
