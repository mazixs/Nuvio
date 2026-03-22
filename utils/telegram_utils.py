"""
Модуль для работы с Telegram API.
"""
import asyncio
import functools
import io
import traceback
import uuid
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor

import telegram
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

from config import DOWNLOAD_WORKERS, BLOCKING_TASK_TIMEOUT, ADMIN_IDS
from utils.logger import setup_logger
from utils.analytics_db import init_db as _init_analytics, track_user, track_event
from utils.youtube_utils import (
    is_valid_youtube_url, get_video_info, get_available_formats,
    download_video, download_audio, download_audio_native, download_subtitles
)
from utils.media_processor import convert_to_mp3_with_compression
from utils.temp_file_manager import create_temp_dir, cleanup_temp_files
import yt_dlp
from messages import (
    WELCOME_MESSAGE, HELP_MESSAGE, PROCESSING_MESSAGE, DOWNLOADING_MESSAGE, DOWNLOADING_AUDIO_MESSAGE, DOWNLOADING_SUBTITLES_MESSAGE,
    INVALID_URL_MESSAGE, ERROR_MESSAGE, TOO_LONG_VIDEO_MESSAGE,
    NO_URL_AFTER_COMMAND, SESSION_EXPIRED, FILE_TOO_LARGE_LINK, FILE_PREPARING, FILE_SENT,
    DOWNLOAD_FORMAT_PROMPT,
    BEST_QUALITY_LABEL, BEST_AUDIO_LABEL, CHOOSE_ANOTHER_FORMAT, NO_SUBTITLES_AVAILABLE,
    NO_TG_VIDEO, NO_FILESIZE, BTN_AUDIO_M4A, BTN_TG_VIDEO, BTN_MORE, TG_SEND_ERROR, BTN_BACK,
    BTN_DOWNLOAD_VIDEO, BTN_AUDIO_ONLY, BTN_SUBTITLES, ERROR_FALLBACK, ERROR_NETWORK, ERROR_FILE_TOO_LARGE_TELEGRAM,
    SUBTITLE_CAPTION, MP3_MIN_LABEL, SPAM_WARNING, LARGE_FILE_DELIVERY_UNAVAILABLE,
    USER_ERROR_WITH_CODE, USER_NETWORK_ERROR_WITH_CODE, USER_FILE_ERROR_WITH_CODE, USER_TELEGRAM_ERROR_WITH_CODE,
)
from utils.tiktok_instagram_utils import (
    is_valid_tiktok_url, is_valid_instagram_url, is_instagram_story_url,
    get_tiktok_info, get_instagram_info, is_instagram_audio_url, handle_instagram_audio_url
)
from utils.video_cache import telegram_cache, CachedVideo
from utils.gokapi_utils import is_gokapi_configured
from utils.cookie_health import check_cookie_health
from datetime import datetime

logger = setup_logger(__name__)

# Инициализация аналитической БД
_init_analytics()

# Ссылка на экземпляр бота для отправки краш-репортов админам
_bot_instance: telegram.Bot | None = None


def set_bot_instance(bot: telegram.Bot) -> None:
    """Устанавливает ссылку на бота для отправки краш-репортов."""
    global _bot_instance
    _bot_instance = bot


async def _notify_admins_crash(
    *,
    error_code: str,
    platform: str,
    stage: str,
    url: str | None,
    exc: Exception,
    session_id: str | None = None,
    cookie_status: str = "not_checked",
    cookie_summary: str = "not_checked",
) -> None:
    """Отправляет файл краш-репорта всем админам из ADMIN_IDS."""
    if not _bot_instance or not ADMIN_IDS:
        return

    report_lines = [
        f"🔴 CRASH REPORT — {error_code}",
        f"Platform: {platform}",
        f"Stage: {stage}",
        f"URL: {url or 'N/A'}",
        f"Session: {session_id or 'N/A'}",
        f"Cookie status: {cookie_status}",
        f"Cookie summary: {cookie_summary}",
        "",
        f"Exception: {type(exc).__name__}: {exc}",
        "",
        "Traceback:",
        traceback.format_exc(),
    ]
    report_text = "\n".join(report_lines)

    for admin_id in ADMIN_IDS:
        try:
            await _bot_instance.send_document(
                chat_id=admin_id,
                document=io.BytesIO(report_text.encode("utf-8")),
                filename=f"crash_{error_code}.txt",
                caption=f"🔴 {error_code} | {platform} | {stage}",
            )
        except Exception:  # noqa: BLE001
            logger.debug("Не удалось отправить краш-репорт админу %s", admin_id)


# Глобальный executor для тяжёлых задач
executor = ThreadPoolExecutor(max_workers=DOWNLOAD_WORKERS)
_SPAM_WINDOW_SECONDS = 5
_SPAM_REQUEST_LIMIT = 4
_SPAM_TIMEOUT_SECONDS = 10
_MAX_ACTIVE_SESSIONS = 5
_ANTISPAM_STATE_KEYS = ("recent_requests", "spam_blocked_until")
_SESSION_STORE_KEY = "sessions"
_DIRECT_VIDEO_CACHE_KEY = "direct_video"


def _track_tg_user(update: Update) -> None:
    """Регистрирует / обновляет пользователя в аналитике."""
    user = update.effective_user
    if not user:
        return
    try:
        track_user(
            user_id=user.id,
            username=user.username,
            first_name=user.first_name,
            last_name=user.last_name,
            language_code=user.language_code,
        )
    except Exception:  # noqa: BLE001
        logger.debug("analytics: не удалось записать пользователя %s", user.id)


async def run_blocking(func, *args, description: str = "blocking task"):
    """Запускает sync-функцию в executor с таймаутом."""
    loop = asyncio.get_running_loop()
    try:
        return await asyncio.wait_for(
            loop.run_in_executor(executor, func, *args),
            BLOCKING_TASK_TIMEOUT,
        )
    except asyncio.TimeoutError as exc:
        logger.error(
            f"{description} превысил таймаут {BLOCKING_TASK_TIMEOUT}с", exc_info=True
        )
        raise exc

# Простая защита от спама: 4 запросa подряд без паузы -> предупреждение и таймаут 10с
def _check_spam(user_id: int, context: ContextTypes.DEFAULT_TYPE, now: float) -> bool:
    blocked_until = context.user_data.get('spam_blocked_until', 0.0)
    if blocked_until and now < blocked_until:
        return True

    if blocked_until and now >= blocked_until:
        context.user_data.pop('spam_blocked_until', None)

    timestamps: list[float] = context.user_data.get('recent_requests', [])
    timestamps = [t for t in timestamps if now - t < _SPAM_WINDOW_SECONDS]
    timestamps.append(now)
    context.user_data['recent_requests'] = timestamps

    if len(timestamps) >= _SPAM_REQUEST_LIMIT:
        context.user_data['spam_blocked_until'] = now + _SPAM_TIMEOUT_SECONDS
        return True

    return False


def _get_session_store(context: ContextTypes.DEFAULT_TYPE) -> dict[str, dict]:
    """Возвращает хранилище активных сессий пользователя."""
    store = context.user_data.get(_SESSION_STORE_KEY)
    if not isinstance(store, dict):
        store = {}
        context.user_data[_SESSION_STORE_KEY] = store
    return store


def _store_session(
    context: ContextTypes.DEFAULT_TYPE,
    *,
    url: str,
    video_info: dict,
    session_id: str,
    platform: str,
    formats: dict,
) -> str:
    """Сохраняет новую сессию и возвращает короткий токен для callback_data."""
    store = _get_session_store(context)
    session_token = uuid.uuid4().hex[:8]
    while session_token in store:
        session_token = uuid.uuid4().hex[:8]

    store[session_token] = {
        "url": url,
        "video_info": video_info,
        "session_id": session_id,
        "platform": platform,
        "formats": formats,
        "created_at": datetime.now().timestamp(),
    }

    while len(store) > _MAX_ACTIVE_SESSIONS:
        oldest_token = min(
            store,
            key=lambda token: float(store[token].get("created_at", 0.0)),
        )
        if oldest_token == session_token:
            break
        old_session = store.pop(oldest_token, None)
        old_session_id = old_session.get("session_id") if old_session else None
        if old_session_id:
            cleanup_temp_files(old_session_id)
            logger.info("Старая сессия %s удалена из-за лимита активных меню", oldest_token)

    return session_token


def _get_session(context: ContextTypes.DEFAULT_TYPE, session_token: str) -> dict | None:
    """Возвращает данные сессии по токену."""
    return _get_session_store(context).get(session_token)


def _make_callback_data(
    session_token: str,
    scope: str,
    action: str,
    extra: str | None = None,
) -> str:
    """Формирует callback_data с привязкой к конкретной сессии."""
    parts = ["s", session_token, scope, action]
    if extra is not None:
        parts.append(extra)
    return "|".join(parts)


def _build_back_markup(session_token: str) -> InlineKeyboardMarkup:
    """Клавиатура с возвратом в меню текущей сессии."""
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton(BTN_BACK, callback_data=_make_callback_data(session_token, "main", "back"))]]
    )


def _build_youtube_prompt(video_info: dict) -> str:
    """Текст карточки YouTube-видео с безопасным Markdown."""
    title = escape_markdown(str(video_info.get('title') or 'Video'))
    duration = format_duration(int(video_info.get('duration') or 0))
    return DOWNLOAD_FORMAT_PROMPT.format(title=title, duration=duration)


def _build_main_menu(
    platform: str,
    video_info: dict,
    session_token: str,
) -> tuple[str, InlineKeyboardMarkup]:
    """Возвращает текст и клавиатуру главного меню для платформы."""
    title = escape_markdown(str(video_info.get('title') or 'Video'))
    uploader = escape_markdown(str(video_info.get('uploader') or 'N/A'))
    duration = format_duration(int(video_info.get('duration') or 0))

    if platform == 'tiktok':
        keyboard = [
            [InlineKeyboardButton(BTN_DOWNLOAD_VIDEO, callback_data=_make_callback_data(session_token, "main", "tiktok_download"))],
            [InlineKeyboardButton(BTN_AUDIO_ONLY, callback_data=_make_callback_data(session_token, "main", "tiktok_audio"))],
            [InlineKeyboardButton(BTN_BACK, callback_data=_make_callback_data(session_token, "main", "back"))],
        ]
        text = f"*{title}*\nАвтор: {uploader}\nДлительность: {duration}"
        return text, InlineKeyboardMarkup(keyboard)

    if platform == 'instagram':
        keyboard = [
            [InlineKeyboardButton(BTN_DOWNLOAD_VIDEO, callback_data=_make_callback_data(session_token, "main", "instagram_download"))],
            [InlineKeyboardButton(BTN_AUDIO_ONLY, callback_data=_make_callback_data(session_token, "main", "instagram_audio"))],
            [InlineKeyboardButton(BTN_BACK, callback_data=_make_callback_data(session_token, "main", "back"))],
        ]
        text = f"*{title}*\nАвтор: {uploader}\nДлительность: {duration}"
        return text, InlineKeyboardMarkup(keyboard)

    keyboard = [
        [InlineKeyboardButton(BTN_TG_VIDEO, callback_data=_make_callback_data(session_token, "main", "tg_video"))],
        [InlineKeyboardButton(BTN_AUDIO_M4A, callback_data=_make_callback_data(session_token, "main", "audio_m4a"))],
        [InlineKeyboardButton(BTN_MORE, callback_data=_make_callback_data(session_token, "main", "more"))],
    ]
    text = _build_youtube_prompt(video_info)
    return text, InlineKeyboardMarkup(keyboard)


def _build_youtube_more_menu(formats: dict, session_token: str) -> InlineKeyboardMarkup:
    """Расширенное меню форматов для YouTube."""
    keyboard = []
    added_button_labels = set()
    combined_count = 0

    for fmt in formats.get('combined', []):
        label = f"📹+🔊 {fmt.get('height', 'N/A')}p - {fmt.get('ext', 'mp4').upper()}"
        if label in added_button_labels or combined_count >= 3:
            continue
        keyboard.append([
            InlineKeyboardButton(
                label,
                callback_data=_make_callback_data(session_token, "format", "combined", fmt['format_id']),
            )
        ])
        added_button_labels.add(label)
        combined_count += 1

    video_only_count = 0
    for fmt in formats.get('video_only', []):
        label = f"📹 {fmt.get('height', 'N/A')}p - {fmt.get('ext', 'mp4').upper()} (без звука)"
        if label in added_button_labels or video_only_count >= 3:
            continue
        keyboard.append([
            InlineKeyboardButton(
                label,
                callback_data=_make_callback_data(session_token, "format", "video_only", fmt['format_id']),
            )
        ])
        added_button_labels.add(label)
        video_only_count += 1

    audio_only = formats.get('audio_only', [])
    audio_only_count = 0
    for fmt in audio_only:
        label = f"🔊 Только аудио - {fmt.get('ext', 'm4a').upper()}"
        if label in added_button_labels or audio_only_count >= 2:
            continue
        keyboard.append([
            InlineKeyboardButton(
                label,
                callback_data=_make_callback_data(session_token, "format", "audio_only", fmt['format_id']),
            )
        ])
        added_button_labels.add(label)
        audio_only_count += 1

    if audio_only:
        min_m4a = min(
            [f for f in audio_only if f.get('ext') == 'm4a'],
            key=lambda x: x.get('filesize', float('inf')),
            default=None,
        )
        if min_m4a:
            keyboard.append([
                InlineKeyboardButton(
                    MP3_MIN_LABEL,
                    callback_data=_make_callback_data(session_token, "format", "mp3_min", min_m4a['format_id']),
                )
            ])

    best_label = BEST_QUALITY_LABEL if is_gokapi_configured() else BEST_QUALITY_LABEL + " (может не влезть в ТГ)"
    keyboard.append([
        InlineKeyboardButton(
            best_label,
            callback_data=_make_callback_data(session_token, "format", "best", "best"),
        )
    ])
    keyboard.append([
        InlineKeyboardButton(
            BEST_AUDIO_LABEL,
            callback_data=_make_callback_data(session_token, "format", "audio_best", "bestaudio"),
        )
    ])
    keyboard.append([
        InlineKeyboardButton(
            BTN_SUBTITLES,
            callback_data=_make_callback_data(session_token, "main", "subtitles"),
        )
    ])
    keyboard.append([
        InlineKeyboardButton(
            BTN_BACK,
            callback_data=_make_callback_data(session_token, "main", "back"),
        )
    ])
    return InlineKeyboardMarkup(keyboard)


def _should_rate_limit_callback(callback_data: str | None) -> bool:
    """Ограничивает только дорогие callback-действия, а не навигацию по меню."""
    if not callback_data:
        return False

    parts = callback_data.split('|')
    if len(parts) < 4 or parts[0] != "s":
        return False

    _, _, scope, action, *_ = parts
    if scope == "format":
        return True
    if scope == "main" and action not in {"more", "back"}:
        return True
    return False

async def safe_edit_message_text(query: telegram.CallbackQuery, text: str, **kwargs) -> bool:
    """Безопасно вызывает edit_message_text, игнорируя ошибку 'Message is not modified'."""
    try:
        await query.edit_message_text(text, **kwargs)
        return True
    except telegram.error.BadRequest as e:
        if "message is not modified" in str(e).lower():
            logger.debug("edit_message_text пропущен: текст и разметка без изменений")
            return False
        raise

def _classify_platform_error(error_msg: str, platform: str) -> str | None:
    """Классифицирует ошибку платформы и возвращает user-friendly сообщение или None."""
    msg_lower = error_msg.lower()
    if platform == "tiktok":
        if "ip address is blocked" in msg_lower or "unable to extract webpage video data" in msg_lower:
            return (
                "🚫 **TikTok недоступен**\n\n"
                "К сожалению, TikTok ограничил доступ к своему контенту из вашего региона.\n\n"
                "**Это происходит из-за:**\n"
                "• Региональных ограничений TikTok\n"
                "• Блокировки IP-адресов\n"
                "• Изменений в API TikTok\n\n"
                "**Альтернативы:**\n"
                "• Попробуйте YouTube или Instagram видео\n"
                "• Используйте VPN для смены региона\n"
                "• Скачайте видео вручную и отправьте боту файлом"
            )
    if platform == "instagram":
        if any(kw in msg_lower for kw in ['rate-limit', 'login required', 'not available', 'sign in', 'ограничил доступ']):
            return (
                "🚫 **Instagram ограничения**\n\n"
                "Instagram ограничил доступ к этому контенту.\n\n"
                "**Возможные причины:**\n"
                "• Превышен лимит запросов\n"
                "• Контент требует авторизации\n"
                "• Региональные ограничения\n"
                "• Приватный аккаунт\n\n"
                "**Что можно попробовать:**\n"
                "• Подождать 10-15 минут\n"
                "• Использовать другую ссылку\n"
                "• Проверить, что контент публичный"
            )
    if "private video" in msg_lower or "private" in msg_lower:
        return (
            "🔒 **Приватное видео**\n\n"
            "Это видео недоступно для скачивания, так как оно приватное или удалено."
        )
    return None


def _classify_large_file_delivery_error(error_msg: str) -> str | None:
    """Возвращает понятное сообщение, если недоступна выдача больших файлов."""
    msg_lower = error_msg.lower()
    if any(
        signature in msg_lower
        for signature in (
            "сервер загрузки больших файлов не настроен",
            "сервер загрузки недоступен",
            "ошибка gokapi",
            "gokapi",
        )
    ):
        return LARGE_FILE_DELIVERY_UNAVAILABLE
    return None


def _classify_youtube_error(error_msg: str) -> str | None:
    """Классифицирует частые YouTube/yt-dlp ошибки для понятного ответа пользователю."""
    error_code = _youtube_error_code(error_msg)

    if error_code == "FORMAT_UNAVAILABLE":
        return CHOOSE_ANOTHER_FORMAT.format(
            error="Выбранный формат недоступен для этого видео."
        )

    if error_code == "ACCESS_RESTRICTED":
        return (
            "🚫 **Ограниченный доступ к YouTube видео**\n\n"
            "YouTube отклонил доступ к этому ролику (ограничения/авторизация).\n"
            "Попробуйте другую ссылку или повторите попытку позже."
        )

    if error_code == "NETWORK_TIMEOUT":
        return ERROR_NETWORK

    if error_code == "EXTRACTOR_RUNTIME":
        return (
            "⚠️ **Проблема совместимости YouTube extractor**\n\n"
            "YouTube изменил схему отдачи видео или потребовался JS runtime. "
            "Сервис уже использует локальные fallback-сценарии, но этот ролик сейчас не удалось обработать.\n"
            "Попробуйте повторить запрос позже."
        )

    if error_code == "FFMPEG_MISSING":
        return "❌ FFmpeg не найден в системе. Установите FFmpeg и добавьте его в PATH."

    return None


def _youtube_error_code(error_msg: str) -> str:
    """Возвращает короткий код YouTube/yt-dlp ошибки для структурированного логирования."""
    msg_lower = error_msg.lower()

    if "requested format is not available" in msg_lower:
        return "FORMAT_UNAVAILABLE"

    if any(
        signature in msg_lower
        for signature in (
            "http error 403",
            "forbidden",
            "sign in to confirm your age",
            "login required",
            "this video is unavailable",
            "private video",
        )
    ):
        return "ACCESS_RESTRICTED"

    if any(
        signature in msg_lower
        for signature in (
            "read timed out",
            "connection timed out",
            "timed out",
            "connection reset by peer",
            "unexpected_eof_while_reading",
            "eof occurred in violation of protocol",
            "network is unreachable",
        )
    ):
        return "NETWORK_TIMEOUT"

    if "ffmpeg" in msg_lower and any(
        signature in msg_lower
        for signature in (
            "not found",
            "is not installed",
            "ffprobe",
        )
    ):
        return "FFMPEG_MISSING"

    if any(
        signature in msg_lower
        for signature in (
            "requires a javascript runtime",
            "nsig extraction failed",
            "signature extraction failed",
            "unable to extract initial player response",
            "remote components",
        )
    ):
        return "EXTRACTOR_RUNTIME"

    return "UNKNOWN"


def _make_error_code(platform: str, category: str) -> str:
    platform_prefix = {
        "youtube": "YT",
        "tiktok": "TT",
        "instagram": "IG",
        "telegram": "TG",
        "file": "FILE",
        "bot": "BOT",
    }.get(platform, "BOT")
    normalized_category = category.upper()[:8]
    return f"{platform_prefix}-{normalized_category}-{uuid.uuid4().hex[:6].upper()}"


def _classify_internal_error_category(platform: str, error_msg: str) -> str:
    if _classify_large_file_delivery_error(error_msg):
        return "LARGE"

    msg_lower = error_msg.lower()
    if platform == "youtube":
        return _youtube_error_code(error_msg)

    if any(signature in msg_lower for signature in ("timed out", "network", "connection reset", "ssl", "eof")):
        return "NETWORK"
    if any(signature in msg_lower for signature in ("rate-limit", "too many requests")):
        return "RATE_LIMIT"
    if any(signature in msg_lower for signature in ("login required", "sign in", "private", "forbidden", "blocked", "unavailable")):
        return "ACCESS"
    if "story" in msg_lower and "не поддерживается" in msg_lower:
        return "STORY_UNSUPPORTED"
    return "UNKNOWN"


def _build_public_error_message(platform: str, error_code: str, error_msg: str) -> str:
    if large_file_message := _classify_large_file_delivery_error(error_msg):
        return large_file_message

    internal_category = _classify_internal_error_category(platform, error_msg)
    if platform == "youtube" and internal_category == "FORMAT_UNAVAILABLE":
        return CHOOSE_ANOTHER_FORMAT.format(error="Выбранный формат сейчас недоступен.")
    if internal_category in {"NETWORK", "NETWORK_TIMEOUT"}:
        return USER_NETWORK_ERROR_WITH_CODE.format(error_code=error_code)
    if internal_category == "STORY_UNSUPPORTED":
        return (
            "📛 Скачивание Instagram Stories не поддерживается.\n\n"
            "Stories — это временный контент (24 часа), и Instagram "
            "ограничивает их загрузку через API.\n\n"
            "Попробуйте скачать обычный пост, Reel или видео из IGTV."
        )
    return USER_ERROR_WITH_CODE.format(error_code=error_code)


async def _log_platform_failure(
    *,
    platform: str,
    stage: str,
    url: str | None,
    error_code: str,
    exc: Exception,
    session_id: str | None = None,
) -> None:
    cookie_status = "not_checked"
    cookie_summary = "not_checked"
    if platform in {"youtube", "instagram", "tiktok"}:
        try:
            health = await asyncio.to_thread(check_cookie_health, platform)
            cookie_status = health.status
            cookie_summary = health.summary
        except Exception as health_exc:  # noqa: BLE001
            cookie_status = "health_failed"
            cookie_summary = str(health_exc)

    logger.error(
        "USER_FLOW_FAIL code=%s platform=%s stage=%s session_id=%s url=%s cookie_status=%s cookie_summary=%s error=%s",
        error_code,
        platform,
        stage,
        session_id,
        url,
        cookie_status,
        cookie_summary,
        exc,
        exc_info=True,
    )

    await _notify_admins_crash(
        error_code=error_code,
        platform=platform,
        stage=stage,
        url=url,
        exc=exc,
        session_id=session_id,
        cookie_status=cookie_status,
        cookie_summary=cookie_summary,
    )


def _schedule_platform_failure_log(
    *,
    platform: str,
    stage: str,
    url: str | None,
    error_code: str,
    exc: Exception,
    session_id: str | None = None,
) -> None:
    async def _runner() -> None:
        try:
            await _log_platform_failure(
                platform=platform,
                stage=stage,
                url=url,
                error_code=error_code,
                exc=exc,
                session_id=session_id,
            )
        except Exception:  # noqa: BLE001
            logger.exception("Failed to emit structured platform error log for %s", error_code)

    asyncio.create_task(_runner())


def _cache_format_id_for_main_action(platform: str, action: str) -> str | None:
    """Возвращает cache-key для прямых пользовательских действий."""
    if platform in {"tiktok", "instagram"} and action.endswith("_download"):
        return _DIRECT_VIDEO_CACHE_KEY
    if platform == "youtube" and action == "tg_video":
        return "tg_video"
    return None


def _cache_format_id_for_format_selection(content_type: str, format_id: str) -> str | None:
    """Возвращает cache-key для выбранного формата."""
    if content_type == "combined":
        return f"combined:{format_id}"
    if content_type == "video_only":
        return f"video_only:{format_id}"
    if content_type == "best":
        return "best"
    return None


async def _try_send_cached(
    update: Update,
    url: str,
    user_id: int,
    cache_format_id: str,
    platform: str = "video",
) -> bool:
    """Проверяет кэш и отправляет видео из кэша. Возвращает True если отправлено."""
    if not update.message:
        return False

    cached = telegram_cache.get(url, format_id=cache_format_id)
    if not cached:
        logger.info("Cache MISS для %s %s (key=%s), скачиваем...", platform, url, cache_format_id)
        return False
    logger.info("Cache HIT для user %s: %s (key=%s)", user_id, url, cache_format_id)
    try:
        if "audio" in cache_format_id.lower():
            await update.message.reply_audio(audio=cached.file_id, caption=None)
        else:
            await update.message.reply_video(
                video=cached.file_id,
                caption=None,
                supports_streaming=True,
            )
        logger.info("%s файл доставлен из кэша за 0 сек (user %s)", platform, user_id)
        return True
    except telegram.error.BadRequest as e:
        logger.warning("file_id устарел для %s (key=%s): %s", url, cache_format_id, e)
        telegram_cache.delete_by_file_id(cached.file_id)
        return False


async def _cleanup_user_session(
    user_id: int,
    context: ContextTypes.DEFAULT_TYPE,
    session_token: str | None = None,
) -> None:
    """Очищает конкретную сессию пользователя, не затрагивая остальные меню."""
    if session_token:
        session = _get_session_store(context).pop(session_token, None)
        session_id = session.get('session_id') if session else None
        if session_id:
            cleanup_temp_files(session_id)
            logger.info(
                "Временные файлы для сессии %s пользователя %s очищены.",
                session_id,
                user_id,
            )
        logger.info("Сессия %s пользователя %s очищена.", session_token, user_id)
        return

    session_id = context.user_data.get('session_id')
    if session_id:
        cleanup_temp_files(session_id)
        logger.info(f"Временные файлы для legacy-сессии {session_id} пользователя {user_id} очищены.")
    preserved_state = {
        key: context.user_data[key]
        for key in (*_ANTISPAM_STATE_KEYS, _SESSION_STORE_KEY)
        if key in context.user_data
    }
    context.user_data.clear()
    context.user_data.update(preserved_state)
    logger.info(f"Legacy-сессия (user_data) для пользователя {user_id} очищена.")

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Обрабатывает команду /start.
    
    Args:
        update (Update): Объект обновления Telegram.
        context (ContextTypes.DEFAULT_TYPE): Контекст.
    """
    logger.info(f"Получена команда /start от пользователя {update.effective_user.id}")
    _track_tg_user(update)
    track_event(update.effective_user.id, "start")
    from utils.cookie_manager import build_admin_entry_markup, is_admin

    user_id = update.effective_user.id if update.effective_user else None
    if is_admin(user_id):
        await update.message.reply_text(
            f"{WELCOME_MESSAGE}\n\n🔐 Доступна админ-панель: /admin",
            reply_markup=build_admin_entry_markup(),
        )
        return

    await update.message.reply_text(WELCOME_MESSAGE)

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Обрабатывает команду /help.
    
    Args:
        update (Update): Объект обновления Telegram.
        context (ContextTypes.DEFAULT_TYPE): Контекст.
    """
    logger.info(f"Получена команда /help от пользователя {update.effective_user.id}")
    from utils.cookie_manager import is_admin

    user_id = update.effective_user.id if update.effective_user else None
    help_message = HELP_MESSAGE
    if is_admin(user_id):
        help_message += "\n\n🔐 *Для администратора:* используйте /admin для управления cookies."
    await update.message.reply_text(help_message, parse_mode='Markdown')

async def _get_url_from_context(update: Update, context: ContextTypes.DEFAULT_TYPE) -> str | None:
    """Извлекает URL из команды /download или текста сообщения."""
    if update.message and update.message.text.startswith('/download'):
        if not context.args:
            await update.message.reply_text(NO_URL_AFTER_COMMAND)
            return None
        return context.args[0]
    elif update.message:
        return update.message.text
    return None

async def download_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Обрабатывает команду /download.
    
    Args:
        update (Update): Объект обновления Telegram.
        context (ContextTypes.DEFAULT_TYPE): Контекст.
    """
    user_id = update.effective_user.id
    logger.info(f"Получена команда /download от пользователя {user_id}")
    url = await _get_url_from_context(update, context)
    if not url:
        return
    
    await process_url(update, context, url)

async def process_url(update: Update, context: ContextTypes.DEFAULT_TYPE, url: str | None = None) -> None:
    """
    Обрабатывает полученный URL от пользователя.
    
    Args:
        update (Update): Объект обновления Telegram.
        context (ContextTypes.DEFAULT_TYPE): Контекст.
    """
    user_id = update.effective_user.id
    if update.message:
        now = asyncio.get_running_loop().time()
        if _check_spam(user_id, context, now):
            await update.message.reply_text(SPAM_WARNING)
            return
    if not url:
        url_from_message = await _get_url_from_context(update, context)
        if not url_from_message:
            return
        url = url_from_message
    logger.info(f"Обработка URL '{url}' от пользователя {user_id}")
    _track_tg_user(update)

    # Определяем платформу для аналитики
    _analytics_platform = None
    if is_valid_youtube_url(url):
        _analytics_platform = "youtube"
    elif is_valid_tiktok_url(url):
        _analytics_platform = "tiktok"
    elif is_valid_instagram_url(url):
        _analytics_platform = "instagram"
    if _analytics_platform:
        track_event(user_id, "download", platform=_analytics_platform, url=url)

    # Проверка YouTube
    if is_valid_youtube_url(url):
        processing_message = await update.message.reply_text(PROCESSING_MESSAGE)
        session_id: str | None = None
        try:
            video_info = await run_blocking(
                get_video_info, url, description="get_video_info"
            )
            session_id = str(user_id) + "_" + str(uuid.uuid4())
            create_temp_dir(session_id)
            formats = get_available_formats(video_info)
            session_token = _store_session(
                context,
                url=url,
                video_info=video_info,
                session_id=session_id,
                platform='youtube',
                formats=formats,
            )
            text, reply_markup = _build_main_menu('youtube', video_info, session_token)
            await processing_message.edit_text(
                text,
                reply_markup=reply_markup,
                parse_mode='Markdown'
            )
        except (yt_dlp.utils.DownloadError, yt_dlp.cookies.CookieLoadError) as e_cookie:
            error_code = _make_error_code("youtube", _classify_internal_error_category("youtube", str(e_cookie)))
            _schedule_platform_failure_log(
                platform="youtube",
                stage="process_url",
                url=url,
                error_code=error_code,
                exc=e_cookie,
                session_id=session_id,
            )
            await processing_message.edit_text(_build_public_error_message("youtube", error_code, str(e_cookie)))
            if session_id:
                cleanup_temp_files(session_id)
        except (ValueError, KeyError) as e:
            if "слишком длинное" in str(e):
                await processing_message.edit_text(TOO_LONG_VIDEO_MESSAGE)
            else:
                error_code = _make_error_code("youtube", "DATA")
                _schedule_platform_failure_log(
                    platform="youtube",
                    stage="process_url_data",
                    url=url,
                    error_code=error_code,
                    exc=e,
                    session_id=session_id,
                )
                await processing_message.edit_text(USER_ERROR_WITH_CODE.format(error_code=error_code))
            if session_id:
                cleanup_temp_files(session_id)
        except (asyncio.TimeoutError, asyncio.CancelledError) as e:
            error_code = _make_error_code("youtube", "TIMEOUT")
            _schedule_platform_failure_log(
                platform="youtube",
                stage="process_url_timeout",
                url=url,
                error_code=error_code,
                exc=e,
                session_id=session_id,
            )
            await processing_message.edit_text(USER_NETWORK_ERROR_WITH_CODE.format(error_code=error_code))
            if session_id:
                cleanup_temp_files(session_id)
        except Exception as e:
            error_code = _make_error_code("youtube", "UNKNOWN")
            _schedule_platform_failure_log(
                platform="youtube",
                stage="process_url_unexpected",
                url=url,
                error_code=error_code,
                exc=e,
                session_id=session_id,
            )
            await processing_message.edit_text(USER_ERROR_WITH_CODE.format(error_code=error_code))
            if session_id:
                cleanup_temp_files(session_id)
        return
    # Проверка TikTok
    if is_valid_tiktok_url(url):
        processing_message = await update.message.reply_text(PROCESSING_MESSAGE)
        session_id = None
        try:
            video_info = await run_blocking(
                get_tiktok_info, url, description="get_tiktok_info"
            )
            session_id = str(user_id) + "_" + str(uuid.uuid4())
            create_temp_dir(session_id)
            from utils.tiktok_instagram_utils import get_available_formats_tiktok
            formats = get_available_formats_tiktok(video_info)
            session_token = _store_session(
                context,
                url=url,
                video_info=video_info,
                session_id=session_id,
                platform='tiktok',
                formats=formats,
            )
            text, reply_markup = _build_main_menu('tiktok', video_info, session_token)
            await processing_message.edit_text(
                text,
                reply_markup=reply_markup,
                parse_mode='Markdown'
            )
        except Exception as e:
            error_code = _make_error_code("tiktok", _classify_internal_error_category("tiktok", str(e)))
            _schedule_platform_failure_log(
                platform="tiktok",
                stage="process_url",
                url=url,
                error_code=error_code,
                exc=e,
                session_id=session_id,
            )
            await processing_message.edit_text(_build_public_error_message("tiktok", error_code, str(e)))
            if session_id:
                cleanup_temp_files(session_id)
        return
    # Проверка Instagram Stories (не поддерживается)
    if is_instagram_story_url(url):
        await update.message.reply_text(
            "📛 Скачивание Instagram Stories не поддерживается.\n\n"
            "Stories — это временный контент (24 часа), и Instagram "
            "ограничивает их загрузку через API.\n\n"
            "Попробуйте скачать обычный пост, Reel или видео из IGTV."
        )
        return
    # Проверка Instagram аудио ссылок
    if is_instagram_audio_url(url):
        message_text = handle_instagram_audio_url(url)
        await update.message.reply_text(
            message_text,
            parse_mode='Markdown',
            disable_web_page_preview=True
        )
        return
    # Проверка Instagram
    if is_valid_instagram_url(url):
        processing_message = await update.message.reply_text(PROCESSING_MESSAGE)
        session_id = None
        try:
            video_info = await run_blocking(
                get_instagram_info, url, description="get_instagram_info"
            )
            session_id = str(user_id) + "_" + str(uuid.uuid4())
            create_temp_dir(session_id)
            session_token = _store_session(
                context,
                url=url,
                video_info=video_info,
                session_id=session_id,
                platform='instagram',
                formats={},
            )
            text, reply_markup = _build_main_menu('instagram', video_info, session_token)
            await processing_message.edit_text(
                text,
                reply_markup=reply_markup,
                parse_mode='Markdown'
            )
        except Exception as e:
            error_code = _make_error_code("instagram", _classify_internal_error_category("instagram", str(e)))
            _schedule_platform_failure_log(
                platform="instagram",
                stage="process_url",
                url=url,
                error_code=error_code,
                exc=e,
                session_id=session_id,
            )
            await processing_message.edit_text(_build_public_error_message("instagram", error_code, str(e)))
            if session_id:
                cleanup_temp_files(session_id)
        return
    # Если не подходит ни один из вариантов
    await update.message.reply_text(INVALID_URL_MESSAGE)

async def _button_callback_legacy_unsafe(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    user_id = update.effective_user.id
    now = asyncio.get_running_loop().time()
    if _check_spam(user_id, context, now):
        # Отвечаем на колбэк и предупреждаем
        await query.answer(text=SPAM_WARNING, show_alert=False)
        return
    logger.info(f"Получен колбэк от пользователя {user_id}: {query.data}")
    if not all(key in context.user_data for key in ('url', 'session_id', 'video_info')):
        await query.edit_message_text(SESSION_EXPIRED)
        return
    try:
        data = query.data.split('|')
        match data:
            case ["main", action]:
                formats = context.user_data.get('formats', {})
                url = context.user_data['url']
                session_id = context.user_data['session_id']
                platform = context.user_data.get('platform')
                # Проверяем наличие необходимых данных сессии
                if not url or not session_id:
                    await query.edit_message_text(SESSION_EXPIRED)
                    await _cleanup_user_session(user_id, context)
                    return
                
                match action:
                    case "tiktok_download":
                        await safe_edit_message_text(query, DOWNLOADING_MESSAGE)
                        from utils.tiktok_instagram_utils import download_tiktok_video
                        try:
                            cached_info = context.user_data.get('video_info')
                            file_path = await run_blocking(
                                download_tiktok_video,
                                url,
                                session_id,
                                None,
                                False,
                                cached_info,
                                description="download_tiktok_video",
                            )
                            if file_path:
                                if isinstance(file_path, str) and file_path.startswith("http"):
                                    await query.edit_message_text(FILE_TOO_LARGE_LINK.format(file_path=file_path))
                                    return
                                await send_file(query, file_path, session_id, context)
                            else:
                                await query.edit_message_text(ERROR_MESSAGE)
                                await _cleanup_user_session(user_id, context)
                        except Exception as e:
                            logger.error(f"Ошибка при скачивании TikTok видео: {e}")
                            error_code = _make_error_code("tiktok", _classify_internal_error_category("tiktok", str(e)))
                            _schedule_platform_failure_log(
                                platform="tiktok",
                                stage="legacy_download_video",
                                url=url,
                                error_code=error_code,
                                exc=e,
                                session_id=session_id,
                            )
                            await query.edit_message_text(_build_public_error_message("tiktok", error_code, str(e)))
                            await _cleanup_user_session(user_id, context)
                        return

                    case "tiktok_audio":
                        await safe_edit_message_text(query, DOWNLOADING_AUDIO_MESSAGE)
                        from utils.tiktok_instagram_utils import download_tiktok_audio
                        try:
                            cached_info = context.user_data.get('video_info')
                            file_path = await run_blocking(
                                download_tiktok_audio,
                                url,
                                session_id,
                                None,
                                False,
                                cached_info,
                                description="download_tiktok_audio",
                            )
                            if file_path:
                                if isinstance(file_path, str) and file_path.startswith("http"):
                                    await query.edit_message_text(FILE_TOO_LARGE_LINK.format(file_path=file_path))
                                    return
                                await send_file(query, file_path, session_id, context)
                            else:
                                await query.edit_message_text(ERROR_MESSAGE)
                                await _cleanup_user_session(user_id, context)
                        except Exception as e:
                            logger.error(f"Ошибка при скачивании TikTok аудио: {e}")
                            error_code = _make_error_code("tiktok", _classify_internal_error_category("tiktok", str(e)))
                            _schedule_platform_failure_log(
                                platform="tiktok",
                                stage="legacy_download_audio",
                                url=url,
                                error_code=error_code,
                                exc=e,
                                session_id=session_id,
                            )
                            await query.edit_message_text(_build_public_error_message("tiktok", error_code, str(e)))
                            await _cleanup_user_session(user_id, context)
                        return
                    case "instagram_download":
                        await query.edit_message_text(DOWNLOADING_MESSAGE)
                        from utils.tiktok_instagram_utils import download_instagram_video
                        try:
                            file_path = await run_blocking(
                                download_instagram_video,
                                url,
                                session_id,
                                description="download_instagram_video",
                            )
                            if file_path:
                                if isinstance(file_path, str) and file_path.startswith("http"):
                                    await query.edit_message_text(FILE_TOO_LARGE_LINK.format(file_path=file_path))
                                    return
                                await send_file(query, file_path, session_id, context)
                            else:
                                await query.edit_message_text(ERROR_MESSAGE)
                                await _cleanup_user_session(user_id, context)
                        except Exception as e:
                            logger.error(f"Ошибка при скачивании Instagram видео: {e}")
                            error_code = _make_error_code("instagram", _classify_internal_error_category("instagram", str(e)))
                            _schedule_platform_failure_log(
                                platform="instagram",
                                stage="legacy_download_video",
                                url=url,
                                error_code=error_code,
                                exc=e,
                                session_id=session_id,
                            )
                            await query.edit_message_text(_build_public_error_message("instagram", error_code, str(e)))
                            await _cleanup_user_session(user_id, context)
                        return
                    case "instagram_audio":
                        await query.edit_message_text(DOWNLOADING_AUDIO_MESSAGE)
                        from utils.tiktok_instagram_utils import download_instagram_audio
                        try:
                            file_path = await run_blocking(
                                download_instagram_audio,
                                url,
                                session_id,
                                description="download_instagram_audio",
                            )
                            if file_path:
                                if isinstance(file_path, str) and file_path.startswith("http"):
                                    await query.edit_message_text(FILE_TOO_LARGE_LINK.format(file_path=file_path))
                                    return
                                await send_file(query, file_path, session_id, context)
                            else:
                                await query.edit_message_text(ERROR_MESSAGE)
                                await _cleanup_user_session(user_id, context)
                        except Exception as e:
                            logger.error(f"Ошибка при скачивании Instagram аудио: {e}")
                            error_code = _make_error_code("instagram", _classify_internal_error_category("instagram", str(e)))
                            _schedule_platform_failure_log(
                                platform="instagram",
                                stage="legacy_download_audio",
                                url=url,
                                error_code=error_code,
                                exc=e,
                                session_id=session_id,
                            )
                            await query.edit_message_text(_build_public_error_message("instagram", error_code, str(e)))
                            await _cleanup_user_session(user_id, context)
                        return
                    case "best":
                        await query.edit_message_text(DOWNLOADING_MESSAGE)
                        file_path = await download_content(url, "bestvideo+bestaudio", session_id, "best")
                        if file_path:
                            if isinstance(file_path, str) and file_path.startswith("http"):
                                await query.edit_message_text(FILE_TOO_LARGE_LINK.format(file_path=file_path))
                                return
                            await send_file(query, file_path, session_id, context)
                        else:
                            await query.edit_message_text(ERROR_MESSAGE)
                            await _cleanup_user_session(user_id, context)
                    case "audio_m4a":
                        audio_only = formats.get('audio_only', [])
                        # Приоритет нативных форматов для Telegram sendAudio: m4a > mp3 > ogg
                        native_audio = None
                        for ext in ['m4a', 'mp3', 'ogg']:
                            native_audio = next((f for f in audio_only if f.get('ext') == ext), None)
                            if native_audio:
                                logger.info(f"Найден нативный аудио формат: {ext}")
                                break
                        
                        # Fallback: если нет нативных форматов (m4a/mp3/ogg), конвертируем в mp3
                        if not native_audio and audio_only:
                            logger.warning(f"Нативные форматы не найдены. Доступные: {[f.get('ext') for f in audio_only]}. Конвертируем в m4a.")
                            await safe_edit_message_text(query, DOWNLOADING_AUDIO_MESSAGE)
                            # Используем bestaudio с конвертацией в m4a
                            file_path = await run_blocking(
                                functools.partial(download_audio, preferred_codec='m4a'),
                                url,
                                'bestaudio',
                                session_id,
                                description="download_audio_bestaudio",
                            )
                            if file_path:
                                if isinstance(file_path, str) and file_path.startswith("http"):
                                    await query.edit_message_text(FILE_TOO_LARGE_LINK.format(file_path=file_path))
                                    return
                                await send_file(query, file_path, session_id, context)
                            else:
                                await query.edit_message_text(ERROR_MESSAGE)
                                await _cleanup_user_session(user_id, context)
                        elif native_audio:
                            await query.edit_message_text(DOWNLOADING_AUDIO_MESSAGE)
                            file_path = await run_blocking(
                                download_audio_native,
                                url,
                                native_audio['format_id'],
                                session_id,
                                description="download_audio_native",
                            )
                            if file_path:
                                if isinstance(file_path, str) and file_path.startswith("http"):
                                    await query.edit_message_text(FILE_TOO_LARGE_LINK.format(file_path=file_path))
                                    return
                                await send_file(query, file_path, session_id, context)
                            else:
                                await query.edit_message_text(ERROR_MESSAGE)
                                await _cleanup_user_session(user_id, context)
                        else:
                            await query.edit_message_text(ERROR_MESSAGE)
                            await _cleanup_user_session(user_id, context)
                    case "tg_video":
                        combined = formats.get('combined', [])
                        tg_video = None
                        
                        # Логируем доступные форматы для отладки
                        logger.info("Доступные combined форматы для tg_video:")
                        for i, f in enumerate(combined):
                            logger.info(f"  {i}: {f.get('format_id')} - {f.get('height')}p - {f.get('ext')} - размер: {f.get('filesize')} байт")
                        
                        # НОВАЯ ЛОГИКА: Сначала пробуем комбинированный подход для лучшего качества
                        video_only = formats.get('video_only', [])
                        audio_only = formats.get('audio_only', [])
                        
                        # Ищем подходящие video_only форматы
                        suitable_video = []
                        for v in video_only:
                            size = v.get('filesize')
                            if size is not None and size <= 35 * 1024 * 1024:  # Оставляем место для аудио
                                suitable_video.append(v)
                        
                        # Ищем подходящий аудио формат
                        suitable_audio = None
                        for a in audio_only:
                            size = a.get('filesize')
                            if size is not None and size <= 15 * 1024 * 1024:  # Аудио обычно меньше
                                suitable_audio = a
                                break
                        
                        # Если можем собрать комбинированный формат лучшего качества
                        if suitable_video and suitable_audio:
                            # Выбираем лучшее видео качество
                            best_video = max(suitable_video, key=lambda x: x.get('height', 0))
                            video_size = best_video.get('filesize') / (1024 * 1024)
                            audio_size = suitable_audio.get('filesize') / (1024 * 1024)
                            total_size = video_size + audio_size
                            
                            logger.info(f"Комбинированный подход: видео {best_video['format_id']} ({best_video.get('height')}p, {video_size:.1f} МБ) + аудио {suitable_audio['format_id']} ({audio_size:.1f} МБ) = {total_size:.1f} МБ")
                            
                            # Используем специальный формат для объединения
                            tg_video = {
                                'format_id': f"{best_video['format_id']}+{suitable_audio['format_id']}",
                                'height': best_video.get('height'),
                                'ext': best_video.get('ext', 'mp4'),
                                'type': 'combined_manual'
                            }
                            logger.info(f"Выбран комбинированный формат: {tg_video.get('format_id')} - {tg_video.get('height')}p")
                            
                            # Пробуем скачать комбинированный формат
                            await safe_edit_message_text(query, DOWNLOADING_MESSAGE)
                            try:
                                file_path = await download_content(url, tg_video['format_id'], session_id, "combined")
                            except Exception as e:
                                error_code = _youtube_error_code(str(e))
                                logger.warning(
                                    "YT_DL_STAGE_FAIL code=%s stage=tg_video_manual_combined format_id=%s url=%s error=%s",
                                    error_code,
                                    tg_video['format_id'],
                                    url,
                                    e,
                                    exc_info=True,
                                )
                                file_path = None
                            
                            # Если комбинированный формат не сработал, переключаемся на готовые форматы
                            if not file_path:
                                tg_video = None  # Сбрасываем для fallback логики
                            else:
                                # Успешно скачали комбинированный формат
                                if isinstance(file_path, str) and file_path.startswith("http"):
                                    await query.edit_message_text(FILE_TOO_LARGE_LINK.format(file_path=file_path))
                                    return
                                await send_file(query, file_path, session_id, context)
                                return
                        
                        # Fallback логика: используем готовые combined форматы
                        if not tg_video:
                            suitable_formats = []
                            formats_without_size = []
                            
                            for f in combined:
                                size = f.get('filesize')
                                if size is not None and size <= 50 * 1024 * 1024:
                                    suitable_formats.append(f)
                                    logger.info(f"Подходящий combined формат: {f.get('format_id')} - {f.get('height')}p - {size/1024/1024:.1f} МБ")
                                elif size is None:
                                    formats_without_size.append(f)
                            
                            if suitable_formats:
                                # Выбираем формат с наибольшим разрешением среди подходящих
                                tg_video = max(suitable_formats, key=lambda x: x.get('height', 0))
                                logger.info(f"Выбран готовый combined формат: {tg_video.get('format_id')} - {tg_video.get('height')}p")
                            elif formats_without_size:
                                # Если нет combined форматов с известным размером, берем формат среднего качества из combined
                                formats_without_size.sort(key=lambda x: x.get('height', 0))
                                middle_index = len(formats_without_size) // 3
                                tg_video = formats_without_size[middle_index] if formats_without_size else None
                                if tg_video:
                                    logger.info(f"Выбран резервный формат (размер неизвестен): {tg_video.get('format_id')} - {tg_video.get('height')}p")
                        
                        if tg_video:
                            await safe_edit_message_text(query, DOWNLOADING_MESSAGE)
                            file_path = await download_content(url, tg_video['format_id'], session_id, "combined")
                            if file_path:
                                if isinstance(file_path, str) and file_path.startswith("http"):
                                    await query.edit_message_text(FILE_TOO_LARGE_LINK.format(file_path=file_path))
                                    return
                                await send_file(query, file_path, session_id, context)
                            else:
                                await query.edit_message_text(ERROR_MESSAGE)
                                await _cleanup_user_session(user_id, context)
                        else:
                            if any(f.get('filesize') is None for f in combined):
                                keyboard = [[InlineKeyboardButton(BTN_BACK, callback_data="main|back")]]
                                await safe_edit_message_text(query, NO_FILESIZE, reply_markup=InlineKeyboardMarkup(keyboard))
                            else:
                                keyboard = [[InlineKeyboardButton(BTN_BACK, callback_data="main|back")]]
                                await safe_edit_message_text(query, NO_TG_VIDEO, reply_markup=InlineKeyboardMarkup(keyboard))
                    case "more":
                        # Сформировать расширенную клавиатуру (старый вариант)
                        keyboard = []
                        added_button_labels = set()
                        combined_count = 0
                        for fmt in formats.get('combined', []):
                            label = f"📹+🔊 {fmt.get('height', 'N/A')}p - {fmt.get('ext', 'mp4').upper()}"
                            if label not in added_button_labels:
                                if combined_count < 3:
                                    callback_data = f"format|combined|{fmt['format_id']}"
                                    keyboard.append([
                                        InlineKeyboardButton(label, callback_data=callback_data)
                                    ])
                                    added_button_labels.add(label)
                                    combined_count += 1
                                else:
                                    break
                        video_only_count = 0
                        for fmt in formats.get('video_only', []):
                            label = f"📹 {fmt.get('height', 'N/A')}p - {fmt.get('ext', 'mp4').upper()} (без звука)"
                            if label not in added_button_labels:
                                if video_only_count < 3:
                                    callback_data = f"format|video_only|{fmt['format_id']}"
                                    keyboard.append([
                                        InlineKeyboardButton(label, callback_data=callback_data)
                                    ])
                                    added_button_labels.add(label)
                                    video_only_count += 1
                                else:
                                    break
                        audio_only = formats.get('audio_only', [])
                        audio_only_count = 0
                        for fmt in audio_only:
                            label = f"🔊 Только аудио - {fmt.get('ext', 'm4a').upper()}"
                            if label not in added_button_labels:
                                if audio_only_count < 2:
                                    callback_data = f"format|audio_only|{fmt['format_id']}"
                                    keyboard.append([
                                        InlineKeyboardButton(label, callback_data=callback_data)
                                    ])
                                    added_button_labels.add(label)
                                    audio_only_count += 1
                                else:
                                    break
                        # Добавляю кнопку для mp3 (минимальный размер)
                        if audio_only:
                            min_m4a = min([f for f in audio_only if f.get('ext') == 'm4a'], key=lambda x: x.get('filesize', float('inf')), default=None)
                            if min_m4a:
                                mp3_label = MP3_MIN_LABEL
                                callback_data = f"format|mp3_min|{min_m4a['format_id']}"
                                keyboard.append([
                                    InlineKeyboardButton(mp3_label, callback_data=callback_data)
                                ])
                        best_label = BEST_QUALITY_LABEL if is_gokapi_configured() else BEST_QUALITY_LABEL + " (может не влезть в ТГ)"
                        keyboard.append([
                            InlineKeyboardButton(best_label, callback_data="format|best|best")
                        ])
                        keyboard.append([
                            InlineKeyboardButton(BEST_AUDIO_LABEL, callback_data="format|audio_best|bestaudio")
                        ])
                        # Добавляю кнопку скачивания субтитров
                        keyboard.append([
                            InlineKeyboardButton("📝 Скачать субтитры (SRT)", callback_data="main|subtitles")
                        ])
                        # Добавляю кнопку назад
                        keyboard.append([
                            InlineKeyboardButton(BTN_BACK, callback_data="main|back")
                        ])
                        reply_markup = InlineKeyboardMarkup(keyboard)
                        video_info = context.user_data['video_info']
                        await query.edit_message_text(
                            DOWNLOAD_FORMAT_PROMPT.format(
                                title=video_info.get('title', 'Video'),
                                duration=format_duration(video_info.get('duration', 0))
                            ),
                            reply_markup=reply_markup,
                            parse_mode='Markdown'
                        )
                    case "subtitles":
                        await query.edit_message_text(DOWNLOADING_SUBTITLES_MESSAGE)
                        try:
                            subtitle_file = await run_blocking(
                                download_subtitles,
                                url,
                                session_id,
                                description="download_subtitles",
                            )
                            if subtitle_file and subtitle_file.exists():
                                await query.edit_message_text(FILE_PREPARING)
                                # Отправляем субтитры как документ
                                with open(subtitle_file, 'rb') as srt_file:
                                    await query.message.reply_document(
                                        document=srt_file,
                                        caption=SUBTITLE_CAPTION
                                    )
                                await query.edit_message_text(FILE_SENT)
                                # Удаляем файл после отправки
                                try:
                                    subtitle_file.unlink()
                                except Exception as e:
                                    logger.error(f"Ошибка удаления файла субтитров: {e}")
                                await _cleanup_user_session(user_id, context)
                            else:
                                keyboard = [[InlineKeyboardButton(BTN_BACK, callback_data="main|back")]]
                                await query.edit_message_text(NO_SUBTITLES_AVAILABLE, reply_markup=InlineKeyboardMarkup(keyboard))
                        except Exception as e:
                            logger.error(f"Ошибка скачивания субтитров: {e}", exc_info=True)
                            keyboard = [[InlineKeyboardButton(BTN_BACK, callback_data="main|back")]]
                            await query.edit_message_text(NO_SUBTITLES_AVAILABLE, reply_markup=InlineKeyboardMarkup(keyboard))
                    case "back":
                        platform = context.user_data.get('platform', 'youtube')
                        video_info = context.user_data['video_info']
                        text, reply_markup = _build_main_menu(platform, video_info)
                        await safe_edit_message_text(
                            query,
                            text,
                            reply_markup=reply_markup,
                            parse_mode='Markdown',
                        )
                        return
                    case _:
                        await query.edit_message_text(ERROR_MESSAGE)
                        await _cleanup_user_session(user_id, context)
            case ["format", content_type, format_id]:
                if content_type == "mp3_min":
                    await safe_edit_message_text(query, DOWNLOADING_AUDIO_MESSAGE)
                    url = context.user_data['url']
                    session_id = context.user_data['session_id']
                    formats = context.user_data.get('formats', {})
                    audio_only = formats.get('audio_only', [])
                    min_m4a = next((f for f in audio_only if f.get('format_id') == format_id and f.get('ext') == 'm4a'), None)
                    if min_m4a:
                        # Скачиваем m4a локально, не загружаем на Gokapi
                        m4a_path = await run_blocking(
                            download_audio,
                            url,
                            min_m4a['format_id'],
                            session_id,
                            True,
                            description="download_audio_min",
                        )
                        # Конвертируем и сжимаем в mp3 (50%)
                        mp3_path = await run_blocking(
                            convert_to_mp3_with_compression,
                            m4a_path,
                            session_id,
                            description="convert_to_mp3_with_compression",
                        )
                        # Удаляем исходный m4a после конвертации
                        try:
                            m4a_path.unlink()
                        except Exception:
                            pass
                        await send_file(query, mp3_path, session_id, context)
                    else:
                        await query.edit_message_text(ERROR_MESSAGE)
                        await _cleanup_user_session(user_id, context)
                    return
                else:
                    if content_type in ("audio_only", "audio_best"):
                        await safe_edit_message_text(query, DOWNLOADING_AUDIO_MESSAGE)
                    else:
                        await safe_edit_message_text(query, DOWNLOADING_MESSAGE)
                try:
                    url = context.user_data['url']
                    session_id = context.user_data['session_id']
                    file_path = None
                    match content_type:
                        case "combined":
                            file_path = await download_content(url, format_id, session_id, "combined")
                        case "video_only":
                            file_path = await download_content(url, format_id, session_id, "video_only")
                        case "audio_only":
                            file_path = await download_content(url, format_id, session_id, "audio_only")
                        case "best":
                            file_path = await download_content(url, "bestvideo+bestaudio", session_id, "best")
                        case "audio_best":
                            file_path = await download_content(url, "bestaudio", session_id, "audio_best")
                    if file_path:
                        if isinstance(file_path, str) and file_path.startswith("http"):
                            await query.edit_message_text(FILE_TOO_LARGE_LINK.format(file_path=file_path))
                            return
                        await send_file(query, file_path, session_id, context)
                    else:
                        await query.edit_message_text(ERROR_MESSAGE)
                        await _cleanup_user_session(user_id, context)
                except Exception as e:
                    e.add_note(f"user_id={user_id}, url={url}, session_id={session_id}")
                    logger.error(f"Ошибка при скачивании: {e}", exc_info=True)
                    error_code = _make_error_code("youtube", _classify_internal_error_category("youtube", str(e)))
                    _schedule_platform_failure_log(
                        platform="youtube",
                        stage="legacy_download",
                        url=url,
                        error_code=error_code,
                        exc=e,
                        session_id=session_id,
                    )
                    await query.edit_message_text(_build_public_error_message("youtube", error_code, str(e)))
                    await _cleanup_user_session(user_id, context)
            case _:
                await query.edit_message_text(ERROR_MESSAGE)
                await _cleanup_user_session(user_id, context)
    except Exception as e:
        logger.error(f"Ошибка в button_callback: {e}", exc_info=True)
        error_msg = str(e)
        
        # Специальная обработка для ошибок парсинга Markdown
        if "Can't parse entities" in error_msg:
            try:
                await query.edit_message_text(
                    "❌ Ошибка отображения информации о видео.\n"
                    "Попробуйте другую ссылку или повторите попытку.",
                    parse_mode=None  # Отключаем Markdown
                )
            except Exception:
                await query.edit_message_text(ERROR_FALLBACK)
        elif classified := _classify_youtube_error(error_msg):
            try:
                await query.edit_message_text(classified, parse_mode='Markdown')
            except Exception:
                await query.edit_message_text(ERROR_FALLBACK)
        else:
            try:
                await query.edit_message_text(f"❌ {ERROR_MESSAGE}")
            except Exception:
                await query.edit_message_text(ERROR_FALLBACK)
        
        await _cleanup_user_session(user_id, context)

async def _handle_main_callback(
    query: telegram.CallbackQuery,
    context: ContextTypes.DEFAULT_TYPE,
    user_id: int,
    session_token: str,
    action: str,
) -> None:
    """Новая версия обработчика main-callback с привязкой к токену сессии."""
    session_data = _get_session(context, session_token)
    if not session_data:
        await query.edit_message_text(SESSION_EXPIRED)
        return

    formats = session_data.get('formats', {})
    url = session_data['url']
    session_id = session_data['session_id']
    platform = session_data.get('platform', 'youtube')
    back_markup = _build_back_markup(session_token)

    match action:
        case "tiktok_download":
            # Проверяем кэш перед скачиванием
            cache_key = _cache_format_id_for_main_action("tiktok", "tiktok_download")
            if cache_key:
                cached = telegram_cache.get(url, format_id=cache_key)
                if cached:
                    try:
                        await query.message.reply_video(
                            video=cached.file_id,
                            caption=None,
                            supports_streaming=True,
                        )
                        logger.info("TikTok видео доставлено из кэша (key=%s)", cache_key)
                        await query.edit_message_text(FILE_SENT)
                        await _cleanup_user_session(user_id, context, session_token)
                        return
                    except telegram.error.BadRequest as e:
                        logger.warning("file_id устарел (key=%s): %s", cache_key, e)
                        telegram_cache.delete_by_file_id(cached.file_id)

            await safe_edit_message_text(query, DOWNLOADING_MESSAGE)
            from utils.tiktok_instagram_utils import download_tiktok_video

            try:
                file_path = await run_blocking(
                    download_tiktok_video,
                    url,
                    session_id,
                    None,
                    False,
                    session_data.get('video_info'),
                    description="download_tiktok_video",
                )
                if not file_path:
                    await query.edit_message_text(ERROR_MESSAGE)
                    await _cleanup_user_session(user_id, context, session_token)
                    return
                await send_file(
                    query,
                    file_path,
                    session_token,
                    session_data,
                    context,
                    cache_format_id=_cache_format_id_for_main_action("tiktok", "tiktok_download"),
                )
            except Exception as e:
                error_code = _make_error_code("tiktok", _classify_internal_error_category("tiktok", str(e)))
                _schedule_platform_failure_log(
                    platform="tiktok",
                    stage="download_video",
                    url=url,
                    error_code=error_code,
                    exc=e,
                    session_id=session_id,
                )
                await query.edit_message_text(_build_public_error_message("tiktok", error_code, str(e)))
                await _cleanup_user_session(user_id, context, session_token)
            return

        case "tiktok_audio":
            await safe_edit_message_text(query, DOWNLOADING_AUDIO_MESSAGE)
            from utils.tiktok_instagram_utils import download_tiktok_audio

            try:
                file_path = await run_blocking(
                    download_tiktok_audio,
                    url,
                    session_id,
                    None,
                    False,
                    session_data.get('video_info'),
                    description="download_tiktok_audio",
                )
                if not file_path:
                    await query.edit_message_text(ERROR_MESSAGE)
                    await _cleanup_user_session(user_id, context, session_token)
                    return
                await send_file(query, file_path, session_token, session_data, context, cache_format_id="tiktok_audio")
            except Exception as e:
                error_code = _make_error_code("tiktok", _classify_internal_error_category("tiktok", str(e)))
                _schedule_platform_failure_log(
                    platform="tiktok",
                    stage="download_audio",
                    url=url,
                    error_code=error_code,
                    exc=e,
                    session_id=session_id,
                )
                await query.edit_message_text(_build_public_error_message("tiktok", error_code, str(e)))
                await _cleanup_user_session(user_id, context, session_token)
            return

        case "instagram_download":
            # Проверяем кэш перед скачиванием
            cache_key = _cache_format_id_for_main_action("instagram", "instagram_download")
            if cache_key:
                cached = telegram_cache.get(url, format_id=cache_key)
                if cached:
                    try:
                        await query.message.reply_video(
                            video=cached.file_id,
                            caption=None,
                            supports_streaming=True,
                        )
                        logger.info("Instagram видео доставлено из кэша (key=%s)", cache_key)
                        await query.edit_message_text(FILE_SENT)
                        await _cleanup_user_session(user_id, context, session_token)
                        return
                    except telegram.error.BadRequest as e:
                        logger.warning("file_id устарел (key=%s): %s", cache_key, e)
                        telegram_cache.delete_by_file_id(cached.file_id)

            await safe_edit_message_text(query, DOWNLOADING_MESSAGE)
            from utils.tiktok_instagram_utils import download_instagram_video

            try:
                file_path = await run_blocking(
                    download_instagram_video,
                    url,
                    session_id,
                    description="download_instagram_video",
                )
                if not file_path:
                    await query.edit_message_text(ERROR_MESSAGE)
                    await _cleanup_user_session(user_id, context, session_token)
                    return
                await send_file(
                    query,
                    file_path,
                    session_token,
                    session_data,
                    context,
                    cache_format_id=_cache_format_id_for_main_action("instagram", "instagram_download"),
                )
            except Exception as e:
                error_code = _make_error_code("instagram", _classify_internal_error_category("instagram", str(e)))
                _schedule_platform_failure_log(
                    platform="instagram",
                    stage="download_video",
                    url=url,
                    error_code=error_code,
                    exc=e,
                    session_id=session_id,
                )
                await query.edit_message_text(_build_public_error_message("instagram", error_code, str(e)))
                await _cleanup_user_session(user_id, context, session_token)
            return

        case "instagram_audio":
            await safe_edit_message_text(query, DOWNLOADING_AUDIO_MESSAGE)
            from utils.tiktok_instagram_utils import download_instagram_audio

            try:
                file_path = await run_blocking(
                    download_instagram_audio,
                    url,
                    session_id,
                    description="download_instagram_audio",
                )
                if not file_path:
                    await query.edit_message_text(ERROR_MESSAGE)
                    await _cleanup_user_session(user_id, context, session_token)
                    return
                await send_file(query, file_path, session_token, session_data, context, cache_format_id="instagram_audio")
            except Exception as e:
                error_code = _make_error_code("instagram", _classify_internal_error_category("instagram", str(e)))
                _schedule_platform_failure_log(
                    platform="instagram",
                    stage="download_audio",
                    url=url,
                    error_code=error_code,
                    exc=e,
                    session_id=session_id,
                )
                await query.edit_message_text(_build_public_error_message("instagram", error_code, str(e)))
                await _cleanup_user_session(user_id, context, session_token)
            return

        case "audio_m4a":
            audio_only = formats.get('audio_only', [])
            native_audio = None
            for ext in ['m4a', 'mp3', 'ogg']:
                native_audio = next((f for f in audio_only if f.get('ext') == ext), None)
                if native_audio:
                    logger.info(f"Найден нативный аудио формат: {ext}")
                    break

            if not native_audio and audio_only:
                logger.warning(
                    "Нативные форматы не найдены. Доступные: %s. Конвертируем в m4a.",
                    [f.get('ext') for f in audio_only],
                )
                await safe_edit_message_text(query, DOWNLOADING_AUDIO_MESSAGE)
                file_path = await run_blocking(
                    functools.partial(download_audio, preferred_codec='m4a'),
                    url,
                    'bestaudio',
                    session_id,
                    description="download_audio_bestaudio",
                )
            elif native_audio:
                await safe_edit_message_text(query, DOWNLOADING_AUDIO_MESSAGE)
                file_path = await run_blocking(
                    download_audio_native,
                    url,
                    native_audio['format_id'],
                    session_id,
                    description="download_audio_native",
                )
            else:
                await query.edit_message_text(ERROR_MESSAGE)
                await _cleanup_user_session(user_id, context, session_token)
                return

            if not file_path:
                await query.edit_message_text(ERROR_MESSAGE)
                await _cleanup_user_session(user_id, context, session_token)
                return

            await send_file(query, file_path, session_token, session_data, context, cache_format_id="audio_m4a")
            return

        case "tg_video":
            combined = formats.get('combined', [])
            tg_video = None

            logger.info("Доступные combined форматы для tg_video:")
            for i, fmt in enumerate(combined):
                logger.info(
                    "  %s: %s - %sp - %s - размер: %s байт",
                    i,
                    fmt.get('format_id'),
                    fmt.get('height'),
                    fmt.get('ext'),
                    fmt.get('filesize'),
                )

            video_only = formats.get('video_only', [])
            audio_only = formats.get('audio_only', [])
            suitable_video = [
                v for v in video_only
                if v.get('filesize') is not None and v.get('filesize') <= 35 * 1024 * 1024
            ]
            suitable_audio = next(
                (
                    a for a in audio_only
                    if a.get('filesize') is not None and a.get('filesize') <= 15 * 1024 * 1024
                ),
                None,
            )

            if suitable_video and suitable_audio:
                best_video = max(suitable_video, key=lambda x: x.get('height', 0))
                tg_video = {
                    'format_id': f"{best_video['format_id']}+{suitable_audio['format_id']}",
                    'height': best_video.get('height'),
                    'ext': best_video.get('ext', 'mp4'),
                    'type': 'combined_manual',
                }
                logger.info(
                    "Выбран комбинированный формат: %s - %sp",
                    tg_video.get('format_id'),
                    tg_video.get('height'),
                )

                await safe_edit_message_text(query, DOWNLOADING_MESSAGE)
                try:
                    file_path = await download_content(url, tg_video['format_id'], session_id, "combined")
                except Exception as e:
                    error_code = _youtube_error_code(str(e))
                    logger.warning(
                        "YT_DL_STAGE_FAIL code=%s stage=tg_video_manual_combined format_id=%s url=%s error=%s",
                        error_code,
                        tg_video['format_id'],
                        url,
                        e,
                        exc_info=True,
                    )
                    file_path = None

                if file_path:
                    await send_file(
                        query,
                        file_path,
                        session_token,
                        session_data,
                        context,
                        cache_format_id=_cache_format_id_for_main_action("youtube", "tg_video"),
                    )
                    return
                tg_video = None

            if not tg_video:
                suitable_formats = []
                formats_without_size = []

                for fmt in combined:
                    size = fmt.get('filesize')
                    if size is not None and size <= 50 * 1024 * 1024:
                        suitable_formats.append(fmt)
                    elif size is None:
                        formats_without_size.append(fmt)

                if suitable_formats:
                    tg_video = max(suitable_formats, key=lambda x: x.get('height', 0))
                    logger.info(
                        "Выбран готовый combined формат: %s - %sp",
                        tg_video.get('format_id'),
                        tg_video.get('height'),
                    )
                elif formats_without_size:
                    formats_without_size.sort(key=lambda x: x.get('height', 0))
                    middle_index = len(formats_without_size) // 3
                    tg_video = formats_without_size[middle_index] if formats_without_size else None
                    if tg_video:
                        logger.info(
                            "Выбран резервный формат (размер неизвестен): %s - %sp",
                            tg_video.get('format_id'),
                            tg_video.get('height'),
                        )

            if tg_video:
                await safe_edit_message_text(query, DOWNLOADING_MESSAGE)
                file_path = await download_content(url, tg_video['format_id'], session_id, "combined")
                if not file_path:
                    await query.edit_message_text(ERROR_MESSAGE)
                    await _cleanup_user_session(user_id, context, session_token)
                    return
                await send_file(
                    query,
                    file_path,
                    session_token,
                    session_data,
                    context,
                    cache_format_id=_cache_format_id_for_main_action("youtube", "tg_video"),
                )
                return

            if any(fmt.get('filesize') is None for fmt in combined):
                await safe_edit_message_text(query, NO_FILESIZE, reply_markup=back_markup)
            else:
                await safe_edit_message_text(query, NO_TG_VIDEO, reply_markup=back_markup)
            return

        case "more":
            await safe_edit_message_text(
                query,
                _build_youtube_prompt(session_data['video_info']),
                reply_markup=_build_youtube_more_menu(formats, session_token),
                parse_mode='Markdown',
            )
            return

        case "subtitles":
            await safe_edit_message_text(query, DOWNLOADING_SUBTITLES_MESSAGE)
            try:
                subtitle_file = await run_blocking(
                    download_subtitles,
                    url,
                    session_id,
                    description="download_subtitles",
                )
                if subtitle_file and subtitle_file.exists():
                    await query.edit_message_text(FILE_PREPARING)
                    with open(subtitle_file, 'rb') as srt_file:
                        await query.message.reply_document(
                            document=srt_file,
                            caption=SUBTITLE_CAPTION,
                        )
                    await query.edit_message_text(FILE_SENT)
                    try:
                        subtitle_file.unlink()
                    except Exception as e:
                        logger.error(f"Ошибка удаления файла субтитров: {e}")
                    await _cleanup_user_session(user_id, context, session_token)
                else:
                    await query.edit_message_text(
                        NO_SUBTITLES_AVAILABLE,
                        reply_markup=back_markup,
                    )
            except Exception as e:
                logger.error(f"Ошибка скачивания субтитров: {e}", exc_info=True)
                await query.edit_message_text(
                    NO_SUBTITLES_AVAILABLE,
                    reply_markup=back_markup,
                )
            return

        case "back":
            text, reply_markup = _build_main_menu(platform, session_data['video_info'], session_token)
            await safe_edit_message_text(
                query,
                text,
                reply_markup=reply_markup,
                parse_mode='Markdown',
            )
            return

        case _:
            await query.edit_message_text(ERROR_MESSAGE)
            await _cleanup_user_session(user_id, context, session_token)
            return


async def _handle_format_callback(
    query: telegram.CallbackQuery,
    context: ContextTypes.DEFAULT_TYPE,
    user_id: int,
    session_token: str,
    content_type: str,
    format_id: str,
) -> None:
    """Новая версия обработчика format-callback с привязкой к токену сессии."""
    session_data = _get_session(context, session_token)
    if not session_data:
        await query.edit_message_text(SESSION_EXPIRED)
        return

    url = session_data['url']
    session_id = session_data['session_id']
    formats = session_data.get('formats', {})

    if content_type == "mp3_min":
        await safe_edit_message_text(query, DOWNLOADING_AUDIO_MESSAGE)
        audio_only = formats.get('audio_only', [])
        min_m4a = next(
            (
                f for f in audio_only
                if f.get('format_id') == format_id and f.get('ext') == 'm4a'
            ),
            None,
        )
        if not min_m4a:
            await query.edit_message_text(ERROR_MESSAGE)
            await _cleanup_user_session(user_id, context, session_token)
            return

        m4a_path = await run_blocking(
            download_audio,
            url,
            min_m4a['format_id'],
            session_id,
            True,
            description="download_audio_min",
        )
        mp3_path = await run_blocking(
            convert_to_mp3_with_compression,
            m4a_path,
            session_id,
            description="convert_to_mp3_with_compression",
        )
        try:
            m4a_path.unlink()
        except Exception:
            pass
        await send_file(query, mp3_path, session_token, session_data, context)
        return

    if content_type in ("audio_only", "audio_best"):
        await safe_edit_message_text(query, DOWNLOADING_AUDIO_MESSAGE)
    else:
        await safe_edit_message_text(query, DOWNLOADING_MESSAGE)

    try:
        file_path = None
        cache_format_id = _cache_format_id_for_format_selection(content_type, format_id)
        match content_type:
            case "combined":
                file_path = await download_content(url, format_id, session_id, "combined")
            case "video_only":
                file_path = await download_content(url, format_id, session_id, "video_only")
            case "audio_only":
                file_path = await download_content(url, format_id, session_id, "audio_only")
            case "best":
                file_path = await download_content(url, "bestvideo+bestaudio", session_id, "best")
            case "audio_best":
                file_path = await download_content(url, "bestaudio", session_id, "audio_best")

        if not file_path:
            await query.edit_message_text(ERROR_MESSAGE)
            await _cleanup_user_session(user_id, context, session_token)
            return

        await send_file(
            query,
            file_path,
            session_token,
            session_data,
            context,
            cache_format_id=cache_format_id,
        )
    except Exception as e:
        e.add_note(f"user_id={user_id}, url={url}, session_id={session_id}")
        error_code = _make_error_code("youtube", _classify_internal_error_category("youtube", str(e)))
        _schedule_platform_failure_log(
            platform="youtube",
            stage="format_download",
            url=url,
            error_code=error_code,
            exc=e,
            session_id=session_id,
        )
        await query.edit_message_text(_build_public_error_message("youtube", error_code, str(e)))
        await _cleanup_user_session(user_id, context, session_token)


async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Новая версия callback-обработчика с независимыми пользовательскими сессиями."""
    query = update.callback_query
    if not query or not query.data:
        return

    user_id = update.effective_user.id
    session_token: str | None = None
    now = asyncio.get_running_loop().time()

    if _should_rate_limit_callback(query.data) and _check_spam(user_id, context, now):
        await query.answer(text=SPAM_WARNING, show_alert=False)
        return

    try:
        await query.answer()
    except telegram.error.TelegramError:
        logger.debug("Не удалось подтвердить callback, продолжаем обработку")

    logger.info(f"Получен колбэк от пользователя {user_id}: {query.data}")

    try:
        data = query.data.split('|')
        match data:
            case ["s", session_token, "main", action]:
                await _handle_main_callback(query, context, user_id, session_token, action)
            case ["s", session_token, "format", content_type, format_id]:
                await _handle_format_callback(query, context, user_id, session_token, content_type, format_id)
            case _:
                await query.edit_message_text(SESSION_EXPIRED)
    except Exception as e:
        logger.error(f"Ошибка в button_callback: {e}", exc_info=True)
        error_msg = str(e)

        if "Can't parse entities" in error_msg:
            try:
                await query.edit_message_text(
                    "❌ Ошибка отображения информации о видео.\n"
                    "Попробуйте другую ссылку или повторите попытку.",
                    parse_mode=None,
                )
            except Exception:
                await query.edit_message_text(ERROR_FALLBACK)
        elif classified := (_classify_youtube_error(error_msg) or _classify_large_file_delivery_error(error_msg)):
            try:
                await query.edit_message_text(classified, parse_mode='Markdown')
            except Exception:
                await query.edit_message_text(ERROR_FALLBACK)
        else:
            error_code = _make_error_code("bot", "CALLBACK")
            _schedule_platform_failure_log(
                platform="bot",
                stage="button_callback",
                url=None,
                error_code=error_code,
                exc=e,
                session_id=session_token,
            )
            try:
                await query.edit_message_text(USER_ERROR_WITH_CODE.format(error_code=error_code))
            except Exception:
                await query.edit_message_text(ERROR_FALLBACK)

        if session_token:
            await _cleanup_user_session(user_id, context, session_token)


async def download_content(
    url: str, 
    format_id: str, 
    session_id: str, 
    content_type: str
) -> Path | str | None:
    """
    Скачивает контент в зависимости от типа.
    Все блокирующие вызовы выполняются через run_blocking.
    """
    try:
        if "+" in format_id and content_type == "combined":
            logger.info(f"Обнаружен комбинированный формат: {format_id}")
            return await run_blocking(
                download_video,
                url,
                format_id,
                session_id,
                description="download_video_combined",
            )
        if content_type == "combined":
            return await run_blocking(
                download_video,
                url,
                format_id,
                session_id,
                description="download_video_combined_simple",
            )
        if content_type == "video_only":
            return await run_blocking(
                download_video,
                url,
                format_id,
                session_id,
                description="download_video_only",
            )
        if content_type == "audio_only":
            return await run_blocking(
                download_audio,
                url,
                format_id,
                session_id,
                description="download_audio_only",
            )
        if content_type == "best":
            try:
                return await run_blocking(
                    download_video,
                    url,
                    "bestvideo+bestaudio/best",
                    session_id,
                    description="download_video_best_combo",
                )
            except Exception as e:
                logger.warning(f"Не удалось скачать bestvideo+bestaudio: {e}")
                logger.info("Пробуем скачать в формате best")
                return await run_blocking(
                    download_video,
                    url,
                    "best",
                    session_id,
                    description="download_video_best",
                )
        if content_type == "audio_best":
            return await run_blocking(
                download_audio,
                url,
                "bestaudio",
                session_id,
                description="download_audio_bestaudio_only",
            )
        raise ValueError(f"Неподдерживаемый content_type: {content_type}")
    except Exception as e:
        e.add_note(
            f"url={url}, format_id={format_id}, session_id={session_id}, content_type={content_type}"
        )
        error_code = _youtube_error_code(str(e))
        logger.error(
            "YT_DL_FAIL code=%s stage=download_content content_type=%s format_id=%s url=%s error=%s",
            error_code,
            content_type,
            format_id,
            url,
            e,
            exc_info=True,
        )
        raise

async def _send_file_legacy_unsafe(
    query: telegram.CallbackQuery, 
    file_path: Path | str,
    session_id: str,
    context: ContextTypes.DEFAULT_TYPE # Добавил context для очистки сессии
) -> None:
    """
    Отправляет файл пользователю.
    
    Args:
        query (telegram.CallbackQuery): Объект колбэк-запроса.
        file_path (Path | str): Путь к файлу или ссылка.
        session_id (str): Идентификатор сессии.
    """
    user_id = query.from_user.id
    try:
        # Если файл был загружен на Gokapi (возвращается ссылка)
        if isinstance(file_path, str) and file_path.startswith("http"):
            await query.edit_message_text(
                FILE_TOO_LARGE_LINK.format(file_path=file_path)
            )
            return
        
        # Отправка одного файла (Path)
        if isinstance(file_path, Path):
            await query.edit_message_text(FILE_PREPARING)
            await asyncio.sleep(1)
            success = await send_single_file(query, file_path, context=context)
            if success:
                await query.edit_message_text(FILE_SENT)
                await _cleanup_user_session(user_id, context)
            else:
                await _cleanup_user_session(user_id, context)
        else:
            # Случай, если file_path не строка-ссылка и не Path, что маловероятно тут
            logger.error(f"Неожиданный тип file_path в send_file: {type(file_path)}")
            await query.edit_message_text(ERROR_MESSAGE)
            await _cleanup_user_session(user_id, context)
            
    except (FileNotFoundError, PermissionError) as e:
        error_code = _make_error_code("file", "ACCESS")
        _schedule_platform_failure_log("file", error_code, "legacy_send_file", e, session_id=session_id)
        await query.edit_message_text(USER_FILE_ERROR_WITH_CODE.format(error_code=error_code))
        await _cleanup_user_session(user_id, context)
    except telegram.error.NetworkError as e:
        error_code = _make_error_code("telegram", "NETWORK")
        _schedule_platform_failure_log("telegram", error_code, "legacy_send_file", e, session_id=session_id)
        await query.edit_message_text(USER_NETWORK_ERROR_WITH_CODE.format(error_code=error_code))
        await _cleanup_user_session(user_id, context)
    except telegram.error.TelegramError as e:
        error_code = _make_error_code("telegram", "API")
        _schedule_platform_failure_log("telegram", error_code, "legacy_send_file", e, session_id=session_id)
        await query.edit_message_text(USER_TELEGRAM_ERROR_WITH_CODE.format(error_code=error_code))
        await _cleanup_user_session(user_id, context)
    except Exception as e:
        error_code = _make_error_code("bot", "SEND")
        _schedule_platform_failure_log("bot", error_code, "legacy_send_file", e, session_id=session_id)
        await query.edit_message_text(USER_ERROR_WITH_CODE.format(error_code=error_code))
        await _cleanup_user_session(user_id, context)

async def _send_single_file_legacy_unsafe(
    query: telegram.CallbackQuery, 
    file_path: Path,
    caption_prefix: str = "",
    context: ContextTypes.DEFAULT_TYPE = None,
    max_retries: int = 3
) -> bool:
    """
    Отправляет один файл пользователю с retry логикой.
    
    Args:
        query (telegram.CallbackQuery): Объект колбэк-запроса.
        file_path (Path): Путь к файлу.
        caption_prefix (str, optional): Префикс для подписи к файлу. По умолчанию "".
        context (ContextTypes.DEFAULT_TYPE, optional): Контекст для сохранения в кэш.
        max_retries (int): Максимальное количество попыток отправки.
    """
    last_error: Exception | None = None
    
    for attempt in range(1, max_retries + 1):
        try:
            file_ext = file_path.suffix.lower()
            message = None
            
            if file_ext in ['.mp4', '.webm', '.mkv', '.avi', '.mov']:
                with open(file_path, 'rb') as video_file:
                    message = await query.message.reply_video(
                        video=video_file,
                        caption=None,
                        supports_streaming=True,
                        write_timeout=300,
                        read_timeout=300
                    )
            elif file_ext in ['.mp3', '.m4a', '.wav', '.ogg']:
                with open(file_path, 'rb') as audio_file:
                    message = await query.message.reply_audio(
                        audio=audio_file,
                        caption=None
                    )
            else:
                with open(file_path, 'rb') as document_file:
                    message = await query.message.reply_document(
                        document=document_file,
                        caption=None
                    )

            # === СОХРАНЕНИЕ В КЭШ после успешной отправки ===
            if context and message:
                url = context.user_data.get('url')
                video_info = context.user_data.get('video_info')
                platform = context.user_data.get('platform', 'youtube')
                file_id = None
                file_unique_id = None
                file_size = None
                duration = None

                if message.video:
                    file_id = message.video.file_id
                    file_unique_id = message.video.file_unique_id
                    file_size = message.video.file_size
                    duration = message.video.duration
                elif message.audio:
                    file_id = message.audio.file_id
                    file_unique_id = message.audio.file_unique_id
                    file_size = message.audio.file_size
                    duration = message.audio.duration
                elif message.document:
                    file_id = message.document.file_id
                    file_unique_id = message.document.file_unique_id
                    file_size = message.document.file_size

                if url and file_id:
                    try:
                        cached = CachedVideo(
                            url=url,
                            file_id=file_id,
                            file_unique_id=file_unique_id,
                            platform=platform,
                            format_id='best',
                            cached_at=datetime.now(),
                            file_size=file_size,
                            duration=duration,
                            title=video_info.get('title') if video_info else None,
                        )
                        telegram_cache.set(cached)
                        logger.info("💾 Файл сохранён в кэш: %s -> %s", url, file_id)
                    except Exception as e:
                        logger.error("Ошибка сохранения в кэш: %s", e)

            # Успешная отправка - выходим из цикла
            return True
                
        except (telegram.error.NetworkError, telegram.error.TimedOut) as e:
            last_error = e
            logger.warning(f"Попытка {attempt}/{max_retries} неудачна: {e}")
            if attempt < max_retries:
                await asyncio.sleep(2 ** attempt)
            continue
        except (FileNotFoundError, PermissionError) as e:
            error_code = _make_error_code("file", "ACCESS")
            _schedule_platform_failure_log(
                "file",
                error_code,
                "legacy_send_single_file",
                e,
                url=context.user_data.get('url') if context else None,
                session_id=context.user_data.get('session_token') if context else None,
            )
            keyboard = [[InlineKeyboardButton(BTN_BACK, callback_data="main|back")]]
            await query.edit_message_text(
                USER_FILE_ERROR_WITH_CODE.format(error_code=error_code),
                reply_markup=InlineKeyboardMarkup(keyboard),
            )
            return False
        except telegram.error.BadRequest as e:
            logger.error(f"Неверный запрос при отправке файла {file_path}: {e}", exc_info=True)
            keyboard = [[InlineKeyboardButton(BTN_BACK, callback_data="main|back")]]
            if "file too large" in str(e).lower():
                await query.edit_message_text(ERROR_FILE_TOO_LARGE_TELEGRAM, reply_markup=InlineKeyboardMarkup(keyboard))
            else:
                await query.edit_message_text(TG_SEND_ERROR, reply_markup=InlineKeyboardMarkup(keyboard))
            return False
        except telegram.error.TelegramError as e:
            error_code = _make_error_code("telegram", "API")
            _schedule_platform_failure_log(
                "telegram",
                error_code,
                "legacy_send_single_file",
                e,
                url=context.user_data.get('url') if context else None,
                session_id=context.user_data.get('session_token') if context else None,
            )
            keyboard = [[InlineKeyboardButton(BTN_BACK, callback_data="main|back")]]
            await query.edit_message_text(
                USER_TELEGRAM_ERROR_WITH_CODE.format(error_code=error_code),
                reply_markup=InlineKeyboardMarkup(keyboard),
            )
            return False
        except Exception as e:
            error_code = _make_error_code("bot", "SEND")
            _schedule_platform_failure_log(
                "bot",
                error_code,
                "legacy_send_single_file",
                e,
                url=context.user_data.get('url') if context else None,
                session_id=context.user_data.get('session_token') if context else None,
            )
            keyboard = [[InlineKeyboardButton(BTN_BACK, callback_data="main|back")]]
            await query.edit_message_text(
                USER_ERROR_WITH_CODE.format(error_code=error_code),
                reply_markup=InlineKeyboardMarkup(keyboard),
            )
            return False
    
    # Все попытки исчерпаны
    if last_error:
        error_code = _make_error_code("telegram", "NETWORK")
        _schedule_platform_failure_log(
            "telegram",
            error_code,
            "legacy_send_single_file_retry",
            last_error,
            url=context.user_data.get('url') if context else None,
            session_id=context.user_data.get('session_token') if context else None,
        )
        keyboard = [[InlineKeyboardButton(BTN_BACK, callback_data="main|back")]]
        await query.edit_message_text(
            USER_NETWORK_ERROR_WITH_CODE.format(error_code=error_code),
            reply_markup=InlineKeyboardMarkup(keyboard),
        )
    return False

async def send_file(
    query: telegram.CallbackQuery,
    file_path: Path | str,
    session_token: str,
    session_data: dict,
    context: ContextTypes.DEFAULT_TYPE,
    cache_format_id: str | None = None,
) -> None:
    """Новая версия отправки файла, привязанная к конкретной сессии."""
    user_id = query.from_user.id
    back_markup = _build_back_markup(session_token)
    platform = session_data.get('platform', 'bot')
    url = session_data.get('url')
    try:
        if isinstance(file_path, str) and file_path.startswith("http"):
            await query.edit_message_text(FILE_TOO_LARGE_LINK.format(file_path=file_path))
            await _cleanup_user_session(user_id, context, session_token)
            return

        if isinstance(file_path, Path):
            await query.edit_message_text(FILE_PREPARING)
            await asyncio.sleep(1)
            success = await send_single_file(
                query,
                file_path,
                session_token,
                session_data,
                cache_format_id=cache_format_id,
            )
            if success:
                await query.edit_message_text(FILE_SENT)
                await _cleanup_user_session(user_id, context, session_token)
            return

        logger.error(f"Неожиданный тип file_path в send_file: {type(file_path)}")
        await query.edit_message_text(ERROR_MESSAGE, reply_markup=back_markup)
    except (FileNotFoundError, PermissionError) as e:
        error_code = _make_error_code("file", "ACCESS")
        _schedule_platform_failure_log(
            platform=platform,
            stage="send_file_access",
            url=url,
            error_code=error_code,
            exc=e,
            session_id=session_data.get('session_id'),
        )
        await query.edit_message_text(
            USER_FILE_ERROR_WITH_CODE.format(error_code=error_code),
            reply_markup=back_markup,
        )
    except telegram.error.NetworkError as e:
        error_code = _make_error_code("telegram", "NETWORK")
        _schedule_platform_failure_log(
            platform=platform,
            stage="send_file_network",
            url=url,
            error_code=error_code,
            exc=e,
            session_id=session_data.get('session_id'),
        )
        await query.edit_message_text(USER_NETWORK_ERROR_WITH_CODE.format(error_code=error_code), reply_markup=back_markup)
    except telegram.error.TelegramError as e:
        error_code = _make_error_code("telegram", "API")
        _schedule_platform_failure_log(
            platform=platform,
            stage="send_file_telegram",
            url=url,
            error_code=error_code,
            exc=e,
            session_id=session_data.get('session_id'),
        )
        await query.edit_message_text(
            USER_TELEGRAM_ERROR_WITH_CODE.format(error_code=error_code),
            reply_markup=back_markup,
        )
    except Exception as e:
        error_code = _make_error_code("bot", "SEND")
        _schedule_platform_failure_log(
            platform=platform,
            stage="send_file_unexpected",
            url=url,
            error_code=error_code,
            exc=e,
            session_id=session_data.get('session_id'),
        )
        await query.edit_message_text(
            USER_ERROR_WITH_CODE.format(error_code=error_code),
            reply_markup=back_markup,
        )


async def send_single_file(
    query: telegram.CallbackQuery,
    file_path: Path,
    session_token: str,
    session_data: dict,
    max_retries: int = 3,
    cache_format_id: str | None = None,
) -> bool:
    """Новая версия отправки одного файла с обратной кнопкой для текущей сессии."""
    last_error: Exception | None = None
    back_markup = _build_back_markup(session_token)
    platform = session_data.get('platform', 'bot')
    url = session_data.get('url')

    for attempt in range(1, max_retries + 1):
        try:
            file_ext = file_path.suffix.lower()
            message = None

            if file_ext in ['.mp4', '.webm', '.mkv', '.avi', '.mov']:
                with open(file_path, 'rb') as video_file:
                    message = await query.message.reply_video(
                        video=video_file,
                        caption=None,
                        supports_streaming=True,
                        write_timeout=300,
                        read_timeout=300,
                    )
            elif file_ext in ['.mp3', '.m4a', '.wav', '.ogg']:
                with open(file_path, 'rb') as audio_file:
                    message = await query.message.reply_audio(audio=audio_file, caption=None)
            else:
                with open(file_path, 'rb') as document_file:
                    message = await query.message.reply_document(document=document_file, caption=None)

            # Кэширование file_id для видео, аудио и документов
            if message and url and cache_format_id:
                video_info = session_data.get('video_info')
                file_id = None
                file_unique_id = None
                file_size = None
                duration = None

                if message.video:
                    file_id = message.video.file_id
                    file_unique_id = message.video.file_unique_id
                    file_size = message.video.file_size
                    duration = message.video.duration
                elif message.audio:
                    file_id = message.audio.file_id
                    file_unique_id = message.audio.file_unique_id
                    file_size = message.audio.file_size
                    duration = message.audio.duration
                elif message.document:
                    file_id = message.document.file_id
                    file_unique_id = message.document.file_unique_id
                    file_size = message.document.file_size

                if file_id:
                    try:
                        cached = CachedVideo(
                            url=url,
                            file_id=file_id,
                            file_unique_id=file_unique_id,
                            platform=platform,
                            format_id=cache_format_id,
                            cached_at=datetime.now(),
                            file_size=file_size,
                            duration=duration,
                            title=video_info.get('title') if video_info else None,
                        )
                        telegram_cache.set(cached)
                        logger.info("💾 Файл сохранён в кэш: %s -> %s (key=%s)", url, file_id, cache_format_id)
                    except Exception as e:
                        logger.error("Ошибка сохранения в кэш: %s", e)

            return True
        except (telegram.error.NetworkError, telegram.error.TimedOut) as e:
            last_error = e
            logger.warning(f"Попытка {attempt}/{max_retries} неудачна: {e}")
            if attempt < max_retries:
                await asyncio.sleep(2 ** attempt)
            continue
        except (FileNotFoundError, PermissionError) as e:
            error_code = _make_error_code("file", "ACCESS")
            _schedule_platform_failure_log(
                platform=platform,
                stage="send_single_file_access",
                url=url,
                error_code=error_code,
                exc=e,
                session_id=session_data.get('session_id'),
            )
            await query.edit_message_text(
                USER_FILE_ERROR_WITH_CODE.format(error_code=error_code),
                reply_markup=back_markup,
            )
            return False
        except telegram.error.BadRequest as e:
            logger.error(f"Неверный запрос при отправке файла {file_path}: {e}", exc_info=True)
            if "file too large" in str(e).lower():
                await query.edit_message_text(
                    ERROR_FILE_TOO_LARGE_TELEGRAM,
                    reply_markup=back_markup,
                )
            else:
                await query.edit_message_text(TG_SEND_ERROR, reply_markup=back_markup)
            return False
        except telegram.error.TelegramError as e:
            error_code = _make_error_code("telegram", "API")
            _schedule_platform_failure_log(
                platform=platform,
                stage="send_single_file_telegram",
                url=url,
                error_code=error_code,
                exc=e,
                session_id=session_data.get('session_id'),
            )
            await query.edit_message_text(
                USER_TELEGRAM_ERROR_WITH_CODE.format(error_code=error_code),
                reply_markup=back_markup,
            )
            return False
        except Exception as e:
            error_code = _make_error_code("bot", "UNKNOWN")
            _schedule_platform_failure_log(
                platform=platform,
                stage="send_single_file_unexpected",
                url=url,
                error_code=error_code,
                exc=e,
                session_id=session_data.get('session_id'),
            )
            await query.edit_message_text(TG_SEND_ERROR, reply_markup=back_markup)
            return False

    if last_error:
        error_code = _make_error_code("telegram", "NETWORK")
        _schedule_platform_failure_log(
            platform=platform,
            stage="send_single_file_retry_exhausted",
            url=url,
            error_code=error_code,
            exc=last_error,
            session_id=session_data.get('session_id'),
        )
        await query.edit_message_text(
            USER_NETWORK_ERROR_WITH_CODE.format(error_code=error_code),
            reply_markup=back_markup,
        )
    return False


def format_duration(seconds: int) -> str:
    """
    Форматирует продолжительность из секунд в формат ЧЧ:ММ:СС.
    Args:
        seconds (int): Продолжительность в секундах.
    Returns:
        str: Отформатированная продолжительность.
    """
    minutes, seconds = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    if hours > 0:
        return f"{hours}:{minutes:02d}:{seconds:02d}"
    else:
        return f"{minutes}:{seconds:02d}"

def escape_markdown(text: str) -> str:
    """
    Экранирует специальные символы для Markdown.
    
    Args:
        text (str): Текст для экранирования.
    
    Returns:
        str: Экранированный текст.
    """
    if not text:
        return "N/A"
    
    # Экранируем специальные символы Markdown
    escape_chars = ['*', '_', '[', ']', '(', ')', '~', '`', '>', '#', '+', '-', '=', '|', '{', '}', '.', '!']
    for char in escape_chars:
        text = text.replace(char, f'\\{char}')
    
    return text
