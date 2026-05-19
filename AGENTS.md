# AGENTS.md — Nuvio

Файл для AI-агентов, работающих с кодовой базой Nuvio. Проект написан преимущественно на русском языке: комментарии, docstrings, пользовательские сообщения и документация — всё на русском.

---

## Обзор проекта

**Nuvio** — асинхронный Telegram-бот для скачивания видео, фото-постов и аудио с YouTube, TikTok, Instagram, Rutube и VK Video. Поддерживает кэширование file_id для мгновенной повторной отправки, аналитику пользователей через WebUI-дашборд и автоматическое обновление yt-dlp.

Основные возможности:
- YouTube (видео + Shorts), TikTok, Instagram (посты, reels, фото-посты, карусели), Rutube, VK Video
- Извлечение аудио (MP3 192k через FFmpeg)
- Кэширование file_id в SQLite (TTL 90 дней)
- Файлы >50MB выгружаются на Gokapi и отправляются ссылкой
- Защита от спама (4 запроса за 5 секунд → cooldown 10 секунд)
- Админские команды: `/cache_stats`, `/search_cache`, `/cleanup_cache`, `/admin`
- WebUI-дашборд аналитики (FastAPI + Jinja2)
- Автообновление yt-dlp (канал nightly по умолчанию)

---

## Технологический стек

- **Язык**: Python 3.13+
- **Бот**: [python-telegram-bot](https://github.com/python-telegram-bot/python-telegram-bot) ≥22.0 (async)
- **Скачивание**: [yt-dlp](https://github.com/yt-dlp/yt-dlp) ≥2025.11.12
- **WebUI**: FastAPI ≥0.115 + Uvicorn + Jinja2
- **Базы данных**: SQLite (WAL mode) — две отдельные БД:
  - `video_cache.db` — кэш file_id
  - `analytics.db` — аналитика пользователей и событий
- **Обработка медиа**: FFmpeg (системная зависимость)
- **Контейнеризация**: Docker, docker-compose
- **Линтер**: ruff
- **Тестирование**: pytest ≥8.0

---

## Структура проекта

```
Nuvio/
├── main.py                      # Точка входа: event loop, хэндлеры, graceful shutdown
├── config.py                    # Парсинг env-переменных, пути к секретам, валидация
├── messages.py                  # Все пользовательские тексты (централизовано)
├── requirements.txt             # Зависимости Python
├── pytest.ini                  # Конфигурация pytest
├── Dockerfile                  # Сборка образа (python:3.13-slim + ffmpeg)
├── docker-compose.yml          # Локальная разработка (сборка из исходников)
├── docker-compose.prod.yml     # Продакшен (образы из GHCR)
├── init_env.sh                 # Headless bootstrap для systemd (git pull, pip install, миграция секретов)
│
├── utils/                       # Основная бизнес-логика
│   ├── __init__.py              # Пустые импорты (избегаем побочных эффектов при тестах)
│   ├── telegram_utils.py        # Хэндлеры бота, callback-кнопки, отправка файлов, спам-защита
│   ├── youtube_utils.py         # Загрузка YouTube/Shorts через yt-dlp
│   ├── tiktok_instagram_utils.py # TikTok и Instagram: видео, фото-посты, карусели
│   ├── rutube_vk_utils.py        # Rutube и VK Video: видео и аудио через yt-dlp
│   ├── media_processor.py       # FFmpeg: конвертация webm→mp4, извлечение аудио, сжатие
│   ├── video_cache.py           # SQLite-кэш file_id (WAL mode, TTL 90 дней)
│   ├── analytics_db.py          # SQLite-аналитика: таблицы users, events (WAL mode)
│   ├── ytdlp_runtime.py         # Автообновление yt-dlp, CLI fallback
│   ├── gokapi_utils.py          # Загрузка файлов >50MB на Gokapi
│   ├── cookie_manager.py        # Админский интерфейс загрузки cookies через Telegram
│   ├── cookie_health.py         # Валидация и проверка здоровья cookies
│   ├── logger.py                # Настройка логирования (rotating file handler, 10MB, 5 backups)
│   ├── cache_commands.py        # Обработчики админских команд управления кэшем
│   └── temp_file_manager.py     # Управление временными файлами при скачивании
│
├── web/                         # WebUI дашборд
│   ├── app.py                   # FastAPI-приложение: логин, дашборд, API
│   ├── __main__.py              # Точка входа: `python -m web`
│   ├── templates/               # Jinja2-шаблоны
│   └── static/                  # CSS, JS, изображения
│
├── tests/                       # Тесты pytest
│   ├── conftest.py              # Фикстуры, хуки, маркеры, заглушка yt_dlp
│   ├── test_syntax.py           # Синтаксический анализ всех .py файлов
│   ├── test_utils.py            # Структурные тесты (пути, модули)
│   ├── test_youtube_smoke.py    # Smoke tests youtube_utils с моком YoutubeDL
│   ├── test_cache_integration.py # Интеграционные тесты TelegramVideoCache
│   ├── test_main_polling.py     # Тесты классификации ошибок polling
│   ├── test_telegram_utils_error_classification.py # Тесты классификации YouTube-ошибок
│   └── test_audit_regressions.py # Регрессионные тесты бизнес-логики
│
├── docs/                        # Документация
│   ├── technical/architecture.md # Архитектурное описание
│   ├── development/contributing.md # Руководство для разработчиков
│   ├── guides/deployment.md     # Руководство по развёртыванию
│   ├── guides/configuration.md  # Справочник по конфигурации
│   ├── troubleshooting/common-issues.md # Устранение неполадок
│   ├── PRD.md                   # Product Requirements Document
│   └── screenshots/             # Скриншоты
│
└── .github/workflows/           # CI/CD
    ├── ci.yml                   # Линтинг (ruff), тесты, проверка Docker-сборки
    └── release.yml              # Релиз: тесты → changelog → GitHub Release → GHCR
```

---

## Сборка и запуск

### Локальная разработка

```bash
# Клонирование и установка зависимостей
git clone https://github.com/mazixs/Nuvio.git
cd Nuvio
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -r requirements.txt

# Настройка окружения
mkdir -p .secrets
cp .env.example .secrets/.env
# Отредактируйте .secrets/.env — заполните TELEGRAM_TOKEN и ADMIN_IDS

# Запуск бота
python main.py

# Запуск WebUI дашборда (в отдельном терминале)
python -m web
```

### Docker

```bash
# Локальная разработка
cp .env.example .env
# Заполните TELEGRAM_TOKEN, ADMIN_IDS, WEB_PASSWORD, WEB_PORT
docker compose up --build

# Продакшен (образ из GHCR)
TAG=1.0.0 docker compose -f docker-compose.prod.yml up -d
```

### Системные зависимости

- Python 3.13+
- FFmpeg (обязательно для конвертации и извлечения аудио)
- git (для `init_env.sh` и автообновления yt-dlp)

---

## Тестирование

### Команды

```bash
pytest                              # Все тесты (кроме slow/network)
pytest -v                          # Подробный вывод
pytest -k "test_name"              # Запуск конкретного теста
pytest tests/test_youtube_smoke.py -v  # Один файл
pytest --run-slow                  # Включить медленные тесты
pytest --run-network               # Включить сетевые тесты
pytest -m "syntax or unit"         # Только syntax и unit (как в CI)
```

### Маркеры pytest

| Маркер | Описание | Требует флага |
|---|---|---|
| `syntax` | Синтаксическая корректность и импорты всех .py файлов | Нет |
| `unit` | Модульные тесты с моками | Нет |
| `integration` | Интеграционные тесты (SQLite) | Нет |
| `slow` | Медленные тесты | `--run-slow` |
| `network` | Тесты, требующие интернет | `--run-network` |

> Автоматически `network` тесты также помечаются как `slow`.

### Принципы тестирования

- YouTube тесты используют мокированный `YoutubeDL` (без сетевых запросов).
- Реальные cookie-файлы автоматически отключаются в тестах через monkeypatch.
- Фикстуры и hooks находятся в `tests/conftest.py`.
- Тестовая заглушка `yt_dlp` создаётся в `conftest.py` для сред без установленной библиотеки.

---

## Стиль кода и соглашения

### Линтинг

В CI используется **ruff**:

```bash
pip install ruff
ruff check --output-format=github .
```

### Язык и тексты

- **Все user-facing сообщения** вынесены в `messages.py`. Не хардкодить тексты в хэндлерах.
- Комментарии и docstrings — на русском языке.
- Код (переменные, функции, классы) — на английском.

### Обработка ошибок

- **Коды ошибок**: формат `PREFIX-CATEGORY-RANDOM` (например, `YT-ACCESS-A1B2C3`).
  - Префиксы: `YT` (YouTube), `TT` (TikTok), `IG` (Instagram), `RU` (Rutube), `VK` (VK), `TG` (Telegram), `FILE`, `BOT`.
  - Категории: `ACCESS`, `NETWORK`, `TIMEOUT`, `FORMAT_UNAVAILABLE`, `FFMPEG_MISSING`, `EXTRACTOR_RUNTIME`, `UNKNOWN`.
- Пользователю показывается только код ошибки; traceback уходит в логи.
- Используется `Exception.add_note()` для обогащения исключений контекстом.

### Асинхронность

- Бот полностью async на базе `python-telegram-bot`.
- Блокирующие операции (yt-dlp, FFmpeg) выполняются в `ThreadPoolExecutor`.
- `DOWNLOAD_WORKERS=8` по умолчанию (настраивается через env).
- `BLOCKING_TASK_TIMEOUT=600` секунд по умолчанию.

### Логирование

- Используется `utils/logger.py` (`setup_logger`).
- Все логгеры пишут в единый файл `logs/bot.log` с ротацией (10MB, 5 backups).
- Уровень логирования задаётся через `LOG_LEVEL` env var.
- **Запрещено** использовать `print()` в production-коде (есть тест `test_no_print_statements`).

### Базы данных

- SQLite с WAL mode (`PRAGMA journal_mode=WAL`) для конкурентного доступа.
- `video_cache.db` — кэш file_id для мгновенной повторной отправки.
- `analytics.db` — аналитика: пользователи, события, метрики retention/churn.
- Все SQL-запросы используют параметризацию (`?`), никакой конкатенации строк в SQL.

### Конфигурация

- Все настройки — через переменные окружения, парсятся в `config.py`.
- Секреты хранятся в `.secrets/` (preferred) или legacy в корне проекта.
- Функция `resolve_secret_path()` обеспечивает обратную совместимость.

---

## Безопасность

- **SQL-инъекции**: все запросы к БД параметризованы.
- **Аутентификация WebUI**: сравнение с env-переменными, timing-safe (`hmac.compare_digest`).
- **Brute-force**: fail2ban на WebUI — `FAIL2BAN_RETRIES` попыток → блокировка IP на `FAIL2BAN_TIME`.
- **Длина ввода**: логин и пароль ограничены 128 символами, обрезаются на входе.
- **Сессии**: подписаны HMAC через `SessionMiddleware`, подделка без `WEB_SECRET_KEY` невозможна.
- **Swagger/ReDoc**: отключены (`docs_url=None, redoc_url=None`).
- **Jinja2**: автоэкранирование HTML по умолчанию.
- **Ошибки**: не раскрывают внутреннюю структуру БД или стектрейсы пользователю.
- **Cookies**: файлы cookies сохраняются с правами `0o600` (только владелец).

---

## CI/CD

### CI (`.github/workflows/ci.yml`)

Запускается на push/PR в `main` и `develop`:
1. **Линтинг** — `ruff check --output-format=github .`
2. **Тесты** — `pytest tests/ -v -m "syntax or unit" --tb=short` на Python 3.13 и 3.14
3. **Docker build** — проверка сборки образа (зависит от lint и test)

### Release (`.github/workflows/release.yml`)

Запускается на push тега `v*`:
1. **Тесты** — прогон syntax и unit тестов.
2. **GitHub Release** — генерация changelog из коммитов по категориям (feat/add, fix/исправл, остальное), создание релиза с инструкцией по установке.
3. **Docker → GHCR** — сборка и пуш образа с тегами версий (`latest`, `major.minor`, `major`).

---

## Ключевые архитектурные паттерны

1. **Async + ThreadPoolExecutor**: блокирующие операции yt-dlp/FFmpeg не блокируют event loop.
2. **Callback-сессии**: пользовательские inline-кнопки привязаны к сессиям через токены (`s|{token}|{scope}|{action}`). Максимум 5 активных сессий на пользователя.
3. **Кэш file_id**: при первой отправке файла в Telegram сохраняется `file_id`; повторные запросы того же URL отправляются мгновенно через CDN.
4. **Smart retry**: экспоненциальный backoff при сетевых таймаутах yt-dlp; fallback на CLI (`python -m yt_dlp`) при сбоях встроенного API.
5. **Cookies-first**: для YouTube сначала пробуем с cookies, затем без них; для TikTok/Instagram — аналогично.
6. **Фото-посты**: TikTok-ссылки вида `/photo/` и Instagram карусели скачиваются как набор изображений; аудио отправляется отдельным сообщением, если есть.
7. **Rolling-release yt-dlp**: бот автоматически обновляет yt-dlp при старте через `pip install --upgrade --pre yt-dlp[default]` (канал nightly).

---

## Переменные окружения

| Переменная | Обязательная | По умолчанию | Описание |
|---|---|---|---|
| `TELEGRAM_TOKEN` | да | — | Токен бота от @BotFather |
| `ADMIN_IDS` | да | — | Список ID администраторов через запятую |
| `GOKAPI_API_KEY` | нет | — | API-ключ для Gokapi (>50MB файлы) |
| `GOKAPI_BASE_URL` | нет | — | Базовый URL сервера Gokapi |
| `WEB_USERNAME` | нет | `admin` | Логин для WebUI |
| `WEB_PASSWORD` | нет | `changeme` | Пароль для WebUI (**сменить!**) |
| `WEB_SECRET_KEY` | нет | авто | Ключ подписи сессий (64 hex-символа) |
| `WEB_PORT` | нет | `8080` | Порт WebUI |
| `FAIL2BAN_RETRIES` | нет | `5` | Попыток логина до блокировки IP |
| `FAIL2BAN_TIME` | нет | `10m` | Время блокировки (`10m`, `1h`, `300`) |
| `LOG_LEVEL` | нет | `INFO` | Уровень логирования |
| `DOWNLOAD_WORKERS` | нет | `8` | Потоков в ThreadPoolExecutor |
| `BLOCKING_TASK_TIMEOUT` | нет | `600` | Таймаут блокирующих задач (сек) |
| `YTDLP_AUTO_UPDATE` | нет | `true` | Автообновление yt-dlp при старте |
| `YTDLP_RELEASE_CHANNEL` | нет | `nightly` | Канал: `stable`, `nightly`, `master` |
| `YTDLP_AUTO_UPDATE_TIMEOUT` | нет | `240` | Таймаут обновления yt-dlp (сек) |
| `YTDLP_CLI_FALLBACK` | нет | `true` | CLI fallback при сбое API |
| `YTDLP_CLI_TIMEOUT` | нет | `900` | Таймаут CLI-вызова yt-dlp (сек) |

---

## Что изменять осторожно

- **`messages.py`**: при изменении сообщений проверяйте, что они не превышают лимиты Telegram (4096 символов для обычных сообщений).
- **`config.py`**: добавление новых env-переменных требует обновления `.env.example` и `README.md`.
- **`utils/telegram_utils.py`**: файл ~120KB, содержит всю логику взаимодействия с пользователем. Изменения в callback_data могут сломать существующие сессии.
- **`utils/youtube_utils.py`**: логика fallback (cookies → без cookies → CLI) чувствительна к порядку операций. Любые изменения должны сохранять стратегию отката.
- **`tests/conftest.py`**: заглушка yt_dlp используется многими тестами. Изменения здесь могут повлиять на весь тестовый набор.
- **SQLite схемы**: при изменении схем `video_cache.db` или `analytics.db` учитывайте WAL mode и необходимость миграций для существующих установок.
