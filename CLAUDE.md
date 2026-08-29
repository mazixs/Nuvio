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
- `url_delivery.py` — чистое решение «отдать ссылку вместо файла»: лимиты 20 МБ на медиа и 5 МБ на фото, allowlist доменов (собирается из allowlist'ов быстрых путей, чтобы не разойтись), поиск прямой ссылки на выбранный формат. Скачивает ссылку инфраструктура Telegram, а не локальный Bot API, — поэтому лимит не снимается режимом `--local`
- `instagram_fast_path.py` — чистый разбор GraphQL-ответа Instagram: прямая ссылка из `video_versions` вместо yt-dlp, отказ на каруселях и фото-постах, allowlist доменов Meta. Включается `INSTAGRAM_FAST_PATH`, при отказе — откат на yt-dlp
- `tiktok_fast_path.py` — чистый разбор ответа резолвера TikTok: прямая H.264-ссылка вместо yt-dlp, признак «`music` — это звук видео, а не библиотечный трек», проверка ссылок по allowlist доменов (`is_allowed_media_url`). Включается `TIKTOK_FAST_PATH`, при отказе — откат на yt-dlp
- `youtube_utils.py` — YouTube/Shorts через yt-dlp с cookie-поддержкой и smart retry
- `tiktok_instagram_utils.py` (~2100 строк) — TikTok (множественные API-хосты, backoff) и Instagram (rate-limit aware, cookies для приватных профилей, фото-посты и карусели)
- `rutube_vk_utils.py` — Rutube и VK Video; для VK стратегия `best[protocol=https]` в обход фрагментированного HLS
- `media_processor.py` — FFmpeg: извлечение аудио (MP3 192k), конвертация WebM→MP4, мерж аудио/видео
- `video_cache.py` — SQLite-кэш `file_id`, ключ `(url, format_id)`, WAL, TTL 90 дней
- `analytics_db.py` — SQLite-аналитика: `users`, `events`, `csi_responses`, `settings` (WAL, миграции через `PRAGMA table_info`). `settings` — общее место записи для бота и WebUI: это разные процессы, у которых совпадает только том `DATA_DIR`. Настройка `csi_interval_days` читается на каждой рассылке, поэтому смена частоты опроса не требует перезапуска; испорченное или выходящее за диапазон 1–365 значение молча заменяется на 14 дней — рассылка не должна падать из-за настройки
- `ytdlp_runtime.py` — авто-обновление yt-dlp, CLI fallback (`python -m yt_dlp`)
- `cookie_manager.py` / `cookie_health.py` — админский интерфейс загрузки и валидации cookies. Проба читает `PROBE_BODY_READ_BYTES` тела ответа: признаки неавторизованности у YouTube лежат за 18-й тысячей байт, а на редирект `consent.youtube.com` попадает только незалогиненный, поэтому маркер нужен и по URL
- `cookie_workfile.py` — рабочая копия cookie-файла. yt-dlp перезаписывает переданный `cookiefile` содержимым своего jar после запроса, поэтому платформа может удалить из файла cookie своим ответом: замерено, что один прогон YouTube убирает `YSC` (15 записей становятся 14), а у TikTok и Instagram на тех же прогонах не пропадает ничего. Загрузчики отдают yt-dlp копию в `DATA_DIR/cookie-work`, а загруженный админом оригинал остаётся целым. Ни один загрузчик не должен передавать оригинал напрямую
- `download_report.py` — побочный канал диагностики на сессию: хвост строк вывода yt-dlp и формат, который загрузчик реально принёс. Канал именно побочный, потому что загрузчики отдают путь к файлу, и менять их сигнатуры ради диагностики значило бы тронуть все пять каскадов. Читают его краш-репорт и выбор ключа кэша; доступ под замком — состояние делят event loop и потоки пула
- `canary.py` — плановая проверка YouTube: качает настоящим путём бота продакшн-опциями и **мимо кэша `file_id`**, ролик берёт длиннее пары минут. Все три условия обязательны: маленький `Range` отдавал 206 при мёртвом продакшн-запросе на 10 МБ, кэш отдал бы готовый `file_id`, а короткий ролик проходил при полностью сломанном YouTube. При провале зовёт админов и один раз в сутки пробует `ensure_latest_yt_dlp(force=True)` с повторной проверкой. Включается `CANARY_ENABLED`
- `logger.py`, `cache_commands.py`, `temp_file_manager.py`

**Расположение БД** зависит от `DATA_DIR`: локально — корень репозитория
(`analytics.db`, `telegram_cache.db`), в Docker — том `bot-data` на `/app/data`.
Временные медиа — в `TEMP_DIR` (локально `./temp`, в Docker общий том
`shared-media` на `/app/media`, чтобы Bot API читал файлы по абсолютному пути).

**WebUI** (`web/`): FastAPI + Jinja2 + Uvicorn. Логин с PBKDF2 и timing-safe
сравнением, in-memory fail2ban с уведомлением админов в Telegram, `/health`
для healthcheck'ов Compose и CI-smoke. Swagger/ReDoc отключены. Порт — `WEB_PORT`.
`/settings` — единственная страница, которая пишет: любой новый POST-роут здесь
опирается на cookie сессии со `SameSite=lax` вместо отдельного CSRF-токена, и
отдельная защита понадобится, если у cookie когда-нибудь сменят `same_site`.

**Поток обработки запроса**: URL → валидация (regex) → `get_video_info` в
ThreadPoolExecutor → inline-меню выбора формата → callback → проверка кэша
(hit → `file_id`, miss → **попытка отдать прямую ссылку**, при отказе download в
пуле потоков → кэширование) → отправка → удаление временного медиа и
уничтожение сессии.

**Доставка по ссылке** применяется до скачивания: медиа до 20 МБ (фото до 5 МБ)
с разрешённого домена уходит в Telegram ссылкой, диск не задействуется вовсе.
Работает для видео и звука TikTok, видео Instagram, фото-постов обеих платформ
(целиком или никак) и progressive-форматов YouTube; audio-only YouTube, VK и Rutube так отдать нельзя — это измерено, см.
`docs/technical/latency-disk-network-research.md` §8. Любой отказ Telegram —
не ошибка, а сигнал идти обычным путём; отказ запоминается на 15 минут по паре
«домен + вид медиа», потому что CDN может отказывать Telegram, оставаясь
доступным для нас.

**FSM** — неявная, сессионная: состояние = наличие inline-клавиатуры в
конкретном сообщении. Формат callback-данных: `s|{token}|main|{action}`,
`s|{token}|format|{action}|{value}`, `csi|{rating}`. Сессии живут в
`context.user_data["sessions"]` и **не переживают перезапуск процесса**.
Разбор архитектуры и список известных узких мест — `docs/technical/fsm-architecture.md`.

## Key Patterns

- **Async + ThreadPoolExecutor**: блокирующие операции (yt-dlp, FFmpeg) выполняются в пуле (`DOWNLOAD_WORKERS=8`, `BLOCKING_TASK_TIMEOUT=600`). `run_blocking` по таймауту помечает сессию отменённой: `wait_for` отменяет только ожидание, а поток продолжает качать — замерено, что загрузка дожила до конца через 4 минуты после своего таймаута. Поэтому вызовы, принадлежащие сессии, обязаны передавать `session_id=`
- **Параллельная обработка апдейтов**: `UPDATE_CONCURRENCY` в `main.py` (32, заведомо больше `DOWNLOAD_WORKERS`). До её включения PTB обрабатывал апдейты строго по одному, и нажатие «Отменить» лежало в очереди всё скачивание — механизм отмены был исправен, но сигнал до него не доходил, поэтому тесты отмены оставались зелёными. Отсюда правило: **хэндлер не имеет права держать фетчер апдейтов дольше необходимого**; блокирующая работа — только через `run_blocking` с `session_id=`. Долгую фоновую работу запускай через `context.application.create_task` — PTB проводит её исключения через глобальный обработчик ошибок и дожидается задач при `stop()`. Тест доставки — `tests/test_concurrent_update_delivery.py`, он падает, если параллельность снова выключат
- **Правка сообщений**: только через `safe_edit_message_text`. Она считает неошибкой два исхода — «текст не изменился» и «сообщение удалено пользователем». Прямой `query.edit_message_text` в обработчике ошибки даёт каскад: правка падает, сообщение об этой ошибке падает так же, и всё уезжает в глобальный хэндлер
- **Отправка видео обязана нести `width`, `height` и `duration`**. Bot API эти поля не вычисляет, а подставляет ноль (`Client.cpp`, `process_send_video_query`), после чего размеры пытается определить сервер Telegram — и на тяжёлых файлах сдаётся, записывая `320x320`. Признак срыва виден в ответе API: вместе с размерами пропадает миниатюра. Клиент iOS рисует строго по этим атрибутам, поэтому 16:9 сжимается по горизонтали, а 9:16 растягивается по ширине; Android и Desktop измеряют поток сами, и дефект у них не виден. Замерено: 124 КБ и 4.9 МБ определяются верно, 35.8 МБ дают квадрат — **проверять такие гипотезы только на настоящих файлах, синтетический ролик даёт ложноотрицательный результат**. Разбор — `docs/technical/adr-002-ios-video-compatibility.md`
- **В Telegram уезжает только H.264 8 бит (`yuv420p`)**. Кодек проверяется у готового файла через `ffprobe`, а не по расширению: `merge_output_format: "mp4"` заставляет yt-dlp класть VP9 в MP4 (в `get_compatible_ext` явный `preferences=['mp4']` выставляет `allow_mkv=False`), поэтому проверка `.webm` бесполезна. Замерено отправкой одного ролика в четырёх видах на iPhone: VP9 и AV1 дают **чёрный экран** при играющем звуке, H.264 играет плавно и в 8, и в 10 битах. Любая команда libx264 несёт `-pix_fmt yuv420p` — это консервативная мера, а не починка наблюдаемого дефекта: 8 бит поддерживаются везде, поддержка 10 зависит от устройства. **Причина рывков не найдена**, версии про VP9 и про High 10 опровергнуты замером — см. ADR-002
- **match-case**: разбор callback-событий, выбор платформы/формата
- **Exception.add_note()**: обогащение ошибок контекстом
- **Коды ошибок**: `<PREFIX>-<CATEGORY>-<RANDOM6>` — префиксы YT/TT/IG/RU/VK/TG/FILE/BOT, категории ACCESS/NETWORK/TIMEOUT/MEDIA_FORBIDDEN/FORMAT_UNAVAILABLE/FFMPEG_MISSING/EXTRACTOR_RUNTIME/UNKNOWN. Пользователь видит только безопасный текст + код; cookies, пути и traceback уходят в админский лог
- **Порядок попыток у загрузчиков**: без cookies → с cookies → CLI `python -m yt_dlp`. Порядок критичен и для YouTube именно такой: авторизованная сессия переводит yt-dlp на клиентов `tv_downgraded`/`web_safari`, которым нужен PO-токен, а работающий без токена `android_vr` yt-dlp вычёркивает при наличии auth-cookies — замерено, что с cookie-файлом список форматов выходит пустым («Requested format is not available»), и весь список бот получает анонимной попыткой. Cookies остаются на возрастные ограничения и приватные видео. Instagram устроен так же — без cookies первым; у TikTok порядок обратный, там cookies идут первыми (`use_cookies_first` в `tiktok_instagram_utils.py`)
- **403 на медиафайле — не запрет доступа**: CDN отказывает по уже выданной ссылке, а не закрывает видео. Категория `MEDIA_FORBIDDEN` (`utils/public_errors.py`) отделяет этот случай от `ACCESS_RESTRICTED`: она повторяется с backoff (свежий разбор даёт свежую ссылку), открывает CLI-fallback и не будит админов. Строгий `ACCESS_RESTRICTED` остаётся за «private video», «login required» и голым 403 без упоминания медиа
- **Версия yt-dlp зажата на nightly**: 18 августа 2026 YouTube сломал скачивание по прямым ссылкам `videoplayback` для клиентов стабильной 2026.7.4 — запрос без `Range` и запрос на 10 МБ (штатный `http_chunk_size` бота) получали 403, а с куском 1 МБ скачивался ровно первый мегабайт. Замерено на двух разных исходящих адресах, так что дело не в IP. Nightly `2026.8.18.122307.dev0` перешла на клиент `visionos`, и те же 137+140 с теми же опциями качаются целиком — [yt-dlp#17456](https://github.com/yt-dlp/yt-dlp/issues/17456). Когда выйдет стабильная с этой правкой, пин надо вернуть на неё
- **SQLite WAL mode** во всех БД; все запросы параметризованы (`?`)
- **Вывод yt-dlp не глушится**: `apply_network_opts` подставляет логгер-адаптер, и предупреждения («cookies are no longer valid», «formats require a GVS PO Token», «forcing SABR streaming») уходят и в `bot.log`, и в хвост сессии. Раньше стоял `no_warnings: True`, и объяснение отказа приходилось добывать по SSH. Строки `[download]` в хвост не попадают: их десятки в секунду, они вытеснили бы полезное из шестидесяти строк. `no_warnings: False` ставится страховкой — в текущей yt-dlp ветка логгера в `report_warning` проверяется раньше флага, но порядок может измениться
- **Кэш пишется под фактический формат**: каскад фолбеков при 403 подменяет формат молча, а ключ кэша `combined:{format_id}` обещает конкретный, поэтому перед записью берётся `download_report.delivered_format(session_id)`. Ключи-корзины (`tg_video`, `direct_video`) конкретный формат не обещают и остаются как были
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
Опциональные: `WEB_*`, `FAIL2BAN_*`, `DATA_DIR`, `TEMP_DIR`, `YTDLP_*`, `CANARY_*`,
`TELEGRAM_LOCAL_MODE`, `TELEGRAM_BOT_API_*`. Шаблон — `.env.example`,
полная таблица с значениями по умолчанию — в `AGENTS.md`.

## Что изменять осторожно

- **`messages.py`** — лимит Telegram 4096 символов на сообщение
- **`config.py`** — новая env-переменная требует обновления `.env.example` (иначе падает тест) и `README.md`
- **`callback_fsm.py` + `telegram_utils.py`** — смена формата `callback_data` ломает уже отправленные пользователям кнопки; сопровождай тестами разбора событий
- **Вытеснение сессий** — `SessionStore` держит 5 записей на пользователя и вытесняет старые, но **никогда не удаляет временные файлы**: прежняя версия звала `cleanup_temp_files` для вытесненной сессии и на проде сносила каталог идущей загрузки (замер: 12 сессий у одного пользователя, файл исчезал между готовностью и отправкой). Занятые сессии не вытесняются вовсе — признак занятости `_session_is_disposable` смотрит, есть ли файлы в каталоге, потому что в записи лежит `session_id`, по которому владелец потом удалит файлы. `hard_limit` страхует от роста, если занято всё
- **`cancellation.py`** — отмена длительных задач по `session_id`. `CancelledByUser`
  наследуется от `BaseException` намеренно (как `asyncio.CancelledError`): она
  летит мимо широких `except Exception` в обработчиках платформ к единственному
  перехвату в `button_callback`. До yt-dlp отмена доходит через `progress_hooks`,
  которые ставит `apply_network_opts(..., session_id=...)` — проверено, что
  yt-dlp пропускает исключение из хука как есть
- **Меню форматов** — два уровня: разделы (`main|video_menu`, `main|audio_menu`, `main|subtitles`), затем выбор. Из `format`-действий живы только `combined` и `audio_only`; `best`, `audio_best`, `mp3_min` и `video_only` удалены вместе с кнопками и закреплены в `test_dead_code_contract.py`
- **Субтитры** — каскад: `main|subtitles` → язык (`format|subs_lang|ru`) → формат
  (`format|subs|ru:srt`). Предлагаются только русский и английский, TXT
  собирается из SRT в `utils/subtitles.py`
- **Отмена** — кнопка есть на каждом экране: на ожидании разбора ссылки, в
  главном меню и на всех статусах скачивания. Сессия заводится **до** разбора
  ссылки именно ради этого, иначе кнопке не за что зацепиться
- **`youtube_utils.py`** — цепочка fallback чувствительна к порядку операций
- **`tests/conftest.py`** — заглушка `yt_dlp` используется всем набором
- **Схемы SQLite** — учитывай WAL и необходимость миграции существующих установок (образец — миграция `last_csi_sent` в `analytics_db.py`)
