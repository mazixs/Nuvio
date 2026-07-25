# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Nuvio — Telegram-бот для скачивания медиа с YouTube, TikTok, Instagram,
Rutube и VK Video. Python 3.14+, async-архитектура на python-telegram-bot.
Включает WebUI-дашборд аналитики и локальный Telegram Bot API.

Проект русскоязычный: комментарии, docstrings, user-facing тексты и
документация — на русском; идентификаторы кода — на английском.

**`AGENTS.md`** содержит расширенный справочник (полная таблица env-переменных,
детали безопасности, разбор CI/CD). Читай его, когда нужны подробности,
которых нет здесь.

## Commands

```bash
# Окружение (в репозитории есть .venv)
python -m pip install --requirement requirements-dev.txt

# Запуск бота
python main.py

# WebUI-дашборд (отдельный процесс)
python -m web

# Docker: сборка из исходников
docker compose --env-file .secrets/.env -f compose.yaml -f compose.dev.yaml up --build
# Docker: готовый образ из GHCR
docker compose --env-file .secrets/.env up -d

# Тесты — весь набор проходит за ~5 секунд без сети, запускай его целиком
pytest
pytest tests/test_youtube_smoke.py  # один файл
pytest -k "test_name"               # один тест
pytest -m syntax                    # только синтаксис + ruff

# Линтинг (та же команда, что в CI)
ruff check --output-format=github .

# Покрытие (порог CI)
coverage run --branch -m pytest tests/
coverage report --fail-under=40
```

Прямой запуск использует облачный Bot API с лимитом 50 МБ. Полный
Docker-стек отправляет файлы до 2 ГБ через локальный Bot API.

## Architecture

**Точка входа**: `main.py` — async event-loop, graceful shutdown
(SIGINT/SIGTERM), регистрация хэндлеров, периодические job'ы (очистка кэша
раз в сутки, `VACUUM` раз в неделю, рассылка CSI-опросов). `.env` грузится из
`.secrets/.env`; `.env.local` и корневой `.env` — только для обратной
совместимости. **Импорты в `main.py` намеренно стоят ниже `load_dotenv()`** —
для этого в `pyproject.toml` включён per-file-ignore `E402`. Не переставляй их
наверх.

**Конфигурация**: `config.py` — типизированный парсинг env. `resolve_secret_path()`
предпочитает `.secrets/<file>`, но поддерживает legacy-файлы в корне.
`MAX_FILE_SIZE_MB` жёстко зажимается режимом доставки: 2000 при
`TELEGRAM_LOCAL_MODE=true`, иначе 50 — независимо от того, что задано в
`TELEGRAM_MAX_FILE_SIZE_MB`. При локальном режиме `validate_config()` требует
`http://` в URL Bot API.

**Основные модули в `utils/`**:
- `telegram_utils.py` (~2800 строк) — хэндлеры, координация пользовательского потока, антиспам, коды ошибок, отправка файлов
- `callback_fsm.py` — `CallbackEvent.parse()` (единственный парсер callback-данных) и `SessionStore` (LRU, максимум 5 сессий на пользователя)
- `platform_actions.py` — чистые решения платформенных действий и ключей кэша
- `file_delivery.py` — выбор Telegram-метода отправки по расширению файла
- `public_errors.py` — безопасная классификация ошибок в user-facing текст
- `ytdlp_common.py` — общие для всех загрузчиков сетевые опции yt-dlp, exponential backoff, проверка лимита размера
- `fast_path.py` — общие примитивы быстрых путей: `FastPathUnavailable` и проверка ссылки по allowlist доменов (одна реализация на все платформы намеренно — это проверка безопасности)
- `instagram_fast_path.py` — чистый разбор GraphQL-ответа Instagram: прямая ссылка из `video_versions` вместо yt-dlp, отказ на каруселях и фото-постах, allowlist доменов Meta. Включается `INSTAGRAM_FAST_PATH`, при отказе — откат на yt-dlp
- `tiktok_fast_path.py` — чистый разбор ответа резолвера TikTok: прямая H.264-ссылка вместо yt-dlp, признак «`music` — это звук видео, а не библиотечный трек», проверка ссылок по allowlist доменов (`is_allowed_media_url`). Включается `TIKTOK_FAST_PATH`, при отказе — откат на yt-dlp
- `youtube_utils.py` — YouTube/Shorts через yt-dlp с cookie-поддержкой и smart retry
- `tiktok_instagram_utils.py` (~2100 строк) — TikTok (множественные API-хосты, backoff) и Instagram (rate-limit aware, cookies для приватных профилей, фото-посты и карусели)
- `rutube_vk_utils.py` — Rutube и VK Video; для VK стратегия `best[protocol=https]` в обход фрагментированного HLS
- `media_processor.py` — FFmpeg: извлечение аудио (MP3 192k), конвертация WebM→MP4, мерж аудио/видео
- `video_cache.py` — SQLite-кэш `file_id`, ключ `(url, format_id)`, WAL, TTL 90 дней
- `analytics_db.py` — SQLite-аналитика: `users`, `events`, `csi_responses` (WAL, миграции через `PRAGMA table_info`)
- `ytdlp_runtime.py` — авто-обновление yt-dlp, CLI fallback (`python -m yt_dlp`)
- `cookie_manager.py` / `cookie_health.py` — админский интерфейс загрузки и валидации cookies
- `logger.py`, `cache_commands.py`, `temp_file_manager.py`

**Расположение БД** зависит от `DATA_DIR`: локально — корень репозитория
(`analytics.db`, `telegram_cache.db`), в Docker — том `bot-data` на `/app/data`.
Временные медиа — в `TEMP_DIR` (локально `./temp`, в Docker общий том
`shared-media` на `/app/media`, чтобы Bot API читал файлы по абсолютному пути).

**WebUI** (`web/`): FastAPI + Jinja2 + Uvicorn. Логин с PBKDF2 и timing-safe
сравнением, in-memory fail2ban с уведомлением админов в Telegram, `/health`
для healthcheck'ов Compose и CI-smoke. Swagger/ReDoc отключены. Порт — `WEB_PORT`.

**Поток обработки запроса**: URL → валидация (regex) → `get_video_info` в
ThreadPoolExecutor → inline-меню выбора формата → callback → проверка кэша
(hit → `file_id`, miss → download в пуле потоков → кэширование) → отправка →
удаление временного медиа и уничтожение сессии.

**FSM** — неявная, сессионная: состояние = наличие inline-клавиатуры в
конкретном сообщении. Формат callback-данных: `s|{token}|main|{action}`,
`s|{token}|format|{action}|{value}`, `csi|{rating}`. Сессии живут в
`context.user_data["sessions"]` и **не переживают перезапуск процесса**.
Разбор архитектуры и список известных узких мест — `docs/technical/fsm-architecture.md`.

## Key Patterns

- **Async + ThreadPoolExecutor**: блокирующие операции (yt-dlp, FFmpeg) выполняются в пуле (`DOWNLOAD_WORKERS=8`, `BLOCKING_TASK_TIMEOUT=600`)
- **match-case**: разбор callback-событий, выбор платформы/формата
- **Exception.add_note()**: обогащение ошибок контекстом
- **Коды ошибок**: `<PREFIX>-<CATEGORY>-<RANDOM6>` — префиксы YT/TT/IG/RU/VK/TG/FILE/BOT, категории ACCESS/NETWORK/TIMEOUT/FORMAT_UNAVAILABLE/FFMPEG_MISSING/EXTRACTOR_RUNTIME/UNKNOWN. Пользователь видит только безопасный текст + код; cookies, пути и traceback уходят в админский лог
- **Cookies-first fallback**: сначала с cookies → без cookies → CLI `python -m yt_dlp`. Порядок критичен
- **SQLite WAL mode** во всех БД; все запросы параметризованы (`?`)
- **Сообщения**: все user-facing тексты в `messages.py`, не хардкодить в хэндлерах
- **Антиспам**: 4 запроса за 5 секунд → cooldown 10 секунд (`_SPAM_*` в `telegram_utils.py`)
- **Логирование**: rotating file handler (10MB, 5 backups) → `logs/bot.log`

## Testing

Маркеры (`--strict-markers` включён): `syntax`, `unit`, `integration`.
Fixtures и hooks — `tests/conftest.py`, там же лёгкая заглушка `yt_dlp` для
сред без библиотеки. Тесты YouTube используют мокированный `YoutubeDL` (без
сети), реальные cookie-файлы отключаются через monkeypatch. Системные
зависимости: FFmpeg, git, Docker (только для CI-этапа сборки).

### Тесты-контракты на файлы конфигурации

Значительная часть набора проверяет **текст** конфигов, а не поведение кода.
Правка инфраструктуры почти всегда требует синхронной правки этих тестов:

| Меняешь | Ломается |
|---|---|
| `.github/workflows/*.yml` | `test_workflow_quality_gates.py` — требует pinning Actions по полному 40-символьному SHA, `permissions: contents: read`, `concurrency`, ровно 3 `timeout-minutes` на workflow, порядок job'ов `test → docker → release`, Trivy/actionlint по digest, smoke-проверки по canonical digest |
| `Dockerfile`, `Dockerfile.telegram-bot-api` | базовые образы обязаны быть пришпилены по `@sha256:`; ревизия telegram-bot-api зафиксирована конкретным коммитом |
| `requirements*.in/.txt` | `test_environment_template.py` и `test_workflow_quality_gates.py` — сверяют точные версии прямых зависимостей, наличие `--hash=sha256:` и отсутствие dev-инструментов в runtime |
| `pyproject.toml` | ожидается ровно `select = ["E4", "E7", "E9", "F"]` — политика намеренно узкая, чтобы обновление ruff не включало новые правила молча |
| `.env.example` | `test_environment_template.py` — обязательные ключи и `YTDLP_AUTO_UPDATE=false` |
| `compose.yaml` / `compose.dev.yaml` | `test_compose_configuration.py` — локальный Bot API, общий том `shared-media`, порт 8081 не публикуется наружу, legacy `docker-compose*.yml` отсутствуют |
| `pytest.ini`, `messages.py`, `video_cache.py` | `test_dead_code_contract.py` — запрещает вернуть маркеры `slow:`/`network:`, флаги `--run-slow`/`--run-network` и удалённые константы/хелперы |
| `.github/dependabot.yml` | ожидаются группы minor/patch для всех трёх экосистем |

Кроме того, `tests/test_ruff.py` запускает `ruff check` внутри pytest, а
`tests/test_syntax.py` запрещает `print()` в production-коде и звёздочные
импорты во всём проекте.

## Environment

Обязательные переменные Docker: `TELEGRAM_TOKEN`, `ADMIN_IDS`, `TELEGRAM_API_ID`,
`TELEGRAM_API_HASH`. Для прямого запуска достаточно `TELEGRAM_TOKEN` и `ADMIN_IDS`.
Опциональные: `WEB_*`, `FAIL2BAN_*`, `DATA_DIR`, `TEMP_DIR`, `YTDLP_*`,
`TELEGRAM_LOCAL_MODE`, `TELEGRAM_BOT_API_*`. Шаблон — `.env.example`,
полная таблица с значениями по умолчанию — в `AGENTS.md`.

## Что изменять осторожно

- **`messages.py`** — лимит Telegram 4096 символов на сообщение
- **`config.py`** — новая env-переменная требует обновления `.env.example` (иначе падает тест) и `README.md`
- **`callback_fsm.py` + `telegram_utils.py`** — смена формата `callback_data` ломает уже отправленные пользователям кнопки; сопровождай тестами разбора событий
- **`youtube_utils.py`** — цепочка fallback чувствительна к порядку операций
- **`tests/conftest.py`** — заглушка `yt_dlp` используется всем набором
- **Схемы SQLite** — учитывай WAL и необходимость миграции существующих установок (образец — миграция `last_csi_sent` в `analytics_db.py`)
