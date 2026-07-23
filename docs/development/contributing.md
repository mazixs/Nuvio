# Руководство для разработчиков

## Подготовка окружения

```bash
git clone https://github.com/mazixs/Nuvio.git
cd Nuvio
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Системные зависимости: FFmpeg, git, Python 3.14+

## Структура кодовой базы

- `main.py` — точка входа, регистрация хэндлеров, event loop, scheduled tasks
- `config.py` — парсинг конфигурации из env
- `messages.py` — все пользовательские тексты (централизовано)
- `pyproject.toml` — конфигурация ruff (per-file-ignores, правила линтинга)
- `utils/` — основная бизнес-логика
- `web/` — FastAPI WebUI дашборд
- `tests/` — pytest тесты
- `docs/` — документация

## Тестирование

```bash
# Все тесты
pytest

# Конкретный файл
pytest tests/test_youtube_smoke.py -v

# По имени
pytest -k "test_name"

# С медленными тестами
pytest --run-slow

# С сетевыми тестами
pytest --run-network
```

### Маркеры pytest

- `syntax` — синтаксическая корректность, импорты и линтинг (ruff)
- `unit` — юнит-тесты с моками
- `integration` — интеграционные (SQLite кэш, CSI)
- `slow` — медленные тесты (пропускаются без `--run-slow`)
- `network` — тесты, требующие интернет (пропускаются без `--run-network`)

### Принципы тестирования

- YouTube тесты используют мокированный YoutubeDL (без сети)
- Реальные cookies автоматически отключаются в тестах
- Fixtures и hooks в `tests/conftest.py`

## Соглашения по коду

### Тексты пользователю

Все user-facing сообщения — в `messages.py`. Не хардкодить тексты в хэндлерах.

### Обработка ошибок

- Коды ошибок: формат `PREFIX-CATEGORY-RANDOM` (например `YT-ACCESS-A1B2C3`)
- Prefixes: `YT` (YouTube), `TT` (TikTok), `IG` (Instagram), `RU` (Rutube), `VK` (VK Video), `TG` (Telegram), `FILE`, `BOT`
- Пользователю показывается только код, traceback уходит в логи
- Используется `Exception.add_note()` для контекста

### Асинхронность

- Блокирующие операции (yt-dlp, ffmpeg) выполняются в `ThreadPoolExecutor`
- `DOWNLOAD_WORKERS=8` по умолчанию
- `match-case` для выбора платформы/формата

### Базы данных

- SQLite с WAL mode для конкурентного доступа (`PRAGMA journal_mode=WAL`, `synchronous=NORMAL`, `cache_size=-64000`)
- Ручные транзакции: `_cursor_read()` для SELECT, `_cursor_write()` с `BEGIN IMMEDIATE` для записи
- `video_cache.db` — кэш file_id
- `analytics.db` — аналитика: пользователи, события, CSI-ответы

### Логирование

- Используется `utils/logger.py` (`setup_logger`)
- Rotating file handler: 10MB, 5 backups
- Уровень через `LOG_LEVEL` env var

## Docker

```bash
docker compose --env-file .secrets/.env \
  -f compose.yaml -f compose.dev.yaml up --build
```

Два сервиса: `bot` и `web`. Общий volume `bot-data` для аналитической БД.
