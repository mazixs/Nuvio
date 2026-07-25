# Кэш-покрытие и устранение дублирующих запросов — план реализации

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Включить кэш `file_id` для шести действий, где он сейчас не даёт попаданий, и убрать повторное развёртывание короткой ссылки в быстром пути TikTok.

**Architecture:** Ключи кэша живут в чистой функции `cache_key_for_main_action` (`utils/platform_actions.py`). Для Rutube и VK видео она возвращает `None`, поэтому и чтение, и запись мертвы — правится одной функцией, так как оба конца её вызывают. Для аудио запись работает по жёстко заданным строкам, но блока чтения нет ни в одном из четырёх обработчиков — добавляется общий хелпер доставки из кэша. Отдельно быстрый путь TikTok разворачивает короткую ссылку повторно после того, как вызывающий код это уже сделал.

**Tech Stack:** Python 3.14, python-telegram-bot 22.8, SQLite (WAL), pytest 9.1.1, ruff 0.16.0.

## Global Constraints

- Python 3.14+; окружение проекта — `.venv`, все команды запускать как `.venv/bin/python -m …`.
- Ruff-политика зафиксирована: `select = ["E4", "E7", "E9", "F"]` в `pyproject.toml`. Правки не должны добавлять предупреждений — `tests/test_ruff.py` запускает линтер внутри набора.
- Маркеры pytest только `syntax`, `unit`, `integration` (включён `--strict-markers`). Добавлять маркеры запрещено — `tests/test_dead_code_contract.py` это проверяет.
- Комментарии и docstrings — на русском, идентификаторы — на английском.
- `print()` в production-коде запрещён (`tests/test_syntax.py::TestCodeQuality::test_no_print_statements`), звёздочные импорты запрещены там же.
- Все пользовательские тексты берутся из `messages.py`, не хардкодятся в обработчиках.
- Порог покрытия: `coverage report --fail-under=40` должен проходить.
- **Формат `callback_data` не меняется.** Ни одна задача не добавляет и не переименовывает кнопки.
- **Значения ключей кэша менять нельзя.** В таблице `video_cache` уже лежат записи с `format_id` равным `"tiktok_audio"`, `"instagram_audio"`, `"rutube_audio"`, `"vk_audio"` — новые ключи обязаны совпадать с этими строками побайтово, иначе существующие записи станут недостижимыми.
- Финальная проверка каждой задачи: `.venv/bin/python -m ruff check .` и `.venv/bin/python -m pytest` — обе без ошибок.

## File Structure

| Файл | Ответственность | Изменение |
|---|---|---|
| `utils/platform_actions.py` | Чистое сопоставление «платформа + действие → ключ кэша» | Расширяется на Rutube, VK и аудио |
| `utils/telegram_utils.py` | Координация обработчиков | Добавляется хелпер `_deliver_cached_audio` и четыре блока чтения кэша |
| `utils/tiktok_instagram_utils.py` | Платформенные загрузчики | Быстрый путь принимает уже развёрнутый URL |
| `tests/test_platform_actions.py` | Тесты чистых решений | Новые кейсы и контракт покрытия |
| `tests/test_cached_audio_delivery.py` | Новый: доставка аудио из кэша | Создаётся |
| `tests/test_tiktok_fast_download.py` | Тесты быстрого пути | Новые кейсы на однократное развёртывание |

## Текущее состояние (замерено, не предполагается)

| Действие | Чтение кэша | Запись в кэш | Итог |
|---|---|---|---|
| `tiktok_download` | есть, `direct_video` | есть, `direct_video` | работает |
| `instagram_download` | есть, `direct_video` | есть, `direct_video` | работает |
| `tg_video` (YouTube) | есть, `tg_video` | есть, `tg_video` | работает |
| `rutube_download` | вызывается с `None` | ключ `None` → не пишет | **мертво** |
| `vk_download` | вызывается с `None` | ключ `None` → не пишет | **мертво** |
| `tiktok_audio` | **блока нет** | есть, `"tiktok_audio"` | пишет, не читает |
| `instagram_audio` | **блока нет** | есть, `"instagram_audio"` | пишет, не читает |
| `rutube_audio` | **блока нет** | есть, `"rutube_audio"` | пишет, не читает |
| `vk_audio` | **блока нет** | есть, `"vk_audio"` | пишет, не читает |

---

### Task 1: Ключи кэша для видео Rutube и VK

Обработчики `rutube_download` (`utils/telegram_utils.py:1556`) и `vk_download` (`utils/telegram_utils.py:1656`) уже вызывают `_cache_format_id_for_main_action` и на чтении, и на записи. Поэтому исправление одной чистой функции включает кэш на обоих концах — правки в `telegram_utils.py` в этой задаче не нужны.

**Files:**
- Modify: `utils/platform_actions.py:4-13`
- Test: `tests/test_platform_actions.py`

**Interfaces:**
- Consumes: ничего из предыдущих задач.
- Produces: `cache_key_for_main_action(platform: str, action: str) -> str | None` — теперь возвращает `"direct_video"` для `("rutube", "rutube_download")` и `("vk", "vk_download")`. Модульная константа `_DIRECT_VIDEO_PLATFORMS: frozenset[str]`. Task 2 расширяет эту же функцию.

- [ ] **Step 1: Написать падающий тест**

Дописать в конец `tests/test_platform_actions.py`:

```python
def test_main_action_cache_keys_cover_rutube_and_vk():
    assert cache_key_for_main_action("rutube", "rutube_download") == "direct_video"
    assert cache_key_for_main_action("vk", "vk_download") == "direct_video"
```

- [ ] **Step 2: Запустить тест и убедиться, что он падает**

Run: `.venv/bin/python -m pytest tests/test_platform_actions.py::test_main_action_cache_keys_cover_rutube_and_vk -v`

Expected: FAIL с `AssertionError: assert None == 'direct_video'`

- [ ] **Step 3: Минимальная реализация**

В `utils/platform_actions.py` заменить блок со строк 4-13.

Было:

```python
DIRECT_VIDEO_CACHE_KEY = "direct_video"


def cache_key_for_main_action(platform: str, action: str) -> str | None:
    """Возвращает ключ кэша для основной кнопки платформы."""
    if platform in {"tiktok", "instagram"} and action.endswith("_download"):
        return DIRECT_VIDEO_CACHE_KEY
    if platform == "youtube" and action == "tg_video":
        return "tg_video"
    return None
```

Стало:

```python
DIRECT_VIDEO_CACHE_KEY = "direct_video"

# Платформы, у которых основная кнопка отдаёт единственный вариант видео,
# поэтому один ключ кэша на URL достаточен.
_DIRECT_VIDEO_PLATFORMS = frozenset({"tiktok", "instagram", "rutube", "vk"})


def cache_key_for_main_action(platform: str, action: str) -> str | None:
    """Возвращает ключ кэша для основной кнопки платформы."""
    if platform in _DIRECT_VIDEO_PLATFORMS and action.endswith("_download"):
        return DIRECT_VIDEO_CACHE_KEY
    if platform == "youtube" and action == "tg_video":
        return "tg_video"
    return None
```

- [ ] **Step 4: Запустить тест и убедиться, что он проходит**

Run: `.venv/bin/python -m pytest tests/test_platform_actions.py -v`

Expected: PASS, все три теста файла зелёные. В частности существующий `test_main_action_cache_keys_are_explicit` должен остаться зелёным: `cache_key_for_main_action("youtube", "audio_m4a")` по-прежнему `None`, потому что `"youtube"` не входит в `_DIRECT_VIDEO_PLATFORMS`.

- [ ] **Step 5: Прогнать весь набор и линтер**

Run: `.venv/bin/python -m ruff check . && .venv/bin/python -m pytest -q`

Expected: `All checks passed!` и `208 passed` (207 существующих плюс один новый).

- [ ] **Step 6: Коммит**

```bash
git add utils/platform_actions.py tests/test_platform_actions.py
git commit -m "fix: включить кэш file_id для видео Rutube и VK

Ключ возвращался None, из-за чего чтение никогда не попадало,
а запись молча пропускалась. Повторный запрос качал файл заново.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: Чтение кэша для аудио на четырёх платформах

Запись аудио в кэш уже работает, но по жёстко заданным строкам в четырёх местах, и ни один обработчик не читает кэш перед скачиванием. Задача добавляет ключи в чистую функцию, заводит общий хелпер доставки и подключает его во все четыре обработчика, заменив хардкод вызовом функции, чтобы ключи чтения и записи не могли разойтись.

**Files:**
- Modify: `utils/platform_actions.py` (функция `cache_key_for_main_action` из Task 1)
- Modify: `utils/telegram_utils.py` — новый хелпер рядом с `_cache_format_id_for_main_action` (строка 813), затем четыре обработчика: `case "tiktok_audio"` (1373), `case "instagram_audio"` (1507), `case "rutube_audio"` (1617), `case "vk_audio"` (1715)
- Create: `tests/test_cached_audio_delivery.py`
- Test: `tests/test_platform_actions.py`

**Interfaces:**
- Consumes: `cache_key_for_main_action` и `_DIRECT_VIDEO_PLATFORMS` из Task 1.
- Produces: `cache_key_for_main_action` дополнительно возвращает само `action` для действий, оканчивающихся на `_audio`, у платформ из `_DIRECT_VIDEO_PLATFORMS`. Новая корутина `_deliver_cached_audio(query, url: str, cache_key: str) -> bool` в `utils/telegram_utils.py` — `True`, если файл доставлен из кэша.

> **Замечание для исполнителя:** `tests/test_dead_code_contract.py:28` проверяет отсутствие атрибута `_try_send_cached`. Имя нового хелпера — `_deliver_cached_audio`, оно этой проверке не противоречит. Не переименовывайте его в `_try_send_cached*`.

- [ ] **Step 1: Написать падающий тест на ключи аудио**

Дописать в `tests/test_platform_actions.py`:

```python
def test_main_action_cache_keys_cover_audio_actions():
    assert cache_key_for_main_action("tiktok", "tiktok_audio") == "tiktok_audio"
    assert cache_key_for_main_action("instagram", "instagram_audio") == "instagram_audio"
    assert cache_key_for_main_action("rutube", "rutube_audio") == "rutube_audio"
    assert cache_key_for_main_action("vk", "vk_audio") == "vk_audio"
```

- [ ] **Step 2: Запустить и убедиться, что падает**

Run: `.venv/bin/python -m pytest tests/test_platform_actions.py::test_main_action_cache_keys_cover_audio_actions -v`

Expected: FAIL с `AssertionError: assert None == 'tiktok_audio'`

- [ ] **Step 3: Добавить ветку аудио в чистую функцию**

В `utils/platform_actions.py`, внутри `cache_key_for_main_action`, перед `return None` добавить:

```python
    if platform in _DIRECT_VIDEO_PLATFORMS and action.endswith("_audio"):
        # Значение совпадает с ключами, под которыми записи уже лежат в кэше.
        return action
```

- [ ] **Step 4: Запустить и убедиться, что проходит**

Run: `.venv/bin/python -m pytest tests/test_platform_actions.py -v`

Expected: PASS. `cache_key_for_main_action("youtube", "audio_m4a")` остаётся `None` — `"youtube"` не входит в `_DIRECT_VIDEO_PLATFORMS`.

- [ ] **Step 5: Коммит**

```bash
git add utils/platform_actions.py tests/test_platform_actions.py
git commit -m "feat: добавить ключи кэша для аудио-действий

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

- [ ] **Step 6: Написать падающий тест на хелпер доставки**

Создать `tests/test_cached_audio_delivery.py`:

```python
"""Тесты доставки аудио из кэша file_id."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import telegram

from utils import telegram_utils


def _query(reply_audio):
    return SimpleNamespace(message=SimpleNamespace(reply_audio=reply_audio))


@pytest.mark.unit
def test_cached_audio_is_delivered_by_file_id(monkeypatch):
    reply_audio = AsyncMock()
    monkeypatch.setattr(
        telegram_utils.telegram_cache,
        "get",
        lambda url, format_id: SimpleNamespace(file_id="AGADcached"),
    )

    delivered = asyncio.run(
        telegram_utils._deliver_cached_audio(
            _query(reply_audio), "https://example.test/v", "tiktok_audio"
        )
    )

    assert delivered is True
    assert reply_audio.await_args.kwargs["audio"] == "AGADcached"


@pytest.mark.unit
def test_missing_cache_entry_reports_not_delivered(monkeypatch):
    reply_audio = AsyncMock()
    monkeypatch.setattr(
        telegram_utils.telegram_cache, "get", lambda url, format_id: None
    )

    delivered = asyncio.run(
        telegram_utils._deliver_cached_audio(
            _query(reply_audio), "https://example.test/v", "tiktok_audio"
        )
    )

    assert delivered is False
    reply_audio.assert_not_awaited()


@pytest.mark.unit
def test_stale_file_id_is_dropped_from_cache(monkeypatch):
    """Устаревший file_id должен удаляться, чтобы не отдаваться повторно."""
    reply_audio = AsyncMock(side_effect=telegram.error.BadRequest("wrong file_id"))
    deleted: list[str] = []
    monkeypatch.setattr(
        telegram_utils.telegram_cache,
        "get",
        lambda url, format_id: SimpleNamespace(file_id="AGADstale"),
    )
    monkeypatch.setattr(
        telegram_utils.telegram_cache,
        "delete_by_file_id",
        lambda file_id: deleted.append(file_id),
    )

    delivered = asyncio.run(
        telegram_utils._deliver_cached_audio(
            _query(reply_audio), "https://example.test/v", "tiktok_audio"
        )
    )

    assert delivered is False
    assert deleted == ["AGADstale"]
```

- [ ] **Step 7: Запустить и убедиться, что падает**

Run: `.venv/bin/python -m pytest tests/test_cached_audio_delivery.py -v`

Expected: FAIL с `AttributeError: module 'utils.telegram_utils' has no attribute '_deliver_cached_audio'`

- [ ] **Step 8: Реализовать хелпер**

В `utils/telegram_utils.py` сразу после функции `_cache_format_id_for_main_action` (заканчивается на строке 815) добавить:

```python
async def _deliver_cached_audio(query, url: str, cache_key: str) -> bool:
    """Отправляет аудио из кэша по file_id.

    Returns:
        bool: True, если файл доставлен; False, если записи нет или file_id устарел.
    """
    cached = telegram_cache.get(url, format_id=cache_key)
    if not cached:
        return False

    try:
        await query.message.reply_audio(audio=cached.file_id)
    except telegram.error.BadRequest as e:
        logger.warning("file_id аудио устарел (key=%s): %s", cache_key, e)
        telegram_cache.delete_by_file_id(cached.file_id)
        return False

    logger.info("Аудио доставлено из кэша (key=%s)", cache_key)
    return True
```

- [ ] **Step 9: Запустить и убедиться, что проходит**

Run: `.venv/bin/python -m pytest tests/test_cached_audio_delivery.py -v`

Expected: PASS, три теста.

- [ ] **Step 10: Коммит**

```bash
git add utils/telegram_utils.py tests/test_cached_audio_delivery.py
git commit -m "feat: добавить доставку аудио из кэша file_id

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

- [ ] **Step 11: Подключить чтение кэша в обработчик tiktok_audio**

В `utils/telegram_utils.py` найти `case "tiktok_audio":` (строка 1373). Было:

```python
        case "tiktok_audio":
            await safe_edit_message_text(query, DOWNLOADING_AUDIO_MESSAGE)
            from utils.tiktok_instagram_utils import download_tiktok_audio
```

Стало:

```python
        case "tiktok_audio":
            cache_key = _cache_format_id_for_main_action("tiktok", "tiktok_audio")
            if cache_key and await _deliver_cached_audio(query, url, cache_key):
                await query.edit_message_text(FILE_SENT)
                await _cleanup_user_session(user_id, context, session_token)
                return

            await safe_edit_message_text(query, DOWNLOADING_AUDIO_MESSAGE)
            from utils.tiktok_instagram_utils import download_tiktok_audio
```

В том же обработчике заменить хардкод ключа записи. Было:

```python
                    cache_format_id="tiktok_audio",
```

Стало:

```python
                    cache_format_id=cache_key,
```

- [ ] **Step 12: Повторить то же для трёх остальных обработчиков**

`case "instagram_audio":` (строка 1507) — вставить перед `await safe_edit_message_text(query, DOWNLOADING_AUDIO_MESSAGE)`:

```python
            cache_key = _cache_format_id_for_main_action(
                "instagram", "instagram_audio"
            )
            if cache_key and await _deliver_cached_audio(query, url, cache_key):
                await query.edit_message_text(FILE_SENT)
                await _cleanup_user_session(user_id, context, session_token)
                return

```

и заменить `cache_format_id="instagram_audio",` на `cache_format_id=cache_key,`.

`case "rutube_audio":` (строка 1617) — вставить в том же месте:

```python
            cache_key = _cache_format_id_for_main_action("rutube", "rutube_audio")
            if cache_key and await _deliver_cached_audio(query, url, cache_key):
                await query.edit_message_text(FILE_SENT)
                await _cleanup_user_session(user_id, context, session_token)
                return

```

и заменить `cache_format_id="rutube_audio",` на `cache_format_id=cache_key,`.

`case "vk_audio":` (строка 1715) — вставить в том же месте:

```python
            cache_key = _cache_format_id_for_main_action("vk", "vk_audio")
            if cache_key and await _deliver_cached_audio(query, url, cache_key):
                await query.edit_message_text(FILE_SENT)
                await _cleanup_user_session(user_id, context, session_token)
                return

```

и заменить `cache_format_id="vk_audio",` на `cache_format_id=cache_key,`.

> Номера строк сдвигаются после каждой вставки. Ориентируйтесь на строку `case "<имя>":`, а не на номер.

- [ ] **Step 13: Написать контрактный тест покрытия всех действий**

Дописать в `tests/test_platform_actions.py`:

```python
def test_every_main_action_has_a_cache_key():
    """Ни одно действие главного меню не должно оставаться без ключа кэша."""
    actions = [
        ("tiktok", "tiktok_download"),
        ("tiktok", "tiktok_audio"),
        ("instagram", "instagram_download"),
        ("instagram", "instagram_audio"),
        ("rutube", "rutube_download"),
        ("rutube", "rutube_audio"),
        ("vk", "vk_download"),
        ("vk", "vk_audio"),
        ("youtube", "tg_video"),
    ]

    without_key = [
        action for platform, action in actions
        if cache_key_for_main_action(platform, action) is None
    ]

    assert without_key == []
```

- [ ] **Step 14: Запустить и убедиться, что проходит**

Run: `.venv/bin/python -m pytest tests/test_platform_actions.py -v`

Expected: PASS. Этот тест написан после реализации намеренно — он страхует от будущего регресса, а не ведёт разработку. Убедитесь, что он действительно работает: временно уберите ветку аудио из Step 3, увидьте падение со списком непокрытых действий, верните ветку.

- [ ] **Step 15: Проверить, что не сломались обработчики**

Run: `.venv/bin/python -m ruff check . && .venv/bin/python -m pytest -q`

Expected: `All checks passed!` и все тесты зелёные. Обратите особое внимание на `tests/test_telegram_csi.py` и `tests/test_audit_regressions.py` — они затрагивают `telegram_utils`.

- [ ] **Step 16: Коммит**

```bash
git add utils/telegram_utils.py tests/test_platform_actions.py
git commit -m "fix: читать кэш перед скачиванием аудио на всех платформах

Запись работала, но ни один обработчик не проверял кэш,
поэтому повторный запрос всегда качал файл заново.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: Однократное развёртывание короткой ссылки в быстром пути TikTok

`download_tiktok_video` разворачивает ссылку на строке 1417, после чего вызывает `download_tiktok_video_fast` (строка 1434), который разворачивает её снова на строке 476. То же в аудио-паре: строки 1914 и 503. Каждое развёртывание — это HTTP-запрос к TikTok.

**Files:**
- Modify: `utils/tiktok_instagram_utils.py:462-486` (`download_tiktok_video_fast`)
- Modify: `utils/tiktok_instagram_utils.py:488-520` (`download_tiktok_audio_fast`)
- Modify: `utils/tiktok_instagram_utils.py:1434` и `:1923` (места вызова)
- Test: `tests/test_tiktok_fast_download.py`

**Interfaces:**
- Consumes: `download_tiktok_video_fast`, `download_tiktok_audio_fast`, `fetch_tiktok_fast_media` из текущего кода.
- Produces: обе функции быстрого пути принимают дополнительный именованный параметр `resolved_url: str | None = None`. Если он передан, `_resolve_tiktok_url` не вызывается. Сигнатуры: `download_tiktok_video_fast(url: str, session_id: str, output_dir: Path | None = None, force_local: bool = False, resolved_url: str | None = None) -> Path` и такая же для `download_tiktok_audio_fast`.

- [ ] **Step 1: Написать падающий тест**

Дописать в `tests/test_tiktok_fast_download.py`:

```python
@pytest.mark.unit
def test_fast_video_reuses_already_resolved_url(monkeypatch, tmp_path):
    """Ссылку уже развернул вызывающий код — повторный запрос лишний."""
    resolves: list[str] = []

    def _resolve(url):
        resolves.append(url)
        return "https://www.tiktok.com/@tester/video/1"

    monkeypatch.setattr(tiktok_instagram_utils, "_resolve_tiktok_url", _resolve)
    monkeypatch.setattr(
        tiktok_instagram_utils,
        "_download_remote_file",
        lambda url, destination, referer=None: (
            destination.write_bytes(b"media") or destination
        ),
    )
    monkeypatch.setattr(
        tiktok_instagram_utils,
        "_call_tiktok_resolver",
        lambda url: _resolver_payload(),
    )

    tiktok_instagram_utils.download_tiktok_video_fast(
        "https://vt.tiktok.com/short/",
        "session-reuse",
        output_dir=tmp_path,
        resolved_url="https://www.tiktok.com/@tester/video/1",
    )

    assert resolves == []


@pytest.mark.unit
def test_download_tiktok_video_resolves_url_once(monkeypatch, tmp_path):
    """На одну доставку должно приходиться одно развёртывание ссылки."""
    resolves: list[str] = []

    def _resolve(url):
        resolves.append(url)
        return "https://www.tiktok.com/@tester/video/1"

    monkeypatch.setattr(tiktok_instagram_utils, "_resolve_tiktok_url", _resolve)
    monkeypatch.setattr(
        tiktok_instagram_utils,
        "_download_remote_file",
        lambda url, destination, referer=None: (
            destination.write_bytes(b"media") or destination
        ),
    )
    monkeypatch.setattr(
        tiktok_instagram_utils,
        "_call_tiktok_resolver",
        lambda url: _resolver_payload(),
    )
    monkeypatch.setattr(
        tiktok_instagram_utils, "TIKTOK_FAST_PATH", True, raising=False
    )

    tiktok_instagram_utils.download_tiktok_video(
        "https://vt.tiktok.com/short/", "session-once", output_dir=tmp_path
    )

    assert len(resolves) == 1
```

- [ ] **Step 2: Запустить и убедиться, что падают**

Run: `.venv/bin/python -m pytest tests/test_tiktok_fast_download.py -v -k "reuses_already_resolved or resolves_url_once"`

Expected: первый тест FAIL с `TypeError: download_tiktok_video_fast() got an unexpected keyword argument 'resolved_url'`; второй FAIL с `assert 2 == 1`.

- [ ] **Step 3: Добавить параметр в функции быстрого пути**

В `utils/tiktok_instagram_utils.py`, в `download_tiktok_video_fast`, изменить сигнатуру и первую строку тела.

Было:

```python
def download_tiktok_video_fast(
    url: str,
    session_id: str,
    output_dir: Path | None = None,
    force_local: bool = False,
) -> Path:
```

Стало:

```python
def download_tiktok_video_fast(
    url: str,
    session_id: str,
    output_dir: Path | None = None,
    force_local: bool = False,
    resolved_url: str | None = None,
) -> Path:
```

Внутри той же функции было:

```python
    resolved_url = _resolve_tiktok_url(url)
    media = fetch_tiktok_fast_media(resolved_url)
```

Стало:

```python
    media = fetch_tiktok_fast_media(resolved_url or _resolve_tiktok_url(url))
```

Ровно те же две правки применить к `download_tiktok_audio_fast`.

- [ ] **Step 4: Передать развёрнутую ссылку из вызывающего кода**

В `download_tiktok_video` (место вызова на строке 1434) было:

```python
            return download_tiktok_video_fast(
                url, session_id, output_dir, force_local
            )
```

Стало:

```python
            return download_tiktok_video_fast(
                url, session_id, output_dir, force_local, resolved_url=resolved_url
            )
```

В `download_tiktok_audio` (строка 1923) было:

```python
            return download_tiktok_audio_fast(
                url, session_id, output_dir, force_local
            )
```

Стало:

```python
            return download_tiktok_audio_fast(
                url, session_id, output_dir, force_local, resolved_url=resolved_url
            )
```

Обе вызывающие функции уже имеют локальную переменную `resolved_url` — в `download_tiktok_video` она заводится на строке 1417, в `download_tiktok_audio` на строке 1914.

- [ ] **Step 5: Запустить и убедиться, что проходят**

Run: `.venv/bin/python -m pytest tests/test_tiktok_fast_download.py -v`

Expected: PASS, восемь тестов файла.

- [ ] **Step 6: Прогнать весь набор и линтер**

Run: `.venv/bin/python -m ruff check . && .venv/bin/python -m coverage run --branch -m pytest tests/ -q && .venv/bin/python -m coverage report --fail-under=40`

Expected: линтер чист, все тесты зелёные, покрытие не ниже 40 %.

- [ ] **Step 7: Живая проверка (опционально, требует сети)**

Run:

```bash
.venv/bin/python - <<'PY'
from utils import tiktok_instagram_utils as t
calls = []
orig = t._resolve_tiktok_url
t._resolve_tiktok_url = lambda u: (calls.append(u), orig(u))[1]
p = t.download_tiktok_video_fast("https://vt.tiktok.com/ZSxeYGgGC/", "live-check")
print(f"развёртываний: {len(calls)} | файл: {p.stat().st_size} б")
p.unlink(missing_ok=True)
PY
```

Expected: `развёртываний: 1`. Ссылка может истечь — при ошибке резолвера возьмите свежую.

- [ ] **Step 8: Коммит**

```bash
git add utils/tiktok_instagram_utils.py tests/test_tiktok_fast_download.py
git commit -m "perf: не разворачивать короткую ссылку TikTok повторно

Быстрый путь разворачивал ссылку заново после вызывающего кода,
что давало лишний HTTP-запрос на каждую доставку.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

## Вне области этого плана

Осознанно не включено, с причинами:

- **Проверка кэша в `process_url` до запроса метаданных.** Экономит время ожидания, но почти не экономит диск и сеть: платит именно скачивание, а не запрос информации. Кроме того требует продуктового решения — отдавать файл сразу (пользователь теряет выбор между видео и аудио) или строить меню из кэшированных `title`/`duration`, чего не хватит для подменю форматов YouTube.
- **Замена yt-dlp на резолвер в `get_tiktok_info`.** Убрала бы полное извлечение через yt-dlp в фазе карточки, но `get_available_formats_tiktok(video_info)` вызывается из `utils/telegram_utils.py:1100` и опирается на структуру ответа yt-dlp. Нужен отдельный разбор того, что именно потребляет словарь информации.
- **Передача URL напрямую в `sendVideo`.** Заблокировано непроверенным: остаётся ли `file_id`, полученный от облачного Bot API, пригодным при последующем обращении через локальный сервер. Проверяется одной живой отправкой с реальным токеном. Плюс выигрыш существует только в облачном режиме — в Docker-стеке фетч выполнит собственный контейнер.
- **tmpfs под `TEMP_DIR`.** По измеренной нагрузке (~25 запросов в сутки, ~200 ГБ записи в год против ресурса SSD 300–600 ТБ) износ диска составляет сотые доли процента в год. Усложнение Compose и риск нехватки памяти не оправданы.
- **Повторный `extract_info` в запасном пути `download_tiktok_audio`.** Затрагивает функцию на 140 строк, которая исполняется только при недоступности резолвера; без возможности воспроизвести отказ живьём риск регресса выше выигрыша.

## Self-review

**Покрытие найденных дефектов:** Rutube и VK видео — Task 1. Аудио на четырёх платформах — Task 2. Дублирующее развёртывание ссылки — Task 3. Все шесть неработающих действий из таблицы «Текущее состояние» закрыты; седьмой найденный пункт (повторный `extract_info`) вынесен в раздел вне области с обоснованием.

**Заглушки:** каждый шаг с кодом содержит код целиком, включая блоки «было/стало». Ожидаемые сообщения об ошибках в шагах проверки указаны дословно.

**Согласованность имён и типов:** `cache_key_for_main_action` и `_DIRECT_VIDEO_PLATFORMS` объявлены в Task 1 и расширяются в Task 2 под теми же именами. `_deliver_cached_audio` объявлен в Task 2 Step 8 и используется в Steps 11-12 с той же сигнатурой. `resolved_url` — одно имя параметра в Task 3 во всех четырёх местах. Значения ключей аудио совпадают с уже записанными строками, поэтому существующие записи кэша не осиротеют.
