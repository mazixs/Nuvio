"""Канареечная проверка YouTube и автоматическая реакция на поломку.

18 августа 2026 YouTube начал отдавать 403 на прямые ссылки `videoplayback`, и
бот стоял сломанным больше суток, пока владелец не наткнулся сам. Починка к тому
моменту уже лежала в nightly yt-dlp, а `ensure_latest_yt_dlp(force=True)` был
написан и просто не подключён ни к какому сигналу. Здесь появляются оба: сигнал
и реакция на него.

Проверка идёт продакшн-путём и «облегчённых» вариантов не признаёт, потому что
на разборе инцидента обе ловушки уже сработали:

* **Маленький кусок врёт ровно тогда, когда проверка нужна.** Запрос `Range` на
  64 КБ отдавал 206, пока продакшн-запрос на 10 МБ (штатный `http_chunk_size`
  из `DEFAULT_YTDLP_NETWORK_OPTS`) получал 403 на том же файле. Ключ `--test` у
  yt-dlp подменяет размер куска на 10 КБ и прошёл бы мимо поломки так же.
  Поэтому канарейка качает ролик целиком и теми же сетевыми опциями, что бот.
* **Кэш `file_id` мерил бы сам себя.** В пользовательском потоке кэш читается
  до скачивания, так что на уже виденной ссылке канарейка получила бы готовый
  `file_id` при полностью сломанном YouTube. Поэтому в модуле нет ни одного
  обращения к `utils.video_cache` — ни на чтение, ни на запись, — а скачивание
  зовётся напрямую через `utils.youtube_utils.download_video`.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

from telegram.ext import ContextTypes

from config import (
    ADMIN_IDS,
    BLOCKING_TASK_TIMEOUT,
    CANARY_ENABLED,
    CANARY_VIDEO_ID,
    MAX_FILE_SIZE,
)
from utils.cancellation import (
    CancelledByUser,
    forget_cancellation,
    request_cancellation,
)
from utils.logger import setup_logger
from utils.public_errors import classify_internal_error_category
from utils.temp_file_manager import cleanup_temp_files
from utils.tg_video_choice import select_tg_video_format
from utils.youtube_utils import download_video, get_available_formats, get_video_info
from utils.ytdlp_runtime import ensure_latest_yt_dlp

logger = setup_logger(__name__)

# Бюджет проверки. Формат выбирает тот же селектор, что и кнопка «отправить
# видео», но бюджет зажат: смысл канарейки — доказать, что медиа отдаётся, а не
# скачать максимум возможного. При локальном Bot API продакшн-бюджет равен 2 ГБ,
# и на десятиминутном ролике это 200+ МБ дважды в сутки на домашнем канале.
# Важно другое: выбранный формат должен быть заметно больше одного куска в 10 МБ,
# иначе проверка не дойдёт до второго запроса к `videoplayback` — того самого,
# на котором инцидент и проявлялся.
CANARY_BUDGET_BYTES = min(MAX_FILE_SIZE, 50 * 1024 * 1024)
CANARY_MAX_HEIGHT = 720

# Тот же предел, что у пользовательских загрузок: канарейка не имеет права висеть
# дольше, чем реальное скачивание.
CANARY_TIMEOUT_SECONDS = BLOCKING_TASK_TIMEOUT

# Не больше одной попытки обновления yt-dlp в сутки. Обновление ставится в
# работающий контейнер и живёт до его пересоздания: следующий `docker compose up`
# вернёт версию из `requirements.txt`. Это не баг, а страховка — канарейка лечит
# прод до того, как владелец дойдёт до правки пина, но сам пин не подменяет.
UPDATE_COOLDOWN_SECONDS = 24 * 60 * 60

# Состояние живёт в модуле: процесс бота один, а переживать перезапуск лимиту не
# нужно — после рестарта одна попытка обновления как раз уместна.
_last_update_attempt_at: float | None = None


@dataclass(frozen=True)
class CanaryOutcome:
    """Итог одной проверки: что делали и чем закончилось."""

    ok: bool
    stage: str
    detail: str
    category: str | None = None
    error_code: str | None = None
    format_id: str | None = None
    size_bytes: int | None = None


def canary_video_url() -> str:
    """Ссылка на эталонный ролик проверки."""
    return f"https://www.youtube.com/watch?v={CANARY_VIDEO_ID}"


def _error_code(category: str) -> str:
    """Код в том же формате, что видит пользователь: `YT-<КАТЕГОРИЯ>-<6 знаков>`.

    Формат повторён здесь намеренно: `_make_error_code` живёт в
    `utils/telegram_utils.py` вместе с хэндлерами и антиспамом, а канарейке из
    этого модуля не нужно ничего, кроме одной строки формата.
    """
    return f"YT-{category.upper()[:8]}-{uuid.uuid4().hex[:6].upper()}"


def _failure(
    stage: str, exc: BaseException, format_id: str | None = None
) -> CanaryOutcome:
    """Превращает исключение в итог с категорией и кодом ошибки."""
    category = classify_internal_error_category("youtube", str(exc))
    return CanaryOutcome(
        ok=False,
        stage=stage,
        detail=f"{type(exc).__name__}: {exc}",
        category=category,
        error_code=_error_code(category),
        format_id=format_id,
    )


def run_youtube_canary_check(session_id: str) -> CanaryOutcome:
    """Качает эталонный ролик тем же кодом, которым бот качает пользовательский.

    Блокирующая: вызывать только из пула потоков.
    """
    url = canary_video_url()
    logger.info("🐤 Канарейка YouTube: проверяю %s (сессия %s)", url, session_id)

    try:
        video_info = get_video_info(url)
    except CancelledByUser:
        return CanaryOutcome(
            ok=False, stage="cancelled", detail="проверка прервана по таймауту"
        )
    except Exception as exc:  # noqa: BLE001
        return _failure("video_info", exc)

    formats = get_available_formats(video_info)
    choice = select_tg_video_format(
        formats.get("video_only", []),
        formats.get("audio_only", []),
        formats.get("combined", []),
        CANARY_BUDGET_BYTES,
        max_height=CANARY_MAX_HEIGHT,
    )
    if choice is None:
        return CanaryOutcome(
            ok=False,
            stage="format_choice",
            detail=(
                "ни один формат не влез в бюджет проверки: обычно так выглядит "
                "пустой или урезанный список форматов"
            ),
            category="FORMAT_UNAVAILABLE",
            error_code=_error_code("FORMAT_UNAVAILABLE"),
        )

    try:
        downloaded = Path(download_video(url, choice.format_id, session_id))
        size_bytes = downloaded.stat().st_size
        if size_bytes <= 0:
            return CanaryOutcome(
                ok=False,
                stage="download",
                detail="файл скачался пустым",
                category="UNKNOWN",
                error_code=_error_code("UNKNOWN"),
                format_id=choice.format_id,
            )
        return CanaryOutcome(
            ok=True,
            stage="download",
            detail=f"скачано {size_bytes / (1024 * 1024):.1f} МБ",
            format_id=choice.format_id,
            size_bytes=size_bytes,
        )
    except CancelledByUser:
        return CanaryOutcome(
            ok=False,
            stage="cancelled",
            detail="проверка прервана по таймауту",
            format_id=choice.format_id,
        )
    except Exception as exc:  # noqa: BLE001
        return _failure("download", exc, format_id=choice.format_id)
    finally:
        # Файл был нужен только как доказательство, что YouTube отдаёт медиа:
        # никому он не отправляется и на диске не остаётся.
        cleanup_temp_files(session_id)
        forget_cancellation(session_id)


async def _check_in_thread() -> CanaryOutcome:
    """Гоняет блокирующую проверку в пуле потоков и не даёт ей висеть вечно."""
    session_id = f"canary-{uuid.uuid4().hex[:8]}"
    try:
        return await asyncio.wait_for(
            asyncio.to_thread(run_youtube_canary_check, session_id),
            timeout=CANARY_TIMEOUT_SECONDS,
        )
    except TimeoutError:
        # `wait_for` снимает только ожидание, а поток продолжает качать — то же
        # поведение, что у `run_blocking`. До yt-dlp отмена доходит хуком,
        # который `download_video` ставит по этому же `session_id`.
        request_cancellation(session_id)
        logger.error(
            "🐤 Канарейка YouTube: проверка не уложилась в %s с",
            CANARY_TIMEOUT_SECONDS,
        )
        return CanaryOutcome(
            ok=False,
            stage="timeout",
            detail=f"проверка не завершилась за {CANARY_TIMEOUT_SECONDS} с",
            category="NETWORK_TIMEOUT",
            error_code=_error_code("NETWORK_TIMEOUT"),
        )


async def _react_to_failure() -> list[str]:
    """Обновляет yt-dlp (не чаще раза в сутки) и повторяет проверку.

    Returns:
        Строки отчёта о реакции — их читает админ в уведомлении.
    """
    global _last_update_attempt_at

    now = time.monotonic()
    if (
        _last_update_attempt_at is not None
        and now - _last_update_attempt_at < UPDATE_COOLDOWN_SECONDS
    ):
        hours_left = (UPDATE_COOLDOWN_SECONDS - (now - _last_update_attempt_at)) / 3600
        logger.warning(
            "🐤 Обновление yt-dlp пропущено: суточный лимит, следующая попытка "
            "через %.1f ч",
            hours_left,
        )
        return [
            "Реакция: обновление yt-dlp пропущено — суточный лимит уже "
            f"израсходован, следующая попытка через {hours_left:.1f} ч."
        ]

    _last_update_attempt_at = now
    logger.warning("🐤 Канарейка запускает принудительное обновление yt-dlp")
    update = await asyncio.to_thread(
        ensure_latest_yt_dlp, reason="canary_failure", force=True
    )
    version_before = update.version_before or "unknown"
    version_after = update.version_after or version_before

    lines: list[str] = []
    if update.succeeded:
        lines.append(
            f"Реакция: обновил yt-dlp {version_before} → {version_after} "
            f"(канал {update.channel})."
        )
    else:
        logger.error(
            "🐤 Обновление yt-dlp не удалось (канал %s), версия осталась %s",
            update.channel,
            version_after,
        )
        lines.append(
            f"Реакция: обновление yt-dlp не удалось, версия осталась {version_after}."
        )

    # Повторяем проверку в любом случае: даже неудавшийся pip не отменяет
    # вероятности, что первый провал был разовым сетевым сбоем.
    retry = await _check_in_thread()
    if retry.ok:
        logger.info("🐤 Канарейка после обновления прошла: %s", retry.detail)
        lines.append(f"Повторная проверка прошла ✅ ({retry.detail}).")
    else:
        logger.error(
            "🐤 Канарейка после обновления снова упала: этап=%s причина=%s",
            retry.stage,
            retry.detail,
        )
        lines.append(
            f"Повторная проверка снова упала ❌ (этап {retry.stage}) — "
            "обновление не помогло."
        )
    return lines


def _build_report(outcome: CanaryOutcome, reaction: list[str]) -> str:
    """Собирает короткий отчёт для админов."""
    lines = [
        "🐤 Канарейка YouTube упала",
        "",
        f"Проверял: скачивание {canary_video_url()}",
        f"Формат: {outcome.format_id or '—'}",
        f"Этап: {outcome.stage}",
        f"Код: {outcome.error_code or '—'} ({outcome.category or 'UNKNOWN'})",
        f"Причина: {outcome.detail[:400]}",
        "",
        *reaction,
    ]
    return "\n".join(lines)


async def _notify_admins(context: ContextTypes.DEFAULT_TYPE, text: str) -> None:
    """Рассылает отчёт админам.

    Бот берётся из контекста job'ы — штатный путь PTB, который не тянет за собой
    хэндлеры из `utils/telegram_utils.py`.
    """
    if not ADMIN_IDS:
        logger.warning("🐤 ADMIN_IDS пуст: отчёт канарейки остался только в логе")
        return

    for admin_id in ADMIN_IDS:
        try:
            await context.bot.send_message(chat_id=admin_id, text=text)
        except Exception:  # noqa: BLE001
            logger.debug("Не удалось отправить отчёт канарейки админу %s", admin_id)


async def youtube_canary_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Периодическая проверка YouTube: качаем эталон, при провале зовём админов."""
    # Флаг проверяется и здесь, хотя job регистрируется в `main.py` только при
    # включённой канарейке: выключаться она должна из одного места.
    if not CANARY_ENABLED:
        return

    outcome = await _check_in_thread()
    if outcome.ok:
        logger.info(
            "🐤 Канарейка YouTube в порядке: формат %s, %s",
            outcome.format_id,
            outcome.detail,
        )
        return

    logger.error(
        "🐤 Канарейка YouTube упала: этап=%s код=%s категория=%s причина=%s",
        outcome.stage,
        outcome.error_code,
        outcome.category,
        outcome.detail,
    )
    reaction = await _react_to_failure()
    await _notify_admins(context, _build_report(outcome, reaction))
