# Руководство для разработчиков

## Подготовка окружения

```bash
git clone https://github.com/mazixs/Nuvio.git
cd Nuvio
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Системные зависимости: FFmpeg, git, Python 3.13+

## Структура кодовой базы

- `main.py` — точка входа, регистрация хэндлеров, event loop
- `config.py` — парсинг конфигурации из env
- `messages.py` — все пользовательские тексты (централизовано)
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

- `syntax` — синтаксическая корректность и импорты
- `unit` — юнит-тесты с моками
- `integration` — интеграционные (SQLite кэш)
- `slow` — медленные тесты (пропускаются без `--run-slow`)

### Принципы тестирования

- YouTube тесты используют мокированный YoutubeDL (без сети)
- Реальные cookies автоматически отключаются в тестах
- Fixtures и hooks в `tests/conftest.py`

## Соглашения по коду

### Тексты пользователю

Все user-facing сообщения — в `messages.py`. Не хардкодить тексты в хэндлерах.

### Обработка ошибок

- Коды ошибок: формат `PREFIX-CATEGORY-RANDOM` (например `YT-ACCESS-A1B2C3`)
- Prefixes: `YT`, `TT`, `IG`, `TG`, `FILE`, `BOT`
- Пользователю показывается только код, traceback уходит в логи
- Используется `Exception.add_note()` для контекста

### Асинхронность

- Блокирующие операции (yt-dlp, ffmpeg) выполняются в `ThreadPoolExecutor`
- `DOWNLOAD_WORKERS=8` по умолчанию
- `match-case` для выбора платформы/формата

### Базы данных

- SQLite с WAL mode для конкурентного доступа
- `video_cache.db` — кэш file_id
- `analytics.db` — аналитика пользователей

### Логирование

- Используется `utils/logger.py` (`setup_logger`)
- Rotating file handler: 10MB, 5 backups
- Уровень через `LOG_LEVEL` env var

## Docker

```bash
docker-compose up --build
```

Два сервиса: `bot` и `web`. Общий volume `bot-data` для аналитической БД.
