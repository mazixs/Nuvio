# Выпуск 1: параллельная обработка апдейтов и живой статус

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Кнопка «Отменить» начинает работать во время скачивания, вторая ссылка принимается не дожидаясь первой, и отметка активности не исчезает после одной сетевой ошибки.

**Architecture:** У PTB по умолчанию `max_concurrent_updates = 1`, и фетчер апдейтов ждёт завершения текущего обработчика. Включаем параллельную обработку явным числом, закрепляем тестом саму доставку нажатия во время долгой задачи, делаем пульс отметки активности живучим и закрепляем тестами свойства общего состояния, на которые параллельность теперь опирается.

**Tech Stack:** Python 3.14, python-telegram-bot 22.8, pytest 9 (маркеры `unit`/`integration`, `--strict-markers`), ruff.

## Global Constraints

- Проект русскоязычный: комментарии, docstrings и user-facing тексты — на русском; идентификаторы — на английском.
- Никакой новой функциональности в этом выпуске. Очередь задач, тасклист и прогресс — выпуск 2, отдельный план.
- `ruff check --output-format=github .` должен быть чист; политика правил в `pyproject.toml` (`select = ["E4", "E7", "E9", "F"]`) не меняется.
- Весь набор тестов проходит целиком (`pytest`), без сети.
- Импорты в `main.py` намеренно стоят ниже `load_dotenv()`; per-file-ignore `E402` включён. Не переставлять их наверх.
- `print()` в production-коде запрещён (`tests/test_syntax.py`), логирование — через `utils/logger.py`.
- Conventional commits: `feat:`, `fix:`, `docs:`.

## Уже проверено — не выяснять заново

- `connection_pool_size` у PTB по умолчанию **256**, исчерпание пула к потере отметки активности не приводит.
- `_check_spam` (`utils/telegram_utils.py:304`) полностью синхронный, без `await` внутри, — в одном event loop он атомарен.
- `SessionStore.create` и остальные его методы синхронные — переплетения через `await` внутри нет.
- `utils/temp_file_manager.py` адресует файлы по `session_id` и общего изменяемого состояния не держит.
- Методы `SessionStore`, меняющие хранилище, называются `create` и `remove` (`utils/callback_fsm.py:68` и `:105`), оба синхронные.
- `cleanup_temp_files()` **без аргумента** стирает все временные папки, но все три таких вызова (`main.py:285`, `main.py:341`, `main.py:359`) выполняются только при старте и остановке процесса. Во время обработки апдейтов вызывается только `cleanup_temp_files(session_id)`. Правка не нужна.

---

## File Structure

| Файл | Ответственность | Действие |
|---|---|---|
| `main.py` | Константа `UPDATE_CONCURRENCY` и её передача билдеру | Modify |
| `tests/test_concurrent_update_delivery.py` | Доставка апдейта во время долгой задачи | Create |
| `utils/telegram_utils.py` | `_pulsing_chat_action` переживает сбой отправки | Modify |
| `tests/test_chat_action_resilience.py` | Живучесть пульса | Create |
| `tests/test_shared_state_under_concurrency.py` | Свойства общего состояния, на которые опирается параллельность | Create |
| `CLAUDE.md`, `AGENTS.md` | Правило «хэндлер не держит фетчер» | Modify |

---

### Task 1: Доставка нажатия во время долгой задачи

Сердце выпуска. Тест сначала падает на текущей конфигурации — это и есть доказательство поломки, а не рассуждение о ней.

**Files:**
- Create: `tests/test_concurrent_update_delivery.py`
- Modify: `main.py:172-196` (функция `_configure_application_builder`)
- Modify: `main.py` — новая константа рядом с остальными настройками

**Interfaces:**
- Produces: `main.UPDATE_CONCURRENCY: int` — предел одновременно обрабатываемых апдейтов. Значение читает тест, поэтому оно обязано быть модульной константой, а не литералом внутри вызова.

- [ ] **Step 1: Написать падающий тест**

Создать `tests/test_concurrent_update_delivery.py`:

```python
"""Нажатие кнопки обязано доходить до хэндлера, пока идёт долгая задача.

Замерено на PTB 22.8: у `Application` по умолчанию
`max_concurrent_updates = 1`, и `__update_fetcher` ждёт завершения текущего
апдейта, прежде чем взять следующий. Пока `download_content` висит на
`run_blocking`, нажатие «Отменить» лежит в очереди:

    0.0с  обработчик длинной задачи начал
    0.3с  нажата кнопка «Отменить»
    1.5с  обработчик длинной задачи кончил
    1.5с  ОТМЕНА обработана        ← уже впустую

Механизм отмены при этом исправен — до него не доходит сигнал. Остальные
тесты отмены вызывают `button_callback` напрямую и поэтому проверяют
механизм, но не доставку.
"""

import asyncio
import contextlib

import pytest
from telegram.ext import ApplicationBuilder, TypeHandler

import main


pytestmark = pytest.mark.integration

LONG_TASK_SECONDS = 0.6
PRESS_AFTER_SECONDS = 0.1


class _LongTask:
    """Апдейт, который обрабатывается долго, как скачивание."""


class _Press:
    """Апдейт от кнопки, который должен обработаться не дожидаясь первого."""


def _run_two_updates(concurrency) -> dict[str, float]:
    """Возвращает моменты событий относительно старта долгой задачи."""
    marks: dict[str, float] = {}

    async def scenario() -> None:
        builder = ApplicationBuilder().token("123:ABC")
        if concurrency is not None:
            builder = builder.concurrent_updates(concurrency)
        app = builder.build()
        # `initialize()` ходит в сеть за `get_me`, а нам нужен только фетчер.
        app._initialized = True

        started = asyncio.get_running_loop().time()

        async def long_task(_update, _context) -> None:
            marks["задача началась"] = asyncio.get_running_loop().time() - started
            await asyncio.sleep(LONG_TASK_SECONDS)
            marks["задача кончилась"] = asyncio.get_running_loop().time() - started

        async def press(_update, _context) -> None:
            marks["нажатие обработано"] = (
                asyncio.get_running_loop().time() - started
            )

        app.add_handler(TypeHandler(_LongTask, long_task))
        app.add_handler(TypeHandler(_Press, press))

        fetcher = asyncio.create_task(app._update_fetcher())
        await app.update_queue.put(_LongTask())
        await asyncio.sleep(PRESS_AFTER_SECONDS)
        await app.update_queue.put(_Press())
        await asyncio.sleep(LONG_TASK_SECONDS + 0.4)
        fetcher.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await fetcher

    asyncio.run(scenario())
    return marks


def test_press_is_handled_before_the_long_task_finishes():
    """Настроенная параллельность обязана доставлять нажатие сразу."""
    marks = _run_two_updates(main.UPDATE_CONCURRENCY)

    assert "нажатие обработано" in marks, "нажатие не дошло до хэндлера"
    assert marks["нажатие обработано"] < marks["задача кончилась"], (
        f"нажатие обработано на {marks['нажатие обработано']:.2f}с, "
        f"а задача кончилась на {marks['задача кончилась']:.2f}с — "
        "значит апдейт ждал в очереди"
    )


def test_default_configuration_is_the_one_that_was_broken():
    """Фиксирует саму причину: без настройки нажатие ждёт конца задачи.

    Тест страхует от «починки», которая на самом деле ничего не меняет:
    если однажды PTB сменит поведение по умолчанию, он упадёт и заставит
    перечитать этот план.
    """
    marks = _run_two_updates(None)

    assert marks["нажатие обработано"] >= marks["задача кончилась"]


def test_concurrency_limit_leaves_room_for_navigation():
    """Предел обязан быть выше числа воркеров скачивания.

    Иначе восемь занятых загрузок съедают все слоты, и девятый апдейт —
    нажатие «Отменить» — снова ждёт в очереди.
    """
    from config import DOWNLOAD_WORKERS

    assert main.UPDATE_CONCURRENCY > DOWNLOAD_WORKERS
```

- [ ] **Step 2: Запустить тест и убедиться, что он падает**

Run: `pytest tests/test_concurrent_update_delivery.py -v`

Expected: FAIL. `test_press_is_handled_before_the_long_task_finishes` и
`test_concurrency_limit_leaves_room_for_navigation` падают с
`AttributeError: module 'main' has no attribute 'UPDATE_CONCURRENCY'`.
`test_default_configuration_is_the_one_that_was_broken` проходит — он описывает
текущее поведение.

- [ ] **Step 3: Добавить константу в `main.py`**

Вставить после блока импортов и настройки логгера (после строки
`logger = setup_logger(__name__, level=LOG_LEVEL)`):

```python
# Сколько апдейтов бот обрабатывает одновременно.
#
# По умолчанию PTB обрабатывает апдейты строго по одному: фетчер ждёт
# завершения текущего обработчика. Из-за этого нажатие «Отменить» лежало в
# очереди всё скачивание и обрабатывалось, когда файл уже отправлен, а вторая
# ссылка не читалась вовсе.
#
# Значение заведомо выше DOWNLOAD_WORKERS: скачивания занимают слоты надолго, и
# на навигацию по меню с отменой должно оставаться место, иначе ограничение
# вернёт ту же поломку под нагрузкой.
UPDATE_CONCURRENCY = 32
```

- [ ] **Step 4: Передать значение билдеру**

В `_configure_application_builder` (`main.py:177`) добавить вызов в цепочку —
первым, чтобы он не терялся среди таймаутов:

```python
    builder = (
        builder.token(TELEGRAM_TOKEN)
        .concurrent_updates(UPDATE_CONCURRENCY)
        .connect_timeout(10.0)
        .read_timeout(120.0)
```

- [ ] **Step 5: Запустить тесты и убедиться, что они проходят**

Run: `pytest tests/test_concurrent_update_delivery.py -v`
Expected: PASS, все три теста.

- [ ] **Step 6: Проверить, что контрактный тест билдера не сломался**

Run: `pytest tests/test_local_bot_api_application.py -v`
Expected: PASS. `_BuilderRecorder` принимает любой метод, поэтому новый вызов
в цепочке его не ломает. Если упало — значит тест перечисляет вызовы точным
списком; в этом случае добавить `concurrent_updates` в ожидания, не убирая
остальных.

- [ ] **Step 7: Запустить весь набор**

Run: `pytest -q`
Expected: PASS целиком. Особое внимание на `tests/test_main_polling.py` — он
собирает приложение и мог опираться на прежнюю конфигурацию.

- [ ] **Step 8: Commit**

```bash
git add main.py tests/test_concurrent_update_delivery.py
git commit -m "fix: нажатие кнопки доходит до бота во время скачивания"
```

---

### Task 2: Пульс отметки активности переживает сбой

**Files:**
- Modify: `utils/telegram_utils.py:747-778` (`_pulsing_chat_action`)
- Create: `tests/test_chat_action_resilience.py`

**Interfaces:**
- Consumes: ничего из Task 1.
- Produces: поведение `_pulsing_chat_action` — при ошибке отправки цикл продолжается; выход только по отмене задачи.

- [ ] **Step 1: Написать падающий тест**

Создать `tests/test_chat_action_resilience.py`:

```python
"""Отметка «отправляет видео…» не должна пропадать на длинной отправке.

Прежний цикл выходил навсегда при первой же ошибке отправки:

    except telegram.error.TelegramError as error:
        logger.debug(...)
        return

Один сетевой сбой за десять минут выгрузки — и в шапке чата больше ничего нет,
хотя работа идёт. Именно из этого выросла жалоба «не понимаешь, сломалось или
видео всё-таки придёт». Исчерпание пула соединений тут не при чём:
`connection_pool_size` по умолчанию 256.
"""

import asyncio

import pytest
import telegram

from utils import telegram_utils


pytestmark = pytest.mark.unit


class _Chat:
    """Чат, у которого первые `failures` отправок падают."""

    def __init__(self, failures: int):
        self._failures = failures
        self.sent = 0

    async def send_action(self, action: str) -> None:
        self.sent += 1
        if self.sent <= self._failures:
            raise telegram.error.TimedOut()


def _pulses_during(chat, seconds: float) -> int:
    async def scenario() -> None:
        async with telegram_utils._pulsing_chat_action(chat, "upload_video"):
            await asyncio.sleep(seconds)

    asyncio.run(scenario())
    return chat.sent


def test_pulse_continues_after_a_failed_send(monkeypatch):
    """Сбой отправки не повод бросать отметку до конца работы."""
    monkeypatch.setattr(telegram_utils, "_CHAT_ACTION_REFRESH_SECONDS", 0.05)
    chat = _Chat(failures=1)

    sent = _pulses_during(chat, 0.3)

    assert sent > 1, "после ошибки пульс больше не пытался"


def test_pulse_does_not_hot_loop_when_every_send_fails(monkeypatch):
    """Пауза обязана соблюдаться и на ошибках, иначе это busy-loop."""
    monkeypatch.setattr(telegram_utils, "_CHAT_ACTION_REFRESH_SECONDS", 0.05)
    chat = _Chat(failures=1000)

    sent = _pulses_during(chat, 0.3)

    assert sent <= 8, f"за 0.3с при паузе 0.05с не может быть {sent} попыток"


def test_pulse_stops_when_the_work_is_over(monkeypatch):
    """Отметка живёт ровно столько, сколько работа."""
    monkeypatch.setattr(telegram_utils, "_CHAT_ACTION_REFRESH_SECONDS", 0.05)
    chat = _Chat(failures=0)

    sent = _pulses_during(chat, 0.12)
    after_exit = chat.sent

    asyncio.run(asyncio.sleep(0.2))

    assert sent >= 1
    assert chat.sent == after_exit, "пульс продолжился после выхода из блока"


def test_disabled_pulse_sends_nothing():
    """На дешёвых действиях отметка не нужна и не должна шуметь."""
    chat = _Chat(failures=0)

    async def scenario() -> None:
        async with telegram_utils._pulsing_chat_action(chat, "upload_video", False):
            await asyncio.sleep(0.05)

    asyncio.run(scenario())

    assert chat.sent == 0
```

- [ ] **Step 2: Запустить тест и убедиться, что он падает**

Run: `pytest tests/test_chat_action_resilience.py -v`
Expected: FAIL на `test_pulse_continues_after_a_failed_send` —
`assert 1 > 1`, потому что после первой ошибки цикл выходит.
Остальные три проходят.

- [ ] **Step 3: Сделать цикл живучим**

В `utils/telegram_utils.py` заменить тело `_pulse`:

```python
    async def _pulse() -> None:
        failures = 0
        while True:
            try:
                await chat.send_action(action)
                failures = 0
            except telegram.error.TelegramError as error:
                failures += 1
                # Первый сбой — рядовое дело на длинной выгрузке, поэтому
                # шумим только когда отметка не проходит подряд: это уже
                # означает, что пользователь всё время видит пустую шапку.
                if failures == _CHAT_ACTION_FAILURES_BEFORE_WARNING:
                    logger.warning(
                        "Отметка активности не проходит %s раз подряд: %s",
                        failures,
                        error,
                    )
                else:
                    logger.debug("Отметка активности не отправлена: %s", error)
            await asyncio.sleep(_CHAT_ACTION_REFRESH_SECONDS)
```

Рядом с `_CHAT_ACTION_REFRESH_SECONDS` (`utils/telegram_utils.py:736`) добавить:

```python
# Сколько подряд неудачных отметок терпим молча. Одна — рядовой сбой на
# длинной выгрузке; серия означает, что шапка чата пуста всё время работы.
_CHAT_ACTION_FAILURES_BEFORE_WARNING = 3
```

Также поправить docstring контекстного менеджера — прежняя формулировка
«отказ работу не роняет» остаётся верной, но нужно добавить, что отказ и не
прекращает попытки:

```python
    """Держит отметку «отправляет видео…» в шапке чата на всё время работы.

    Это единственная анимация, доступная боту: рисовать «крутилку» правкой
    текста значило бы запрос на каждый кадр и затирание статусов. Отметка —
    украшение, поэтому её отказ работу не роняет и не прекращает попыток:
    прежний цикл выходил навсегда после первой ошибки, и на длинной отправке
    шапка чата пустела до самого конца.
    """
```

- [ ] **Step 4: Запустить тесты и убедиться, что они проходят**

Run: `pytest tests/test_chat_action_resilience.py -v`
Expected: PASS, все четыре теста.

- [ ] **Step 5: Проверить прежние тесты пульса**

Run: `pytest tests/test_chat_action_pulse.py -v`
Expected: PASS без правок.

- [ ] **Step 6: Commit**

```bash
git add utils/telegram_utils.py tests/test_chat_action_resilience.py
git commit -m "fix: отметка активности не пропадает после сетевого сбоя"
```

---

### Task 3: Закрепить свойства общего состояния

Параллельность опирается на то, что общее состояние меняется без `await`
посередине. Сейчас это так, но держится на случайности — тест превращает это в
требование.

**Files:**
- Create: `tests/test_shared_state_under_concurrency.py`

**Interfaces:**
- Consumes: `main.UPDATE_CONCURRENCY` из Task 1.

- [ ] **Step 1: Написать тест**

Создать `tests/test_shared_state_under_concurrency.py`:

```python
"""Общее состояние обязано меняться без `await` посередине.

Апдейты теперь обрабатываются параллельно (`main.UPDATE_CONCURRENCY`). Гонок
данных в одном event loop не бывает, а логическое переплетение — бывает:
прочитал счётчик → `await` → записал устаревшее значение. Пока все правки
общего состояния синхронны, переплетаться нечему. Тесты закрепляют именно это
свойство, чтобы будущий `await` внутри не проехал незамеченным.
"""

import inspect

import pytest

from utils import callback_fsm, telegram_utils


pytestmark = pytest.mark.unit


def _has_await(func) -> bool:
    return "await " in inspect.getsource(func)


def test_antispam_check_is_synchronous():
    """Между чтением и записью списка запросов не должно быть точки переключения."""
    assert not inspect.iscoroutinefunction(telegram_utils._check_spam)
    assert not _has_await(telegram_utils._check_spam)


def test_session_store_mutations_are_synchronous():
    """Создание и удаление сессии — атомарные операции для event loop."""
    for method in (
        callback_fsm.SessionStore.create,
        callback_fsm.SessionStore.remove,
    ):
        assert not inspect.iscoroutinefunction(method), method.__name__
        assert not _has_await(method), method.__name__


def test_antispam_counts_every_request():
    """Ни один запрос не теряется: четвёртый за окно упирается в лимит."""

    class _Context:
        def __init__(self):
            self.user_data: dict = {}

    context = _Context()
    verdicts = [
        telegram_utils._check_spam(1, context, now=100.0 + index * 0.1)
        for index in range(telegram_utils._SPAM_REQUEST_LIMIT)
    ]

    assert verdicts[:-1] == [False] * (telegram_utils._SPAM_REQUEST_LIMIT - 1)
    assert verdicts[-1] is True


def test_two_users_do_not_share_antispam_state():
    """Счётчики живут в user_data, поэтому один пользователь не блокирует другого."""

    class _Context:
        def __init__(self):
            self.user_data: dict = {}

    first, second = _Context(), _Context()
    for index in range(telegram_utils._SPAM_REQUEST_LIMIT):
        telegram_utils._check_spam(1, first, now=100.0 + index * 0.1)

    assert telegram_utils._check_spam(2, second, now=100.5) is False
```

- [ ] **Step 2: Запустить тест**

Run: `pytest tests/test_shared_state_under_concurrency.py -v`

Expected: PASS. Тесты описывают уже существующие свойства — это страховка, а не
починка. **Если что-то упало — останов:** значит найдено настоящее место
переплетения, и его надо разобрать до продолжения выпуска, а не обходить
правкой теста.

- [ ] **Step 3: Commit**

```bash
git add tests/test_shared_state_under_concurrency.py
git commit -m "test: закрепить синхронность правок общего состояния"
```

---

### Task 4: Документация правила

Правило неочевидное и легко нарушаемое: теперь хэндлер, который держит апдейт,
больше не «просто медленный» — он снова ломает отмену.

**Files:**
- Modify: `CLAUDE.md` — раздел «Key Patterns»
- Modify: `AGENTS.md` — рядом с описанием архитектуры

- [ ] **Step 1: Дописать правило в `CLAUDE.md`**

В раздел `## Key Patterns`, сразу после пункта про `Async + ThreadPoolExecutor`,
добавить:

```markdown
- **Параллельная обработка апдейтов**: `UPDATE_CONCURRENCY` в `main.py` (32,
  заведомо больше `DOWNLOAD_WORKERS`). До её включения PTB обрабатывал апдейты
  строго по одному, и нажатие «Отменить» лежало в очереди всё скачивание —
  механизм отмены был исправен, но сигнал до него не доходил. Отсюда правило:
  **хэндлер не имеет права держать фетчер апдейтов дольше необходимого**.
  Блокирующая работа — только через `run_blocking` с `session_id=`. Тест
  доставки — `tests/test_concurrent_update_delivery.py`, он падает, если
  параллельность снова выключат
```

- [ ] **Step 2: Дописать в `AGENTS.md`**

Найти строку про планировщик задач и graceful shutdown в описании `main.py` и
добавить рядом:

```markdown
- Обрабатывает апдейты параллельно (`UPDATE_CONCURRENCY = 32`): последовательная
  обработка по умолчанию лишала смысла кнопку отмены и не давала принять вторую
  ссылку во время скачивания.
```

- [ ] **Step 3: Проверить, что тесты документации не сломались**

Run: `pytest tests/test_documentation_consistency.py -v`
Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add CLAUDE.md AGENTS.md
git commit -m "docs: правило про параллельную обработку апдейтов"
```

---

### Task 5: Проверка целиком и выпуск

- [ ] **Step 1: Полный набор и линтинг**

```bash
ruff check --output-format=github .
pytest -q
```

Expected: ruff без замечаний, все тесты проходят.

- [ ] **Step 2: Покрытие не упало ниже порога CI**

```bash
coverage run --branch -m pytest tests/
coverage report --fail-under=40
```

Expected: PASS.

- [ ] **Step 3: Проверить руками на живом боте**

Обязательно, потому что автотест проверяет фетчер PTB, а не настоящий Telegram:

1. Прислать ссылку на большое видео, начать скачивание.
2. Нажать «Отменить» — статус обязан сменяться на «Отменено» **в течение
   секунды**, а не после отправки файла.
3. Не дожидаясь конца, прислать вторую ссылку — меню форматов должно прийти
   сразу.
4. Проверить в логах, что отметка активности не пропадает: `docker compose logs
   bot | grep -i "отметка активности"`.

- [ ] **Step 4: Выпуск**

Ветка, PR, дождаться зелёного CI, rebase-мерж, тег `v1.7.1` (исправления без
новой функциональности), дождаться сборки образа, затем на сервере:

```bash
ssh dockge 'cd /opt/stacks/nuvio && docker compose pull && docker compose up -d'
```

- [ ] **Step 5: Проверить прод после обновления**

```bash
ssh dockge 'cd /opt/stacks/nuvio && docker compose ps && docker compose logs bot --since 5m | grep -ciE "error|traceback"'
```

Expected: контейнеры `Up`, ошибок 0. Повторить проверку отмены из Step 3 на
боевом боте.

---

## Что этот выпуск не делает

Очередь задач, лимит трёх задач, тасклист одним сообщением, проценты
скачивания, сообщение о потерянной после перезапуска очереди — всё это
выпуск 2 по спеке
`docs/superpowers/specs/2026-07-29-download-queue-and-progress-design.md`.

После этого выпуска вторая ссылка **принимается**, но скачивания идут
одновременно, а не по очереди: пользователь может запустить сколько угодно
параллельных загрузок в пределах `DOWNLOAD_WORKERS`. Ограничение тремя задачами
появится в выпуске 2. Риск на этот промежуток осознанный: антиспам (4 запроса
за 5 секунд) остаётся единственным ограничителем.
