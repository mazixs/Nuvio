# Архитектурный разбор FSM-логики Nuvio

> Дата: 2026-05-29
> Область: Telegram-бот, конечные автоматы, pipeline скачивания
> Цель: выявить неявные состояния, узкие места и предложить оптимизации

---

## 1. Общая концепция: неявная FSM

В Nuvio **нет явного enum состояний** и центрального диспетчера переходов. Вместо этого FSM реализована через:

- **Callback-data** (`s|{token}|{scope}|{action}`) — несут в себе "адрес" перехода
- **Inline-keyboard** — текущее сообщение с кнопками = текущее состояние
- `context.user_data["sessions"]` — хранилище активных сессий (макс. 5 на пользователя)
- **LRU-эвикция** — при превышении 5 сессий самая старая уничтожается

Это не классическая FSM, а **сессионная машина состояний**, где состояние определяется наличием inline-клавиатуры в конкретном сообщении чата.

---

## 2. Диаграмма состояний

```text
┌─────────────────────────────────────────────────────────────────────────────┐
│                              ПОЛЬЗОВАТЕЛЬ                                   │
└─────────────────────────────────────────────────────────────────────────────┘
       │
       ▼ отправляет URL
┌──────────────┐
│   [IDLE]     │  ← нет активного меню
└──────────────┘
       │
       ▼ _check_spam()
   ┌───────┐
   │SPAM?  │  ──YES──►  SPAM_WARNING, остаёмся в IDLE
   └───────┘
       │ NO
       ▼
┌──────────────┐  "⏳ Обрабатываю ссылку..."
│ [PROCESSING] │  process_url() → get_video_info() через ThreadPool
└──────────────┘
       │
  ┌────┴────┐
  ▼         ▼
[Ошибка]  [Успех]
  │         │
  ▼         ▼
  cleanup   ┌──────────────┐
  IDLE      │  [MAIN_MENU] │  ← карточка видео + inline-кнопки
            │(_build_main_ │     (TG-видео / Аудио / Ещё / Назад)
            │    menu)     │
            └──────────────┘
                  │
     ┌────────────┼────────────┐
     ▼            ▼            ▼
┌────────┐  ┌──────────┐  ┌──────────┐
│[action= │  │[action=  │  │[action=  │
│ back]   │  │ more]    │  │ download]│
└────┬───┘  └────┬─────┘  └────┬─────┘
     │           │             │
     ▼           ▼             ▼
┌────────┐  ┌──────────┐  ┌──────────┐
│[MAIN_  │  │[FORMAT_  │  │[DOWNLOAD-│
│ MENU]   │  │  MENU]   │  │  ING]    │
└────────┘  └────┬─────┘  │"⏳ Скачи- │
                 │        │ ваю..."   │
        ┌────────┴────────┐└────┬─────┘
        ▼                 ▼      │
  [format|combined]  [format|    │
                      audio_only]│
        │                 │      │
        └────────┬────────┘      │
                 ▼                │
        ┌────────────────┐        │
        │   [PREPARING]  │◄───────┘  "⏳ Подготавливаю файл..."
        │  (send_file)   │           (после скачивания)
        └───────┬────────┘
                │
           ┌────┴────┐
           ▼         ▼
       [Cache      [SENT]
        HIT]       "✅ Файл
       мгновенно    отправлен!"
           │         │
           └───┬─────┘
               ▼
      [_cleanup_user_session]
               │
               ▼
         ┌──────────┐
         │  [IDLE]  │  ← сессия уничтожена, можно новый URL
         └──────────┘
```

---

## 3. Механизм сессий

### Хранилище

```python
context.user_data["sessions"] = {
    "a1b2c3d4": {
        "url": "...",
        "video_info": {...},
        "session_id": "{user_id}_{uuid}",
        "platform": "youtube",
        "formats": [...],
        "created_at": 1716921600.0,
    },
    # ... максимум 5 записей
}
```

### Жизненный цикл токена

| Этап | Действие | Кто вызывает |
|------|----------|--------------|
| Создание | `uuid.uuid4().hex[:8]` | `_store_session` в `process_url` |
| Использование | `query.data.split('\|')` | `button_callback` |
| Валидация | `session_data = _get_session(...)` | `_handle_main_callback` / `_handle_format_callback` |
| Уничтожение | `_cleanup_user_session(...)` | После успеха, ошибки или LRU-эвикции |

### Проблема: коллизии и предсказуемость

- Токен 8 hex-символов = 2³² комбинаций. Для одного пользователя это достаточно, но:
- **Нет проверки подписи** — любой, кто знает `s|a1b2c3d4|main|tg_video`, может инициировать скачивание
- Теоретически возможен **brute-force callback** (хотя Telegram защищает от этого через chat_id)
- Токен предсказуем, если seed UUID предсказуем (Python `uuid4()` использует `/dev/urandom` — безопасно)

---

## 4. Pipeline скачивания: полный путь

```text
[URL от пользователя]
       │
       ▼
┌─────────────────────────────┐
│ process_url()               │  ← async, event loop
│   ├── _check_spam()         │
│   ├── track_event()         │  ← SQLite sync (!)
│   └── run_blocking()        │
└─────────────┬───────────────┘
              ▼
       ThreadPoolExecutor
              │
              ▼
┌─────────────────────────────┐
│ get_video_info()            │  ← sync, HTTP-запросы к YouTube
│   ├── yt_dlp.YoutubeDL      │     extract_info(url, download=False)
│   ├── apply_network_opts    │     retries=5, timeout=40s
│   └── Cookie fallback       │
└─────────────┬───────────────┘
              │
              ▼
┌─────────────────────────────┐
│ _build_main_menu()          │  ← inline keyboard
│   └── callback_data =       │     "s|token|main|{action}"
└─────────────┬───────────────┘
              │
              ▼  пользователь нажимает кнопку
┌─────────────────────────────┐
│ button_callback()           │  ← async, event loop
│   ├── _should_rate_limit_   │
│   │   callback()            │
│   ├── _check_spam()         │
│   └── match data:           │
│       case ["s", t, "main"] │
│           → _handle_main_   │
│             callback()      │
│       case ["s", t, "fmt"]  │
│           → _handle_format_ │
│             callback()      │
└─────────────┬───────────────┘
              │
              ▼
┌─────────────────────────────┐
│ telegram_cache.get()        │  ← SQLite sync, event loop (!)
└─────────────┬───────────────┘
              │
      ┌───────┴───────┐
      ▼               ▼
  [Cache HIT]     [Cache MISS]
      │               │
      ▼               ▼
  reply_video()   run_blocking()
  (file_id)            │
  мгновенно            ▼
                ThreadPoolExecutor
                       │
                       ▼
              ┌─────────────────┐
              │ download_video()│  ← sync, yt-dlp
              │   ├── HTTP GET  │     fragment downloads
              │   ├── write disk│
              │   └── FFmpeg    │     webm→mp4
              │       convert   │
              └────────┬────────┘
                       │
                       ▼
              ┌─────────────────┐
              │finalize_download│  ← sync
              │_file()          │
              │   > лимита?     │
              │   → ошибка      │
              └────────┬────────┘
                       │
                       ▼
              ┌─────────────────┐
              │ send_file()     │  ← async, event loop
              │   ├── open()    │     sync (!)
              │   ├── reply_    │     video/audio
              │   │   video()   │     HTTP upload TG
              │   └── telegram_ │     SQLite sync (!)
              │       cache.set │
              └─────────────────┘
```

---

## 5. Узкие места FSM

### 5.1. Нет явного state machine

**Проблема:** состояния неявные, разбросаны по `if/else` и `match/case`. Добавление нового состояния (например, "ожидание выбора качества") требует модификации `button_callback`, `_handle_main_callback`, `_build_main_menu` и т.д.

**Риск:** при масштабировании логики (новые платформы, новые типы контента) код `telegram_utils.py` (~120KB) станет необслуживаемым.

**Признак:** `_handle_main_callback` содержит ~600 строк `match action` без вынесения обработчиков в отдельные функции-стратегии.

### 5.2. Синхронный SQLite в event loop

```python
# В _handle_main_callback (async!)
cached = telegram_cache.get(url, format_id=cache_key)  # sync SQLite

# В send_single_file (async!)
telegram_cache.set(CachedVideo(...))  # sync SQLite
```

- SQLite WAL mode быстрый, но при конкурентной записи `BEGIN IMMEDIATE` может блокировать до `timeout=30s`
- Все callback'и пользователей ждут в event loop, пока один поток пишет в БД

**Рекомендация:** обернуть в `asyncio.to_thread()` или использовать очередь записи.

### 5.3. ThreadPool bottleneck

```python
executor = ThreadPoolExecutor(max_workers=DOWNLOAD_WORKERS)  # default=8
```

| Сценарий | Занятые воркеры | Результат |
|----------|-----------------|-----------|
| 8 пользователей скачивают видео по 5 минут | 8/8 | 9-й пользователь ждёт в очереди |
| 1 пользователь, MP3 min | 1 (скачивание) + 1 (FFmpeg) | последовательно, не параллельно |
| Отправка файла 200MB | event loop + локальный API | воркер скачивания свободен |

**Признак:** `run_blocking` использует **один** executor для info extraction,
download и FFmpeg; передача локального пути выполняется асинхронно.

### 5.4. Нет кэш-проверки на этапе `process_url`

```python
# process_url:
video_info = await run_blocking(get_video_info, ...)  # всегда выполняется
# → только потом в callback проверяется кэш
```

Если пользователь повторно отправляет URL, бот всегда делает HTTP-запрос к YouTube, даже если файл уже в кэше.

**Рекомендация:** проверять `telegram_cache.get()` в `process_url` **до** `get_video_info`.

### 5.5. Отсутствие предпроцессинга

**Что есть сейчас:**
- Пользователь отправляет URL
- Бот ждёт полного `get_video_info` (1-15 сек)
- Потом показывает меню

**Чего нет:**
- Предварительного thumbnail/preview
- Прогресс-бара скачивания
- Параллельного fetch info для нескольких URL
- Streaming-режима (начать скачивание до выбора формата)

### 5.6. Отсутствие постпроцессинга

**Что есть сейчас:**
- FFmpeg конвертация webm→mp4 внутри `download_video`
- Сжатие файла >50MB тоже внутри скачивания

**Чего нет:**
- Отложенной конвертации (скачать сырые фрагменты, конвертировать позже)
- Background cleanup (temp-файлы чистятся только при сессии)
- Фонового удаления осиротевших файлов во время долгой работы

### 5.7. FSM state не сохраняется при перезапуске бота

```python
context.user_data  # хранится в памяти процесса
```

При перезапуске бота:
- Все активные сессии теряются
- Пользователи видят `SESSION_EXPIRED` при нажатии кнопок
- Антиспам-состояние сбрасывается

**Рекомендация:** сохранять `user_data` в Redis или SQLite при graceful shutdown.

---

## 6. Чанкинг и yt-dlp: работаем ли мы с фрагментами?

### Как yt-dlp загружает видео

yt-dlp поддерживает **fragment-based downloading** для:
- **HLS** (HTTP Live Streaming) — `.m3u8` плейлисты
- **DASH** (Dynamic Adaptive Streaming) — `.mpd` манифесты
- **YouTube** — использует `Range`-запросы для сегментов

Конфигурация в Nuvio (`utils/youtube_utils.py`):

```python
"concurrent_fragment_downloads": 4,
"fragment_retries": 5,
```

Это означает: yt-dlp **под капотом** загружает видео фрагментами параллельно (4 потока), но Nuvio **не управляет этим процессом**.

### Уровни чанкинга

| Уровень | Кто управляет | Доступно в Nuvio? |
|---------|---------------|-------------------|
| HTTP-range chunks | yt-dlp (внутри) | ✅ неявно |
| Fragment parallel (HLS/DASH) | yt-dlp (`concurrent_fragment_downloads=4`) | ✅ конфигурируется |
| Chunked upload to Telegram | python-telegram-bot (внутри) | ✅ неявно |
| Resume download | yt-dlp (`--continue`) | ❌ не включено |
| Partial download (обрезка) | yt-dlp (`--download-sections`) | ❌ не используется |

### Является ли чанкинг узким местом?

**Нет.** yt-dlp эффективно использует фрагментную загрузку. Проблема не в чанкинге, а в:

1. **Однопоточности pipeline** — весь процесс (info → download → convert → upload) выполняется последовательно в одном воркере ThreadPool
2. **Отсутствии приоритизации** — короткие аудио и длинные видео ждут в одной очереди
3. **Блокировании event loop** — `telegram_cache.get/set`, `open(file)`, `track_event` выполняются синхронно в async-контексте

### Может ли чанкинг ускорить доставку?

**Да, если реализовать на уровне бота:**

```text
Текущий подход:
[Скачать полное видео] → [Конвертировать] → [Отправить]
     5 минут          →    2 минуты      →   1 минута

Chunked-подход (потенциальный):
[Скачать фрагмент 1] ─┐
[Скачать фрагмент 2] ─┼→ [Собрать MP4] → [Отправить]
[Скачать фрагмент 3] ─┘
[Скачать фрагмент 4] ─┘
     1.5 минуты      →    1 минута     →   1 минута
```

Но для Telegram это **избыточно** — файл должен быть целым перед отправкой.

**Где чанкинг реально помогает:**
1. **Resume** — при обрыве соединения не перекачивать заново
2. **Parallel segments** — уже работает через yt-dlp
3. **Partial content** — скачать только нужный диапазон (для preview)

---

## 7. Предложения по оптимизации

### 7.1. Предпроцессы (до скачивания)

#### A. Кэш-first при `process_url`

```python
async def process_url(update, context, url=None):
    if not url:
        url = await _get_url_from_context(update, context)
    
    # Проверить кэш ДО get_video_info
    for cache_key in ["direct_video", "tg_video", "best"]:
        cached = telegram_cache.get(url, format_id=cache_key)
        if cached:
            await update.message.reply_video(video=cached.file_id)
            track_event(user_id, "cache_hit")
            return  # Пропускаем info extraction целиком
    
    # Иначе — стандартный flow
    ...
```

**Выигрыш:** для повторных URL экономим 1-15 секунд на HTTP-запросе.

#### B. Async info extraction с таймаутом

```python
# Вместо run_blocking(get_video_info) с таймаутом 600s
# Использовать asyncio.wait_for с разумным лимитом:
video_info = await asyncio.wait_for(
    run_blocking(get_video_info, url),
    timeout=30.0,  # info extraction не должен занимать минуты
)
```

**Выигрыш:** пользователь быстрее получает ошибку, если видео недоступно.

#### C. Предварительный thumbnail

```python
# В _build_main_menu добавить:
thumb_url = video_info.get("thumbnail")
if thumb_url:
    await query.message.reply_photo(thumb_url, caption=title_text)
```

**Выигрыш:** пользователь видит превью мгновенно, пока готовится меню.

### 7.2. Постпроцессы (после скачивания)

#### A. Отложенная конвертация (для Rutube/VK)

```python
# Вместо:
download_video(url, format_id)  # внутри: yt-dlp + FFmpeg

# Сделать:
raw_path = download_raw(url, format_id)  # только yt-dlp
# Показать меню "Файл скачан, идёт конвертация..."
converted_path = await run_blocking(convert_with_ffmpeg, raw_path)
```

**Выигрыш:** пользователь не ждёт в "тёмном" состоянии DOWNLOADING.

#### B. Background cleanup

```python
# Вместо cleanup при сессии:
async def _background_cleanup(session_id: str):
    await asyncio.sleep(300)  # через 5 минут
    cleanup_temp_files(session_id)

# После отправки:
asyncio.create_task(_background_cleanup(session_id))
```

**Выигрыш:** пользователь видит результат мгновенно, файлы чистятся в фоне.

#### C. Локальная передача файлов

Bot API получает абсолютный путь в общем томе, поэтому отдельный исполнитель
для внешней загрузки больше не требуется.

### 7.3. FSM-рефакторинг

#### A. Явные состояния

```python
from enum import StrEnum

class BotState(StrEnum):
    IDLE = "idle"
    PROCESSING = "processing"
    MAIN_MENU = "main_menu"
    FORMAT_MENU = "format_menu"
    DOWNLOADING = "downloading"
    PREPARING = "preparing"
    SENT = "sent"
    ERROR = "error"
    AWAITING_CSI_FEEDBACK = "awaiting_csi_feedback"
```

#### B. Центральный диспетчер

```python
# Вместо match/case в button_callback:
_state_handlers = {
    BotState.MAIN_MENU: _handle_main_menu_action,
    BotState.FORMAT_MENU: _handle_format_menu_action,
}

async def button_callback(update, context):
    state = _get_user_state(user_id)
    handler = _state_handlers.get(state, _handle_unknown_state)
    await handler(update, context, session_data)
```

#### C. Хранение FSM в SQLite

```python
# Таблица user_states
CREATE TABLE user_states (
    user_id INTEGER PRIMARY KEY,
    state TEXT NOT NULL,
    session_token TEXT,
    session_data TEXT,  -- JSON
    updated_at TEXT
);
```

**Выигрыш:** состояния сохраняются при перезапуске бота, возможна горизонтальная масштабируемость.

### 7.4. Потоковая обработка (streaming)

```python
# Потенциальный подход для YouTube:
# 1. Получить info
# 2. Если формат известен (best) — начать скачивание фоновой задачей
# 3. Показать меню с прогрессом
# 4. При выборе формата — проверить, не скачался ли уже

class BackgroundDownload:
    def __init__(self):
        self._tasks: dict[str, asyncio.Task] = {}
    
    def start(self, url, format_id):
        task = asyncio.create_task(self._download(url, format_id))
        self._tasks[f"{url}:{format_id}"] = task
    
    async def get(self, url, format_id, timeout=300):
        task = self._tasks.get(f"{url}:{format_id}")
        if task:
            return await asyncio.wait_for(task, timeout=timeout)
        return None
```

**Выигрыш:** если пользователь часто выбирает "best" — файл может быть уже скачан к моменту нажатия кнопки.

---

## 8. Сравнительная таблица: текущее vs оптимальное

| Аспект | Текущее | Оптимальное | Сложность |
|--------|---------|-------------|-----------|
| FSM state | Неявный (inline keyboard) | Явный enum + SQLite | Средняя |
| Cache check | Только в callback | В `process_url` + в callback | Низкая |
| Info extraction | Блокирует ThreadPool | Async с таймаутом 30s | Низкая |
| SQLite операции | Sync в event loop | `asyncio.to_thread` | Низкая |
| FFmpeg | Sync в download pipeline | Отложенный postprocess | Средняя |
| Отправка файла | Локальный путь | Локальный Bot API | Низкая |
| Cleanup | При сессии | Background через 5 мин | Низкая |
| State persistence | В памяти | SQLite/Redis | Средняя |
| Chunking | yt-dlp под капотом | Добавить resume/partial | Низкая |
| Progress | Нет | Периодические edit_message | Средняя |
| Thumbnail | Нет | Предварительный preview | Низкая |

---

## 9. ICE-приоритизация оптимизаций

> **ICE Score = (Impact + Confidence + Ease) / 3**
>
> - **Impact (1–10):** влияние на UX, метрики, нагрузку. Критичность P0 → Impact 8–10, P1 → 6–8, P2 → 4–6, P3 → 2–4.
> - **Confidence (1–10):** уверенность в реализации. 10 = код почти готов, 1 = исследовательская задача.
> - **Ease (1–10):** лёгкость внедрения. 10 = одна строка / параметр, 1 = архитектурный рефакторинг.
>
> **Критичность** напрямую влияет на Impact: чем выше критичность проблемы, тем выше потенциальное улучшение от её устранения.

### Матрица

| # | Оптимизация | Критичность | Impact (I) | Confidence (C) | Ease (E) | **ICE Score** | Обоснование |
|---|-------------|-------------|------------|----------------|----------|---------------|-------------|
| 1 | **Кэш-first в `process_url`** | P0 | 9 | 10 | 10 | **9.67** | Проверка `telegram_cache.get()` уже реализована в callback — достаточно перенести вызов в начало `process_url`. Мгновенный выигрыш для всех повторных URL. |
| 2 | **Таймаут на info extraction 30с** | P0 | 8 | 9 | 10 | **9.00** | Один параметр `timeout` в `asyncio.wait_for`. Пользователь получает ошибку за секунды вместо минут при недоступном видео. |
| 3 | **Async SQLite (to_thread)** | P0 | 8 | 8 | 8 | **8.00** | Стандартный паттерн Python 3.9+. Обернуть `telegram_cache.get/set` и `track_event` в `asyncio.to_thread`. Снимает блокировку event loop для всех пользователей. |
| 4 | **Background cleanup** | P1 | 6 | 9 | 9 | **8.00** | `asyncio.create_task(asyncio.sleep(300)); cleanup_temp_files()`. Не требует новых зависимостей. UI освобождается мгновенно. |
| 5 | **Предварительный thumbnail** | P2 | 5 | 8 | 9 | **7.33** | `reply_photo(thumb_url)` до построения меню. Мгновенный UX-выигрыш, почти нулевая сложность. |
| 6 | **Периодическая очистка сирот** | P1 | 6 | 8 | 7 | **7.00** | Удалять забытые временные файлы старше заданного TTL без остановки бота. |
| 7 | **Хранение FSM в SQLite** | P1 | 7 | 7 | 5 | **6.33** | Таблица `user_states` + JSON-сериализация `session_data`. Переживает перезапуск, но требует миграции session store и graceful shutdown hook. |
| 8 | **Явные состояния (enum)** | P2 | 5 | 7 | 6 | **6.00** | `enum.StrEnum` + `_state_handlers` dict. Упрощает поддержку, но требует рефакторинг ~120KB `telegram_utils.py`. |
| 9 | **Resume download** | P2 | 5 | 6 | 5 | **5.33** | yt-dlp поддерживает `--continue`. Достаточно передать параметр в `ydl_opts`. Низкий выигрыш — обрывы редки. |
| 10 | **Progress bar** | P2 | 6 | 5 | 4 | **5.00** | Требует фоновый task + периодические `edit_message_text`. Сложно интегрировать с yt-dlp callback прогресса. |
| 11 | **Отложенная конвертация** | P1 | 5 | 6 | 4 | **5.00** | Меняет UX (два этапа: «скачано» → «конвертируется» → «готово»). Требует новых FSM-состояний и UI. |
| 12 | **Background pre-download** | P3 | 4 | 3 | 3 | **3.33** | Начинать скачивание до выбора формата. Риск зря тратить трафик и CPU. Сложная логика предсказания. |
| 13 | **Chunked streaming upload** | P3 | 3 | 2 | 2 | **2.33** | Telegram Bot API не поддерживает partial upload. Невозможно без изменений на стороне Telegram. |

### Ранжирование по ICE

| Ранг | ICE Score | Оптимизации |
|------|-----------|-------------|
| 🥇 9.0–10.0 | **Must have** | Кэш-first в `process_url`, Таймаут 30с |
| 🥈 8.0–8.9 | **High value** | Async SQLite, Background cleanup |
| 🥉 7.0–7.9 | **Quick wins** | Thumbnail, Отдельный executor для upload |
| ⚡ 6.0–6.9 | **Medium term** | Хранение FSM в SQLite, Явные состояния |
| 📌 5.0–5.9 | **Low hanging / Complex** | Resume, Progress bar, Отложенная конвертация |
| 🔬 < 5.0 | **Research** | Background pre-download, Chunked streaming upload |

### Как критичность влияет на ICE

- **P0 (Cache-first, Таймаут, Async SQLite)** → высокий **Impact (8–9)**, потому что устраняют немедленную боль пользователя (ожидание, блокировка UI).
- **P1 (Background cleanup, Executor, FSM persistence)** → средний **Impact (6–7)**, улучшают стабильность и масштабируемость, но эффект не мгновенный.
- **P2 (Thumbnail, Enum, Resume, Progress)** → низкий **Impact (4–6)**, UX-улучшения и технический долг.
- **P3 (Pre-download, Streaming)** → минимальный **Impact (2–4)**, исследовательские гипотезы с высоким риском.

### Вывод

**Кэш-first в `process_url`** — единственная оптимизация с ICE > 9. Она:
- Устраняет критичный дефект (P0)
- Требует одного условия и переноса существующего кода (Ease = 10)
- Даёт мгновенный выигрыш для ~30% повторных запросов (Impact = 9)

**Async SQLite** и **Background cleanup** — вторые по приоритету (ICE = 8.0). Они снимают системные блокировки и не требуют изменения логики FSM.

**Thumbnail** и **Отдельный executor** — «quick wins» (ICE = 7.0+), которые можно реализовать за один коммит.

**FSM persistence** и **Явные состояния** — среднесрочные задачи (ICE = 6.0+), требующие рефакторинга, но дающие фундамент для масштабирования.

**P3-задачи** (ICE < 5) — не стоит брать в ближайший квартал: либо невозможны (streaming upload), либо рискованны (pre-download).

---

## 10. Заключение

Архитектура Nuvio использует **неявную сессионную FSM**, которая хорошо работает для текущего масштаба, но имеет архитектурные ограничения:

1. **Отсутствие явных состояний** затрудняет масштабирование
2. **ThreadPool bottleneck** — 8 воркеров на всё (info, download, convert, upload)
3. **Синхронные операции в event loop** — SQLite, `open()`, `track_event()`
4. **Нет пред/постпроцессинга** — всё линейно, нет параллелизма на уровне бота
5. **State не переживает перезапуск**

Чанкинг на уровне yt-dlp **уже работает** (`concurrent_fragment_downloads=4`), но Nuvio не управляет им явно и не использует преимущества fragment-based загрузки для resume/partial content.

**Краткосрочные победы (ICE ≥ 8):** кэш-first в `process_url`, таймаут 30с, async SQLite, background cleanup.
**Среднесрочные (ICE 6–8):** thumbnail, отдельный executor, хранение FSM в SQLite, явные состояния.
**Долгосрочное / исследования (ICE < 6):** progress tracking, resume download, background pre-download.
