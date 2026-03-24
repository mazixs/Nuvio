<p align="center">
  <img src="web/static/logo.svg" alt="Nuvio" width="64" height="64">
</p>

<h1 align="center">Nuvio</h1>

<p align="center">
  Telegram-бот для скачивания видео, фото-постов и аудио с YouTube, TikTok и Instagram<br>
  с поддержкой кэширования, аналитики и автоматического обновления yt-dlp.
</p>

---

## Возможности

- YouTube (видео + Shorts), TikTok, Instagram (посты, reels, фото-посты, карусели)
- TikTok и Instagram фото-посты: каждая картинка отправляется отдельным сообщением, звук — отдельно, если он есть
- Извлечение аудио (MP3 192k через FFmpeg для видео и отдельная звуковая дорожка для фото-постов)
- Кэширование file_id -- мгновенная повторная отправка через Telegram CDN
- Файлы >50MB загружаются на Gokapi и отправляются ссылкой
- Защита от спама (4 запроса за 5 секунд = cooldown 10 секунд)
- Админские команды: `/cache_stats`, `/search_cache`, `/cleanup_cache`, `/admin` (управление cookies)
- WebUI-дашборд аналитики (FastAPI + Jinja2)
- Автообновление yt-dlp (rolling-release, nightly channel)
- Готовность к headless/systemd-развертыванию (`init_env.sh` в качестве `ExecStartPre`)
- Поддержка Docker

### Фото-посты и карусели

- TikTok-ссылки вида `.../photo/...` скачиваются как набор изображений; если в посте есть звук, бот отправляет его отдельным сообщением после картинок
- Instagram фото-посты и карусели скачиваются как исходные картинки поста без сборки в видео
- Если у фото-поста нет звука, бот просто отправит картинки и не завершится ошибкой

## Скриншоты

<p align="center">
  <img src="docs/screenshots/dashboard-kpi_gh.png" alt="Dashboard KPI" width="900">
  <br><br>
  <img src="docs/screenshots/dashboard-charts_gh.png" alt="Dashboard Charts" width="900">
</p>

<p align="center">
  <img src="docs/screenshots/phone.png" alt="Telegram Bot" height="520">
</p>

---

## Быстрый старт

### Требования

- Python 3.13+
- FFmpeg
- Токен Telegram-бота ([@BotFather](https://t.me/BotFather))

### Установка

```bash
git clone https://github.com/mazixs/Nuvio.git
cd Nuvio
pip install -r requirements.txt
```

Скопируйте `.env.example` в `.secrets/.env` и заполните `TELEGRAM_TOKEN` и `ADMIN_IDS`:

```bash
mkdir -p .secrets
cp .env.example .secrets/.env
# отредактируйте .secrets/.env
python main.py
```

### Docker

```bash
cp .env.example .env
# Отредактируйте .env — укажите TELEGRAM_TOKEN, ADMIN_IDS, WEB_PASSWORD и WEB_PORT

# Локальная сборка
docker compose up --build

# Или из GHCR по версии (продакшен)
TAG=1.0.0 docker compose -f docker-compose.prod.yml up -d
```

Дашборд будет доступен на `http://localhost:<WEB_PORT>` (по умолчанию 8080).

Подробная настройка Docker (порты, пароли, volumes, cookies) — в [`docs/guides/deployment.md`](docs/guides/deployment.md#docker).

---

## CI/CD

- **CI** — автоматические тесты и линтинг на каждый push/PR в `main`
- **Релиз** — при пуше тега `v*` автоматически:
  - Прогоняются тесты
  - Генерируется changelog из коммитов
  - Создаётся GitHub Release с инструкцией по установке
  - Собирается Docker-образ и пушится в GHCR с тегами версий

Создание нового релиза:

```bash
git tag v1.0.0
git push origin v1.0.0
```

---

## Конфигурация

| Переменная | Обязательная | По умолчанию | Описание |
|---|---|---|---|
| `TELEGRAM_TOKEN` | да | -- | Токен бота от @BotFather |
| `ADMIN_IDS` | да | -- | Список ID администраторов через запятую |
| `GOKAPI_API_KEY` | нет | -- | API-ключ для Gokapi (отправка файлов >50MB) |
| `GOKAPI_BASE_URL` | нет | -- | Базовый URL сервера Gokapi |
| `WEB_USERNAME` | нет | `admin` | Логин для WebUI-дашборда |
| `WEB_PASSWORD` | нет | `changeme` | Пароль для WebUI-дашборда (**сменить!**) |
| `WEB_SECRET_KEY` | нет | авто | Ключ подписи сессий (см. ниже) |
| `WEB_PORT` | нет | `8080` | Порт WebUI-дашборда |
| `FAIL2BAN_RETRIES` | нет | `5` | Неудачных попыток логина до блокировки IP |
| `FAIL2BAN_TIME` | нет | `10m` | Время блокировки IP (`10m`, `1h`, `300`) |
| `LOG_LEVEL` | нет | `INFO` | Уровень логирования |
| `DOWNLOAD_WORKERS` | нет | `8` | Количество потоков в ThreadPoolExecutor |
| `BLOCKING_TASK_TIMEOUT` | нет | `600` | Таймаут блокирующих задач (секунды) |
| `YTDLP_AUTO_UPDATE` | нет | -- | Автообновление yt-dlp при старте |
| `YTDLP_RELEASE_CHANNEL` | нет | -- | Канал обновлений yt-dlp (stable/nightly) |
| `YTDLP_AUTO_UPDATE_TIMEOUT` | нет | -- | Таймаут операции обновления yt-dlp (секунды) |
| `YTDLP_CLI_FALLBACK` | нет | -- | Использовать CLI-режим yt-dlp как fallback |
| `YTDLP_CLI_TIMEOUT` | нет | -- | Таймаут CLI-вызова yt-dlp (секунды) |

### WEB_SECRET_KEY

Ключ подписи сессионных cookie. Если не задан — генерируется случайный при каждом старте, и после рестарта контейнера все сессии сбрасываются (нужно залогиниться заново).

Генерация:

```bash
python -c "import secrets; print(secrets.token_hex(32))"
```

Результат (64 hex-символа) вставить в `.env`:

```env
WEB_SECRET_KEY=a3f8b2c1d4e5f6a7b8c9d0e1f2a3b4c5d6e7f8a9b0c1d2e3f4a5b6c7d8e9f0a1
```

---

## Безопасность

- **SQL-инъекции** — все запросы к БД используют параметризацию (`?`), данные никогда не подставляются в SQL напрямую
- **Логин** — сравнение с env-переменными, без обращений к БД. Timing-safe сравнение (`hmac.compare_digest`) защищает от timing-атак
- **Brute-force** — fail2ban: `FAIL2BAN_RETRIES` попыток (по умолчанию 5) → блокировка IP на `FAIL2BAN_TIME` (по умолчанию 10 минут). Формат времени: `15m`, `1h`, `300`
- **Длина ввода** — логин и пароль ограничены 128 символами, обрезаются на входе
- **Сессии** — подписаны HMAC через `SessionMiddleware`, подделка без `WEB_SECRET_KEY` невозможна
- **Swagger/ReDoc** — отключены (`docs_url=None, redoc_url=None`), API-схема не раскрывается
- **Jinja2** — автоэкранирование HTML по умолчанию, защита от XSS
- **Ошибки** — не раскрывают внутреннюю структуру БД или стектрейсы пользователю

---

## Команды бота

| Команда | Описание | Доступ |
|---|---|---|
| `/start` | Приветственное сообщение и краткая справка | Все пользователи |
| `/help` | Подробная справка по использованию бота | Все пользователи |
| `/download <URL>` | Скачать видео, фото-пост или звук по ссылке (необязательна -- достаточно отправить ссылку) | Все пользователи |
| `/admin` | Панель администратора (управление cookies) | Администраторы |
| `/cache_stats` | Статистика кэша file_id | Администраторы |
| `/cleanup_cache` | Очистка устаревших записей кэша | Администраторы |
| `/search_cache` | Поиск по кэшу file_id | Администраторы |

---

## Структура проекта

```
Nuvio/
├── main.py
├── config.py
├── messages.py
├── utils/
│   ├── telegram_utils.py
│   ├── youtube_utils.py
│   ├── tiktok_instagram_utils.py
│   ├── media_processor.py
│   ├── video_cache.py
│   ├── analytics_db.py
│   ├── ytdlp_runtime.py
│   ├── gokapi_utils.py
│   ├── cookie_manager.py
│   ├── cookie_health.py
│   ├── logger.py
│   ├── cache_commands.py
│   └── temp_file_manager.py
├── web/
│   ├── app.py
│   ├── templates/
│   └── static/
├── tests/
├── docs/
├── .secrets/
├── .github/workflows/       # CI/CD (тесты, линтинг, релиз, GHCR)
├── Dockerfile
├── docker-compose.yml       # для локальной разработки
├── docker-compose.prod.yml  # production — тянет из GHCR
└── requirements.txt
```

| Модуль | Назначение |
|---|---|
| `main.py` | Точка входа: async event-loop, graceful shutdown, scheduled tasks |
| `config.py` | Парсинг переменных окружения с типизацией, пути к секретам |
| `messages.py` | Все пользовательские тексты и сообщения бота |
| `telegram_utils.py` | Хэндлеры бота: команды, callback-кнопки, обработка URL, отправка файлов |
| `youtube_utils.py` | Загрузка YouTube/Shorts через yt-dlp с cookie-поддержкой и smart retry |
| `tiktok_instagram_utils.py` | TikTok и Instagram: видео, фото-посты, карусели, запасные пути для картинок и отдельного звука |
| `media_processor.py` | FFmpeg: извлечение аудио, конвертация WebM в MP4, мерж аудио/видео |
| `video_cache.py` | SQLite-кэш file_id для мгновенной повторной отправки (WAL mode, TTL 90 дней) |
| `analytics_db.py` | SQLite-аналитика: таблицы users, events (WAL mode) |
| `ytdlp_runtime.py` | Автообновление yt-dlp, CLI fallback |
| `gokapi_utils.py` | Загрузка файлов >50MB на Gokapi (multipart, API key) |
| `cookie_manager.py` | Админский интерфейс загрузки cookies |
| `cookie_health.py` | Валидация и проверка здоровья cookies |
| `logger.py` | Настройка логирования (rotating file handler, 10MB, 5 backups) |
| `cache_commands.py` | Обработчики админских команд для управления кэшем |
| `temp_file_manager.py` | Управление временными файлами при скачивании |
| `web/app.py` | FastAPI-приложение: логин, дашборд, список и детали пользователей |

---

## Тестирование

```bash
pytest                              # все тесты
pytest tests/test_youtube_smoke.py -v  # один файл с подробным выводом
pytest --run-slow                   # включить медленные тесты
pytest --run-network                # включить сетевые тесты
pytest -k "test_name"               # запуск конкретного теста
```

Маркеры: `syntax`, `unit`, `integration`, `slow`. Тесты YouTube используют мокированный YoutubeDL (без сетевых запросов). Системные зависимости для тестов: FFmpeg, git.

---

## Документация

Подробная документация находится в директории [`docs/`](docs/):

- Архитектура проекта
- Руководство по развертыванию
- Справочник по конфигурации
- Устранение неполадок
- Коды ошибок

---

## Лицензия

Условия лицензирования указаны в файле [LICENSE](LICENSE).
