"""
Модуль для работы с Telegram API.
"""

import asyncio
import contextlib
import functools
import io
import traceback
import uuid
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor

import telegram
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

from config import (
    ADMIN_IDS,
    BLOCKING_TASK_TIMEOUT,
    DOWNLOAD_WORKERS,
    MAX_FILE_SIZE,
    TELEGRAM_LOCAL_MODE,
    TEMP_DIR,
)
from utils.logger import setup_logger
from utils.cancellation import CancelledByUser, request_cancellation
from utils.subtitles import (
    SUBTITLE_FORMATS,
    available_subtitle_languages,
    parse_subtitle_choice,
)
from utils.tg_video_choice import (
    list_audio_options,
    list_video_options,
    select_tg_video_format,
)
from utils.analytics_db import (
    init_db as _init_analytics,
    track_user,
    track_event,
    update_last_csi_sent,
    save_csi_rating,
    update_csi_feedback,
)
from utils.youtube_utils import (
    is_valid_youtube_url,
    get_video_info,
    get_available_formats,
    download_video,
    download_audio,
    download_audio_native,
    download_subtitles,
)
from utils.temp_file_manager import create_temp_dir, cleanup_temp_files
from utils.callback_fsm import CallbackEvent, SessionStore
from utils.file_delivery import media_kind_for_suffix
from utils.platform_actions import (
    DIRECT_VIDEO_CACHE_KEY,
    cache_key_for_format_selection,
    cache_key_for_main_action,
)
from utils.public_errors import (
    build_public_error_message,
    classify_internal_error_category,
    youtube_error_code,
)
from utils.url_delivery import (
    HandoffRefusals,
    PhotoPostHandoff,
    UrlHandoff,
    find_format_url,
    plan_url_handoff,
)
import yt_dlp
from messages import (
    WELCOME_MESSAGE,
    HELP_MESSAGE,
    PROCESSING_MESSAGE,
    DOWNLOADING_MESSAGE,
    DOWNLOADING_AUDIO_MESSAGE,
    DOWNLOADING_SUBTITLES_MESSAGE,
    PHOTO_POST_AUDIO_UNAVAILABLE,
    INVALID_URL_MESSAGE,
    ERROR_MESSAGE,
    TOO_LONG_VIDEO_MESSAGE,
    NO_URL_AFTER_COMMAND,
    SESSION_EXPIRED,
    FILE_PREPARING,
    FILE_SENT,
    DOWNLOAD_FORMAT_PROMPT,
    CHOOSE_ANOTHER_FORMAT,
    NO_SUBTITLES_AVAILABLE,
    NO_TG_VIDEO,
    NO_FILESIZE,
    BTN_AUDIO_M4A,
    BTN_TG_VIDEO,
    BTN_MORE,
    TG_SEND_ERROR,
    BTN_BACK,
    BTN_DOWNLOAD_VIDEO,
    BTN_DOWNLOAD_POST,
    BTN_AUDIO_ONLY,
    BTN_SECTION_VIDEO,
    BTN_SECTION_AUDIO,
    BTN_SECTION_SUBTITLES,
    BTN_AUDIO_TRANSCODE,
    CHOOSE_SECTION_MESSAGE,
    CHOOSE_RESOLUTION_MESSAGE,
    CHOOSE_AUDIO_MESSAGE,
    NO_VIDEO_OPTIONS_MESSAGE,
    NO_AUDIO_OPTIONS_MESSAGE,
    BTN_CANCEL,
    CANCELLED_MESSAGE,
    CHOOSE_SUBTITLE_LANGUAGE_MESSAGE,
    CHOOSE_SUBTITLE_FORMAT_MESSAGE,
    NO_SUBTITLE_LANGUAGES_MESSAGE,
    ERROR_FALLBACK,
    ERROR_NETWORK,
    ERROR_FILE_TOO_LARGE_TELEGRAM,
    SUBTITLE_CAPTION,
    SPAM_WARNING,
    USER_ERROR_WITH_CODE,
    USER_NETWORK_ERROR_WITH_CODE,
    USER_FILE_ERROR_WITH_CODE,
    USER_TELEGRAM_ERROR_WITH_CODE,
    CSI_REQUEST_MESSAGE,
    CSI_THANKS_MESSAGE,
    CSI_FEEDBACK_REQUEST,
    CSI_FEEDBACK_THANKS,
)
from utils.tiktok_instagram_utils import (
    is_valid_tiktok_url,
    is_valid_instagram_url,
    is_instagram_story_url,
    get_tiktok_info,
    get_instagram_info,
    is_instagram_audio_url,
    handle_instagram_audio_url,
    PhotoPostAudioMissingError,
)
from utils.rutube_vk_utils import (
    is_valid_rutube_url,
    is_valid_vk_url,
    get_rutube_info,
    get_vk_info,
    get_available_formats_rutube,
    get_available_formats_vk,
    download_rutube_video,
    download_vk_video,
    download_rutube_audio,
    download_vk_audio,
)
from utils.video_cache import telegram_cache, CachedVideo
from utils.cookie_health import check_cookie_health
from datetime import datetime, timezone

logger = setup_logger(__name__)

# Инициализация аналитической БД
_init_analytics()

# Ссылка на экземпляр бота для отправки краш-репортов админам
_bot_instance: telegram.Bot | None = None


def set_bot_instance(bot: telegram.Bot) -> None:
    """Устанавливает ссылку на бота для отправки краш-репортов."""
    global _bot_instance
    _bot_instance = bot


async def send_csi_request(user_id: int, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Отправляет пользователю inline-клавиатуру с оценками 0–10 для CSI."""
    keyboard = []
    row = []
    for i in range(11):
        row.append(InlineKeyboardButton(str(i), callback_data=f"csi|{i}"))
        if len(row) == 6:
            keyboard.append(row)
            row = []
    if row:
        keyboard.append(row)
    reply_markup = InlineKeyboardMarkup(keyboard)
    await context.bot.send_message(
        chat_id=user_id, text=CSI_REQUEST_MESSAGE, reply_markup=reply_markup
    )
    update_last_csi_sent(user_id)


def _format_exception_traceback(exc: BaseException) -> str:
    """Формирует traceback из объекта исключения даже вне активного except-блока."""
    if exc.__traceback__:
        return "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
    return (
        "Traceback unavailable: exception was logged outside the original except block."
    )


def _md_cell(value: object) -> str:
    """Готовит значение к вставке в ячейку markdown-таблицы.

    Вертикальная черта закрывает столбец, а перевод строки — всю строку таблицы,
    поэтому и то и другое обезвреживается: в краш-репорт попадают тексты
    исключений, где встречается любой символ.
    """
    text = str(value).replace("|", "\\|")
    return " ".join(text.split()) or "N/A"


def _md_code_block(text: str, language: str = "text") -> str:
    """Оборачивает текст в ограждённый блок кода, который он не сможет разорвать.

    Забор берётся длиннее самой длинной череды обратных кавычек внутри: в
    traceback попадают строки исходников, а в них кавычки бывают.
    """
    longest_run = 0
    current_run = 0
    for char in text:
        current_run = current_run + 1 if char == "`" else 0
        longest_run = max(longest_run, current_run)
    fence = "`" * max(3, longest_run + 1)
    return f"{fence}{language}\n{text.rstrip()}\n{fence}"


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
    """Отправляет админам из ADMIN_IDS краш-репорт файлом в markdown."""
    if not _bot_instance or not ADMIN_IDS:
        return

    # Ссылка идёт в угловых скобках: так markdown делает её ссылкой, не пытаясь
    # разобрать подчёркивания и звёздочки внутри как разметку. Время — в UTC,
    # как и метки в `logs/bot.log`, чтобы репорт искался в журнале по времени.
    facts = (
        (
            "Время (UTC)",
            _md_cell(datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")),
        ),
        ("Платформа", _md_cell(platform)),
        ("Этап", _md_cell(stage)),
        ("Ссылка", f"<{_md_cell(url)}>" if url else "N/A"),
        ("Сессия", f"`{_md_cell(session_id)}`" if session_id else "N/A"),
        ("Cookies", _md_cell(cookie_status)),
        ("Что с cookies", _md_cell(cookie_summary)),
    )
    report_text = "\n".join(
        [
            f"# 🔴 Краш-репорт — `{error_code}`",
            "",
            "| Поле | Значение |",
            "| --- | --- |",
            *(f"| {name} | {value} |" for name, value in facts),
            "",
            "## Исключение",
            "",
            _md_code_block(f"{type(exc).__name__}: {exc}"),
            "",
            "## Traceback",
            "",
            _md_code_block(_format_exception_traceback(exc), "python"),
            "",
        ]
    )

    for admin_id in ADMIN_IDS:
        try:
            await _bot_instance.send_document(
                chat_id=admin_id,
                document=io.BytesIO(report_text.encode("utf-8")),
                filename=f"crash_{error_code}.md",
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
_DIRECT_VIDEO_CACHE_KEY = DIRECT_VIDEO_CACHE_KEY


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


async def run_blocking(
    func, *args, description: str = "blocking task", session_id: str | None = None
):
    """Запускает sync-функцию в executor с таймаутом.

    `wait_for` отменяет только ожидание — поток в пуле продолжает работать. Для
    скачивания это означает занятый воркер и трафик ради файла, который уже
    никому не отдадут: замерено, что загрузка завершилась через 4 минуты после
    своего таймаута. Поэтому по таймауту сессия помечается отменённой, и
    progress hook обрывает yt-dlp изнутри.

    Args:
        session_id: Сессия задачи. Без неё остановить работу нечем.
    """
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
        if session_id:
            request_cancellation(session_id)
            logger.info(
                "Запрошена отмена сессии %s: задача осталась в пуле после таймаута",
                session_id,
            )
        raise exc


# Простая защита от спама: 4 запросa подряд без паузы -> предупреждение и таймаут 10с
def _check_spam(user_id: int, context: ContextTypes.DEFAULT_TYPE, now: float) -> bool:
    blocked_until = context.user_data.get("spam_blocked_until", 0.0)
    if blocked_until and now < blocked_until:
        return True

    if blocked_until and now >= blocked_until:
        context.user_data.pop("spam_blocked_until", None)

    timestamps: list[float] = context.user_data.get("recent_requests", [])
    timestamps = [t for t in timestamps if now - t < _SPAM_WINDOW_SECONDS]
    timestamps.append(now)
    context.user_data["recent_requests"] = timestamps

    if len(timestamps) >= _SPAM_REQUEST_LIMIT:
        context.user_data["spam_blocked_until"] = now + _SPAM_TIMEOUT_SECONDS
        return True

    return False


def _session_is_disposable(session_id: str | None) -> bool:
    """Сообщает, можно ли вытеснить сессию, не потеряв ничьей работы.

    Признак занятости — файлы в её временном каталоге. Пустой каталог означает
    брошенное меню: формат не выбирали, качать нечего. Непустой означает либо
    идущую загрузку, либо готовый файл, который ещё не отправлен, — и такую
    запись терять нельзя: в ней лежит `session_id`, по которому владелец потом
    удалит файлы.
    """
    if not session_id:
        return True
    directory = TEMP_DIR / session_id
    try:
        return not any(directory.iterdir())
    except FileNotFoundError:
        return True
    except OSError as error:
        # Не смогли посмотреть — считаем занятой: вытеснение обратимо ожиданием,
        # а потеря скачанного файла нет.
        logger.warning(
            "Не удалось проверить каталог сессии %s (%s), сессия сохранена",
            session_id,
            error,
        )
        return False


def _get_session_store(context: ContextTypes.DEFAULT_TYPE) -> dict[str, dict]:
    """Возвращает хранилище активных сессий пользователя."""
    return SessionStore(
        context.user_data,
        key=_SESSION_STORE_KEY,
        max_active=_MAX_ACTIVE_SESSIONS,
        is_disposable=_session_is_disposable,
    ).data


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
    return SessionStore(
        context.user_data,
        key=_SESSION_STORE_KEY,
        max_active=_MAX_ACTIVE_SESSIONS,
        is_disposable=_session_is_disposable,
    ).create(
        url=url,
        video_info=video_info,
        session_id=session_id,
        platform=platform,
        formats=formats,
    )


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
        [
            [
                InlineKeyboardButton(
                    BTN_BACK,
                    callback_data=_make_callback_data(session_token, "main", "back"),
                )
            ]
        ]
    )


def _build_youtube_prompt(video_info: dict) -> str:
    """Текст карточки YouTube-видео с безопасным Markdown."""
    title = escape_markdown(str(video_info.get("title") or "Video"))
    duration = format_duration(int(video_info.get("duration") or 0))
    return DOWNLOAD_FORMAT_PROMPT.format(title=title, duration=duration)


def _build_main_menu(
    platform: str,
    video_info: dict,
    session_token: str,
    formats: dict | None = None,
) -> tuple[str, InlineKeyboardMarkup]:
    """Возвращает текст и клавиатуру главного меню для платформы."""
    formats = formats or {}
    title = escape_markdown(str(video_info.get("title") or "Video"))
    uploader = escape_markdown(str(video_info.get("uploader") or "N/A"))
    duration = format_duration(int(video_info.get("duration") or 0))

    if platform == "tiktok":
        is_photo_post = bool(video_info.get("_nuvio_tiktok_photo_post"))
        keyboard = [
            [
                InlineKeyboardButton(
                    BTN_DOWNLOAD_POST if is_photo_post else BTN_DOWNLOAD_VIDEO,
                    callback_data=_make_callback_data(
                        session_token, "main", "tiktok_download"
                    ),
                )
            ]
        ]
        if not (is_photo_post and not video_info.get("_nuvio_tiktok_audio_url")):
            keyboard.append(
                [
                    InlineKeyboardButton(
                        BTN_AUDIO_ONLY,
                        callback_data=_make_callback_data(
                            session_token, "main", "tiktok_audio"
                        ),
                    )
                ]
            )
        keyboard.append(
            [
                InlineKeyboardButton(
                    BTN_CANCEL,
                    callback_data=_make_callback_data(session_token, "main", "cancel"),
                )
            ]
        )
        if is_photo_post:
            images_count = len(video_info.get("_nuvio_tiktok_images") or [])
            text = f"*{title}*\nАвтор: {uploader}\nКадров: {images_count}\nЗвук: {'есть' if video_info.get('_nuvio_tiktok_audio_url') else 'нет'}\nДлительность: {duration}"
        else:
            text = f"*{title}*\nАвтор: {uploader}\nДлительность: {duration}"
        return text, InlineKeyboardMarkup(keyboard)

    if platform == "instagram":
        is_photo_post = bool(video_info.get("_nuvio_instagram_photo_post"))
        keyboard = [
            [
                InlineKeyboardButton(
                    BTN_DOWNLOAD_POST if is_photo_post else BTN_DOWNLOAD_VIDEO,
                    callback_data=_make_callback_data(
                        session_token, "main", "instagram_download"
                    ),
                )
            ]
        ]
        if not (is_photo_post and not video_info.get("_nuvio_instagram_audio_url")):
            keyboard.append(
                [
                    InlineKeyboardButton(
                        BTN_AUDIO_ONLY,
                        callback_data=_make_callback_data(
                            session_token, "main", "instagram_audio"
                        ),
                    )
                ]
            )
        keyboard.append(
            [
                InlineKeyboardButton(
                    BTN_CANCEL,
                    callback_data=_make_callback_data(session_token, "main", "cancel"),
                )
            ]
        )
        if is_photo_post:
            images_count = len(video_info.get("_nuvio_instagram_images") or [])
            text = f"*{title}*\nАвтор: {uploader}\nКадров: {images_count}\nЗвук: {'есть' if video_info.get('_nuvio_instagram_audio_url') else 'нет'}\nДлительность: {duration}"
        else:
            text = f"*{title}*\nАвтор: {uploader}\nДлительность: {duration}"
        return text, InlineKeyboardMarkup(keyboard)

    if platform == "rutube":
        keyboard = [
            [
                InlineKeyboardButton(
                    BTN_DOWNLOAD_VIDEO,
                    callback_data=_make_callback_data(
                        session_token, "main", "rutube_download"
                    ),
                )
            ],
            [
                InlineKeyboardButton(
                    BTN_AUDIO_ONLY,
                    callback_data=_make_callback_data(
                        session_token, "main", "rutube_audio"
                    ),
                )
            ],
            [
                InlineKeyboardButton(
                    BTN_CANCEL,
                    callback_data=_make_callback_data(session_token, "main", "cancel"),
                )
            ],
        ]
        text = f"*{title}*\nАвтор: {uploader}\nДлительность: {duration}"
        return text, InlineKeyboardMarkup(keyboard)

    if platform == "vk":
        keyboard = [
            [
                InlineKeyboardButton(
                    BTN_DOWNLOAD_VIDEO,
                    callback_data=_make_callback_data(
                        session_token, "main", "vk_download"
                    ),
                )
            ],
            [
                InlineKeyboardButton(
                    BTN_AUDIO_ONLY,
                    callback_data=_make_callback_data(
                        session_token, "main", "vk_audio"
                    ),
                )
            ],
            [
                InlineKeyboardButton(
                    BTN_CANCEL,
                    callback_data=_make_callback_data(session_token, "main", "cancel"),
                )
            ],
        ]
        text = f"*{title}*\nАвтор: {uploader}\nДлительность: {duration}"
        return text, InlineKeyboardMarkup(keyboard)

    keyboard = [
        [
            InlineKeyboardButton(
                BTN_TG_VIDEO,
                callback_data=_make_callback_data(session_token, "main", "tg_video"),
            )
        ]
    ]
    # Кнопка звука рисуется только когда звук есть: у беззвучного видео она
    # раньше приводила к отказу уже после нажатия.
    if formats.get("audio_only"):
        keyboard.append(
            [
                InlineKeyboardButton(
                    BTN_AUDIO_M4A,
                    callback_data=_make_callback_data(
                        session_token, "main", "audio_m4a"
                    ),
                )
            ]
        )
    keyboard.append(
        [
            InlineKeyboardButton(
                BTN_MORE,
                callback_data=_make_callback_data(session_token, "main", "more"),
            )
        ]
    )
    keyboard.append(
        [
            InlineKeyboardButton(
                BTN_CANCEL,
                callback_data=_make_callback_data(session_token, "main", "cancel"),
            )
        ]
    )
    text = _build_youtube_prompt(video_info)
    return text, InlineKeyboardMarkup(keyboard)


def _format_size(size: int) -> str:
    """Размер для подписи кнопки; для неизвестного размера — пустая строка."""
    if size <= 0:
        return ""
    if size >= 1024 * 1024 * 1024:
        return f" · {size / 1024 / 1024 / 1024:.1f} ГБ"
    if size < 1024 * 1024:
        # «0 МБ» на дорожке в несколько сотен килобайт выглядит как ошибка.
        return f" · {size / 1024:.0f} КБ"
    return f" · {size / 1024 / 1024:.0f} МБ"


def _button_row(label: str, session_token: str, scope: str, action: str, value=None):
    """Строка клавиатуры из одной кнопки."""
    return [
        InlineKeyboardButton(
            label,
            callback_data=_make_callback_data(session_token, scope, action, value),
        )
    ]


SUBTITLE_FORMAT_LABELS = {"srt": "SRT", "vtt": "VTT", "txt": "Текст"}


def _build_cancel_markup(session_token: str) -> InlineKeyboardMarkup:
    """Клавиатура из одной кнопки отмены — для экранов ожидания."""
    return InlineKeyboardMarkup([_button_row(BTN_CANCEL, session_token, "main", "cancel")])


def _build_subtitle_language_menu(
    video_info: dict, session_token: str
) -> InlineKeyboardMarkup | None:
    """Меню языков субтитров.

    Returns:
        None, если ни русских, ни английских субтитров у видео нет.
    """
    languages = available_subtitle_languages(video_info)
    if not languages:
        return None

    keyboard = [
        _button_row(
            language.label, session_token, "format", "subs_lang", language.code
        )
        for language in languages
    ]
    keyboard.append(_button_row(BTN_BACK, session_token, "main", "more"))
    return InlineKeyboardMarkup(keyboard)


def _build_subtitle_format_menu(
    language: str, session_token: str
) -> InlineKeyboardMarkup:
    """Меню форматов субтитров для выбранного языка."""
    keyboard = [
        _button_row(
            SUBTITLE_FORMAT_LABELS[subtitle_format],
            session_token,
            "format",
            "subs",
            f"{language}:{subtitle_format}",
        )
        for subtitle_format in SUBTITLE_FORMATS
    ]
    # Назад ведёт к выбору языка, а не в главное меню: иначе каскад теряет смысл.
    keyboard.append(_button_row(BTN_BACK, session_token, "main", "subtitles"))
    return InlineKeyboardMarkup(keyboard)


def _build_more_menu(formats: dict, session_token: str) -> InlineKeyboardMarkup:
    """Разделы расширенного меню.

    Раньше здесь лежал плоский список: до трёх combined, до трёх «без звука», до
    двух аудио, плюс «Лучшее качество», «Лучшее аудио» и MP3. Ограничения
    прятали форматы (из 22 доступных было видно шесть), а дедупликация по тексту
    кнопки скрывала одно разрешение за другим. Теперь выбор идёт по разделам, и
    ни одно доступное разрешение не теряется.
    """
    keyboard = [_button_row(BTN_SECTION_VIDEO, session_token, "main", "video_menu")]
    if formats.get("audio_only"):
        keyboard.append(
            _button_row(BTN_SECTION_AUDIO, session_token, "main", "audio_menu")
        )
    keyboard.append(
        _button_row(BTN_SECTION_SUBTITLES, session_token, "main", "subtitles")
    )
    keyboard.append(_button_row(BTN_BACK, session_token, "main", "back"))
    return InlineKeyboardMarkup(keyboard)


def _build_video_menu(formats: dict, session_token: str) -> InlineKeyboardMarkup | None:
    """Меню разрешений: по одной кнопке на разрешение, от высокого к низкому.

    Returns:
        None, если ни одно разрешение не проходит по лимиту доставки.
    """
    options = list_video_options(
        formats.get("video_only", []),
        formats.get("audio_only", []),
        formats.get("combined", []),
        MAX_FILE_SIZE,
    )
    if not options:
        return None

    keyboard = [
        _button_row(
            f"{option.resolution}p{_format_size(option.size)}",
            session_token,
            "format",
            "combined",
            option.format_id,
        )
        for option in options
    ]
    keyboard.append(_button_row(BTN_BACK, session_token, "main", "more"))
    return InlineKeyboardMarkup(keyboard)


def _build_audio_menu(formats: dict, session_token: str) -> InlineKeyboardMarkup | None:
    """Меню звуковых дорожек: только родные и только пригодные для Telegram.

    Если родной пригодной дорожки нет, предлагается единственный вариант с
    перекодированием — иначе звук у таких видео был бы недоступен вовсе.

    Returns:
        None, если у видео нет звука вообще.
    """
    if not formats.get("audio_only"):
        return None

    options = list_audio_options(formats.get("audio_only", []), MAX_FILE_SIZE)
    if options:
        keyboard = [
            _button_row(
                f"🎵 {option.ext.upper()}{_format_size(option.size)}",
                session_token,
                "format",
                "audio_only",
                option.format_id,
            )
            for option in options
        ]
    else:
        keyboard = [
            _button_row(BTN_AUDIO_TRANSCODE, session_token, "main", "audio_m4a")
        ]
    keyboard.append(_button_row(BTN_BACK, session_token, "main", "more"))
    return InlineKeyboardMarkup(keyboard)


# Telegram гасит отметку активности через пять секунд, поэтому её обновляем чаще.
_CHAT_ACTION_REFRESH_SECONDS = 4

# Сколько подряд неудачных отметок терпим молча. Одна — рядовой сбой на длинной
# выгрузке; серия означает, что шапка чата пуста всё время работы.
_CHAT_ACTION_FAILURES_BEFORE_WARNING = 3


def _chat_action_for(action: str) -> str:
    """Подбирает отметку активности под характер работы."""
    if "audio" in action or action == "subtitles":
        return telegram.constants.ChatAction.UPLOAD_DOCUMENT
    return telegram.constants.ChatAction.UPLOAD_VIDEO


@contextlib.asynccontextmanager
async def _pulsing_chat_action(chat, action: str, enabled: bool = True):
    """Держит отметку «отправляет видео…» в шапке чата на всё время работы.

    Это единственная анимация, доступная боту: рисовать «крутилку» правкой
    текста значило бы запрос на каждый кадр и затирание статусов. Отметка —
    украшение, поэтому её отказ работу не роняет и не прекращает попыток:
    прежний цикл выходил навсегда после первой ошибки, и на длинной отправке
    шапка чата пустела до самого конца.
    """
    if not enabled:
        yield
        return

    async def _pulse() -> None:
        failures = 0
        while True:
            try:
                await chat.send_action(action)
                failures = 0
            except telegram.error.TelegramError as error:
                failures += 1
                # Первый сбой — рядовое дело на длинной выгрузке, поэтому шумим
                # только когда отметка не проходит подряд: это уже означает, что
                # пользователь всё время видит пустую шапку.
                if failures == _CHAT_ACTION_FAILURES_BEFORE_WARNING:
                    logger.warning(
                        "Отметка активности не проходит %s раз подряд: %s",
                        failures,
                        error,
                    )
                else:
                    logger.debug("Отметка активности не отправлена: %s", error)
            await asyncio.sleep(_CHAT_ACTION_REFRESH_SECONDS)

    task = asyncio.create_task(_pulse())
    try:
        yield
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task


def _should_rate_limit_callback(callback_data: str | None) -> bool:
    """Ограничивает только дорогие callback-действия, а не навигацию по меню."""
    if not callback_data:
        return False

    parts = callback_data.split("|")
    if len(parts) < 4 or parts[0] != "s":
        return False

    _, _, scope, action, *_ = parts
    if scope == "format":
        return True
    if scope == "main" and action not in {"more", "back"}:
        return True
    return False


async def safe_edit_message_text(
    query: telegram.CallbackQuery, text: str, **kwargs
) -> bool:
    """Безопасно правит сообщение, отличая «нечего править» от настоящей поломки.

    Два исхода не считаются ошибкой:

    * текст и разметка не изменились — править нечего;
    * сообщения больше нет, пользователь его удалил. Это его право, и работу
      это не отменяет: файл уйдёт отдельным сообщением. Раньше такая правка
      поднимала исключение, обработчик ошибки пытался сообщить о ней той же
      правкой, падал так же, и всё уезжало в глобальный обработчик.

    Returns:
        `True`, если правка прошла, иначе `False`.
    """
    try:
        await query.edit_message_text(text, **kwargs)
        return True
    except telegram.error.BadRequest as e:
        reason = str(e).lower()
        if "message is not modified" in reason:
            logger.debug("edit_message_text пропущен: текст и разметка без изменений")
            return False
        if "message to edit not found" in reason:
            logger.info("Сообщение удалено пользователем — правка статуса пропущена")
            return False
        raise


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

    if error_code in {"NETWORK_TIMEOUT", "MEDIA_FORBIDDEN"}:
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
    return youtube_error_code(error_msg)


def _make_error_code(platform: str, category: str) -> str:
    platform_prefix = {
        "youtube": "YT",
        "tiktok": "TT",
        "instagram": "IG",
        "rutube": "RU",
        "vk": "VK",
        "telegram": "TG",
        "file": "FILE",
        "bot": "BOT",
    }.get(platform, "BOT")
    normalized_category = category.upper()[:8]
    return f"{platform_prefix}-{normalized_category}-{uuid.uuid4().hex[:6].upper()}"


def _classify_internal_error_category(platform: str, error_msg: str) -> str:
    return classify_internal_error_category(platform, error_msg)


def _build_public_error_message(platform: str, error_code: str, error_msg: str) -> str:
    return build_public_error_message(platform, error_code, error_msg)


def _should_notify_admins_platform_failure(
    platform: str, category: str, stage: str
) -> bool:
    """Отделяет ожидаемые пользовательские ограничения платформ от настоящих аварий."""
    if stage.endswith("_timeout") or category in {
        "NETWORK",
        "NETWORK_TIMEOUT",
        "MEDIA_FORBIDDEN",
    }:
        return False
    return True


async def _log_platform_failure(
    *,
    platform: str,
    stage: str,
    url: str | None,
    error_code: str,
    exc: Exception,
    session_id: str | None = None,
) -> None:
    category = _classify_internal_error_category(platform, str(exc))
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

    should_notify_admins = _should_notify_admins_platform_failure(
        platform, category, stage
    )
    log_method = logger.error if should_notify_admins else logger.warning
    log_method(
        "USER_FLOW_FAIL code=%s platform=%s stage=%s session_id=%s url=%s cookie_status=%s cookie_summary=%s error=%s",
        error_code,
        platform,
        stage,
        session_id,
        url,
        cookie_status,
        cookie_summary,
        exc,
        exc_info=should_notify_admins,
    )

    if should_notify_admins:
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
            logger.exception(
                "Failed to emit structured platform error log for %s", error_code
            )

    asyncio.create_task(_runner())


def _cache_format_id_for_main_action(platform: str, action: str) -> str | None:
    """Возвращает cache-key для прямых пользовательских действий."""
    return cache_key_for_main_action(platform, action)


async def _deliver_cached_audio(
    query: telegram.CallbackQuery, url: str, cache_key: str
) -> bool:
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


async def _deliver_cached_video(
    query: telegram.CallbackQuery, url: str, cache_key: str
) -> bool:
    """Отправляет видео из кэша по file_id.

    Returns:
        bool: True, если файл доставлен; False, если записи нет или file_id устарел.
    """
    cached = telegram_cache.get(url, format_id=cache_key)
    if not cached:
        return False

    try:
        await query.message.reply_video(
            video=cached.file_id,
            caption=None,
            supports_streaming=True,
        )
    except telegram.error.BadRequest as e:
        logger.warning("file_id видео устарел (key=%s): %s", cache_key, e)
        telegram_cache.delete_by_file_id(cached.file_id)
        return False

    logger.info("Видео доставлено из кэша (key=%s)", cache_key)
    return True


def _cache_sent_media(
    message: telegram.Message,
    url: str,
    platform: str,
    cache_format_id: str,
    video_info: dict | None = None,
) -> None:
    """Сохраняет file_id отправленного медиа в кэш.

    Доставка уже состоялась, поэтому сбой кэша только логируется: ронять из-за
    него ответ пользователю нельзя.
    """
    media = message.video or message.audio or message.document
    file_id = getattr(media, "file_id", None) if media else None
    if not file_id:
        return

    try:
        telegram_cache.set(
            CachedVideo(
                url=url,
                file_id=file_id,
                file_unique_id=getattr(media, "file_unique_id", None),
                platform=platform,
                format_id=cache_format_id,
                cached_at=datetime.now(),
                file_size=getattr(media, "file_size", None),
                duration=getattr(media, "duration", None),
                title=video_info.get("title") if video_info else None,
            )
        )
        logger.info(
            "💾 Файл сохранён в кэш: %s -> %s (key=%s)", url, file_id, cache_format_id
        )
    except Exception as e:
        logger.error("Ошибка сохранения в кэш: %s", e)


async def _deliver_by_url(
    query: telegram.CallbackQuery,
    plan: UrlHandoff,
    url: str,
    platform: str,
    cache_format_id: str | None = None,
    video_info: dict | None = None,
) -> bool:
    """Отдаёт медиа Telegram прямой ссылкой, минуя диск.

    Returns:
        bool: True, если Telegram ссылку принял; False — если отказал или не
        успел её забрать, и тогда вызывающий код обязан пойти обычным путём
        через скачивание файла.
    """
    size_mb = plan.size / 1024 / 1024
    now = asyncio.get_running_loop().time()
    if _HANDOFF_REFUSALS.is_cooling_down(plan.url, plan.kind, now):
        logger.info(
            "Пропускаю доставку ссылкой (%s): CDN недавно отказал Telegram", plan.kind
        )
        return False

    try:
        match plan.kind:
            case "video":
                message = await query.message.reply_video(
                    video=plan.url, caption=None, supports_streaming=True
                )
            case "audio":
                message = await query.message.reply_audio(audio=plan.url, caption=None)
            case _:
                message = await query.message.reply_photo(photo=plan.url, caption=None)
    except telegram.error.TelegramError as e:
        _HANDOFF_REFUSALS.remember(plan.url, plan.kind, now)
        logger.warning(
            "Telegram не принял ссылку (%s, %.2f МБ): %s — уходим на скачивание",
            plan.kind,
            size_mb,
            e,
        )
        return False

    logger.info("Медиа доставлено ссылкой (%s, %.2f МБ)", plan.kind, size_mb)
    # Кэш file_id рассчитан на видео, аудио и документы; фото-посты в нём не
    # хранятся, поэтому для них запись пропускается.
    if cache_format_id and plan.kind != "photo":
        _cache_sent_media(message, url, platform, cache_format_id, video_info)
    return True


# CDN может отказать инфраструктуре Telegram, оставаясь доступным для нас;
# тогда попытка отдать ссылку — чистая потеря 0.3 с на каждом запросе. Память
# отказов гасит попытки на время и сама возвращает их, когда политика CDN
# меняется. Живёт в процессе: переживать перезапуск ей незачем.
_HANDOFF_REFUSALS = HandoffRefusals()


async def _deliver_photo_post_by_url(
    query: telegram.CallbackQuery, plan: PhotoPostHandoff
) -> bool:
    """Отправляет фото-пост прямыми ссылками.

    Returns:
        bool: True, если пост доставлен; False — если Telegram отказал на первой
        же картинке, когда уйти на обычный путь ещё безопасно.

    Raises:
        telegram.error.TelegramError: отказ после того, как часть поста уже
            ушла. Повторять пост нельзя — пользователь получил бы дубли, —
            поэтому ошибка поднимается наверх к обычной обработке.
    """
    now = asyncio.get_running_loop().time()
    first = plan.images[0]
    if _HANDOFF_REFUSALS.is_cooling_down(first.url, first.kind, now):
        logger.info("Пропускаю фото-пост ссылками: CDN недавно отказал Telegram")
        return False

    for index, image in enumerate(plan.images):
        try:
            await query.message.reply_photo(photo=image.url, caption=None)
        except telegram.error.TelegramError as error:
            if index:
                raise
            _HANDOFF_REFUSALS.remember(image.url, image.kind, now)
            logger.warning(
                "Telegram не принял ссылку на картинку: %s — уходим на скачивание",
                error,
            )
            return False

    if plan.audio:
        await query.message.reply_audio(audio=plan.audio.url, caption=None)

    logger.info("Фото-пост доставлен ссылками: %s кадров", len(plan.images))
    return True


async def _deliver_plan(
    query: telegram.CallbackQuery,
    context: ContextTypes.DEFAULT_TYPE,
    session_token: str,
    session_data: dict,
    plan: UrlHandoff | None,
    cache_format_id: str | None = None,
) -> bool:
    """Доводит доставку по ссылке до конца: отправка, статус, очистка сессии.

    Returns:
        bool: True, если пользователь уже получил медиа. False означает, что
        доставка ссылкой не состоялась и нужно продолжать обычным путём — диск
        при этом ещё не тронут.
    """
    if not plan:
        return False

    delivered = await _deliver_by_url(
        query,
        plan,
        session_data["url"],
        session_data.get("platform", "bot"),
        cache_format_id,
        session_data.get("video_info"),
    )
    if not delivered:
        return False

    await query.edit_message_text(FILE_SENT)
    await _cleanup_user_session(query.from_user.id, context, session_token)
    return True


def _cache_format_id_for_format_selection(
    content_type: str, format_id: str
) -> str | None:
    """Возвращает cache-key для выбранного формата."""
    return cache_key_for_format_selection(content_type, format_id)


async def _begin_processing(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    url: str,
    platform: str,
) -> tuple[telegram.Message, str, str]:
    """Показывает экран ожидания с кнопкой отмены и заводит сессию заранее.

    Сессия создаётся до разбора ссылки намеренно: без неё кнопке отмены не за
    что зацепиться, и передумавшему оставалось бы только ждать.
    """
    session_id = f"{update.effective_user.id}_{uuid.uuid4()}"
    create_temp_dir(session_id)
    session_token = _store_session(
        context,
        url=url,
        video_info={},
        session_id=session_id,
        platform=platform,
        formats={},
    )
    message = await update.message.reply_text(
        PROCESSING_MESSAGE, reply_markup=_build_cancel_markup(session_token)
    )
    return message, session_token, session_id


def _finish_processing(
    context: ContextTypes.DEFAULT_TYPE,
    session_token: str,
    video_info: dict,
    formats: dict,
) -> bool:
    """Дописывает сессию разобранными данными.

    Returns:
        False, если сессии уже нет: пользователь нажал «Отмена», пока шёл
        разбор ссылки, и показывать меню поверх этого нельзя.
    """
    session = _get_session(context, session_token)
    if session is None:
        return False
    session["video_info"] = video_info
    session["formats"] = formats
    return True


async def _cleanup_user_session(
    user_id: int,
    context: ContextTypes.DEFAULT_TYPE,
    session_token: str | None = None,
) -> None:
    """Очищает конкретную сессию пользователя, не затрагивая остальные меню."""
    if session_token:
        session = _get_session_store(context).pop(session_token, None)
        session_id = session.get("session_id") if session else None
        if session_id:
            cleanup_temp_files(session_id)
            logger.info(
                "Временные файлы для сессии %s пользователя %s очищены.",
                session_id,
                user_id,
            )
        logger.info("Сессия %s пользователя %s очищена.", session_token, user_id)
        return

    session_id = context.user_data.get("session_id")
    if session_id:
        cleanup_temp_files(session_id)
        logger.info(
            f"Временные файлы для legacy-сессии {session_id} пользователя {user_id} очищены."
        )
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
        help_message += (
            "\n\n🔐 *Для администратора:* используйте /admin для управления cookies."
        )
    await update.message.reply_text(help_message, parse_mode="Markdown")


async def _get_url_from_context(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> str | None:
    """Извлекает URL из команды /download или текста сообщения."""
    if update.message and update.message.text.startswith("/download"):
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


async def process_url(
    update: Update, context: ContextTypes.DEFAULT_TYPE, url: str | None = None
) -> None:
    """
    Обрабатывает полученный URL от пользователя.

    Args:
        update (Update): Объект обновления Telegram.
        context (ContextTypes.DEFAULT_TYPE): Контекст.
    """
    user_id = update.effective_user.id

    # Перехват текстового отзыва CSI (если пользователь не прислал ссылку)
    if url is None and update.message and update.message.text:
        awaiting_id = context.user_data.get("awaiting_csi_feedback_id")
        if awaiting_id:
            text = update.message.text
            # Если прислана ссылка — сбрасываем ожидание отзыва и обрабатываем как URL
            if "http://" in text or "https://" in text:
                context.user_data.pop("awaiting_csi_feedback_id", None)
            else:
                try:
                    update_csi_feedback(awaiting_id, text)
                    await update.message.reply_text(CSI_FEEDBACK_THANKS)
                except Exception as e:
                    logger.error(f"Ошибка сохранения CSI отзыва: {e}")
                finally:
                    context.user_data.pop("awaiting_csi_feedback_id", None)
                return

    if url is None:
        from utils.cookie_manager import handle_admin_text_input

        if await handle_admin_text_input(update, context):
            return

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
    elif is_valid_rutube_url(url):
        _analytics_platform = "rutube"
    elif is_valid_vk_url(url):
        _analytics_platform = "vk"
    if _analytics_platform:
        track_event(user_id, "download", platform=_analytics_platform, url=url)

    # Проверка YouTube
    if is_valid_youtube_url(url):
        processing_message, session_token, session_id = await _begin_processing(
            update, context, url, "youtube"
        )
        try:
            video_info = await run_blocking(
                get_video_info, url, description="get_video_info"
            )
            formats = get_available_formats(video_info)
            if not _finish_processing(context, session_token, video_info, formats):
                return
            text, reply_markup = _build_main_menu(
                "youtube", video_info, session_token, formats
            )
            await processing_message.edit_text(
                text, reply_markup=reply_markup, parse_mode="Markdown"
            )
        except (yt_dlp.utils.DownloadError, yt_dlp.cookies.CookieLoadError) as e_cookie:
            error_code = _make_error_code(
                "youtube", _classify_internal_error_category("youtube", str(e_cookie))
            )
            _schedule_platform_failure_log(
                platform="youtube",
                stage="process_url",
                url=url,
                error_code=error_code,
                exc=e_cookie,
                session_id=session_id,
            )
            await processing_message.edit_text(
                _build_public_error_message("youtube", error_code, str(e_cookie))
            )
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
                await processing_message.edit_text(
                    USER_ERROR_WITH_CODE.format(error_code=error_code)
                )
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
            await processing_message.edit_text(
                USER_NETWORK_ERROR_WITH_CODE.format(error_code=error_code)
            )
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
            await processing_message.edit_text(
                USER_ERROR_WITH_CODE.format(error_code=error_code)
            )
            if session_id:
                cleanup_temp_files(session_id)
        return
    # Проверка TikTok
    if is_valid_tiktok_url(url):
        processing_message, session_token, session_id = await _begin_processing(
            update, context, url, "tiktok"
        )
        try:
            video_info = await run_blocking(
                get_tiktok_info, url, description="get_tiktok_info"
            )
            from utils.tiktok_instagram_utils import get_available_formats_tiktok

            formats = get_available_formats_tiktok(video_info)
            if not _finish_processing(context, session_token, video_info, formats):
                return
            text, reply_markup = _build_main_menu("tiktok", video_info, session_token)
            await processing_message.edit_text(
                text, reply_markup=reply_markup, parse_mode="Markdown"
            )
        except Exception as e:
            error_code = _make_error_code(
                "tiktok", _classify_internal_error_category("tiktok", str(e))
            )
            _schedule_platform_failure_log(
                platform="tiktok",
                stage="process_url",
                url=url,
                error_code=error_code,
                exc=e,
                session_id=session_id,
            )
            await processing_message.edit_text(
                _build_public_error_message("tiktok", error_code, str(e))
            )
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
            message_text, parse_mode="Markdown", disable_web_page_preview=True
        )
        return
    # Проверка Instagram
    if is_valid_instagram_url(url):
        processing_message, session_token, session_id = await _begin_processing(
            update, context, url, "instagram"
        )
        try:
            video_info = await run_blocking(
                get_instagram_info, url, description="get_instagram_info"
            )
            if not _finish_processing(context, session_token, video_info, {}):
                return
            text, reply_markup = _build_main_menu(
                "instagram", video_info, session_token
            )
            await processing_message.edit_text(
                text, reply_markup=reply_markup, parse_mode="Markdown"
            )
        except Exception as e:
            error_code = _make_error_code(
                "instagram", _classify_internal_error_category("instagram", str(e))
            )
            _schedule_platform_failure_log(
                platform="instagram",
                stage="process_url",
                url=url,
                error_code=error_code,
                exc=e,
                session_id=session_id,
            )
            await processing_message.edit_text(
                _build_public_error_message("instagram", error_code, str(e))
            )
            if session_id:
                cleanup_temp_files(session_id)
        return
    # Проверка Rutube
    if is_valid_rutube_url(url):
        processing_message, session_token, session_id = await _begin_processing(
            update, context, url, "rutube"
        )
        try:
            video_info = await run_blocking(
                get_rutube_info, url, description="get_rutube_info"
            )
            formats = get_available_formats_rutube(video_info)
            if not _finish_processing(context, session_token, video_info, formats):
                return
            text, reply_markup = _build_main_menu("rutube", video_info, session_token)
            await processing_message.edit_text(
                text, reply_markup=reply_markup, parse_mode="Markdown"
            )
        except Exception as e:
            error_code = _make_error_code(
                "rutube", _classify_internal_error_category("rutube", str(e))
            )
            _schedule_platform_failure_log(
                platform="rutube",
                stage="process_url",
                url=url,
                error_code=error_code,
                exc=e,
                session_id=session_id,
            )
            await processing_message.edit_text(
                _build_public_error_message("rutube", error_code, str(e))
            )
            if session_id:
                cleanup_temp_files(session_id)
        return
    # Проверка VK
    if is_valid_vk_url(url):
        processing_message, session_token, session_id = await _begin_processing(
            update, context, url, "vk"
        )
        try:
            video_info = await run_blocking(get_vk_info, url, description="get_vk_info")
            formats = get_available_formats_vk(video_info)
            if not _finish_processing(context, session_token, video_info, formats):
                return
            text, reply_markup = _build_main_menu("vk", video_info, session_token)
            await processing_message.edit_text(
                text, reply_markup=reply_markup, parse_mode="Markdown"
            )
        except Exception as e:
            error_code = _make_error_code(
                "vk", _classify_internal_error_category("vk", str(e))
            )
            _schedule_platform_failure_log(
                platform="vk",
                stage="process_url",
                url=url,
                error_code=error_code,
                exc=e,
                session_id=session_id,
            )
            await processing_message.edit_text(
                _build_public_error_message("vk", error_code, str(e))
            )
            if session_id:
                cleanup_temp_files(session_id)
        return
    # Если не подходит ни один из вариантов
    await update.message.reply_text(INVALID_URL_MESSAGE)


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

    formats = session_data.get("formats", {})
    url = session_data["url"]
    session_id = session_data["session_id"]
    platform = session_data.get("platform", "youtube")
    back_markup = _build_back_markup(session_token)
    # Пока идёт скачивание, отмена — единственное осмысленное действие.
    cancel_markup = _build_cancel_markup(session_token)

    match action:
        case "tiktok_download":
            is_photo_post = bool(
                session_data.get("video_info", {}).get("_nuvio_tiktok_photo_post")
            )
            if is_photo_post:
                await _send_photo_post_assets(
                    query, session_token, session_data, context
                )
                return

            # Проверяем кэш перед скачиванием
            cache_key = (
                None
                if is_photo_post
                else _cache_format_id_for_main_action("tiktok", "tiktok_download")
            )
            if cache_key:
                cached = telegram_cache.get(url, format_id=cache_key)
                if cached:
                    try:
                        await query.message.reply_video(
                            video=cached.file_id,
                            caption=None,
                            supports_streaming=True,
                        )
                        logger.info(
                            "TikTok видео доставлено из кэша (key=%s)", cache_key
                        )
                        await query.edit_message_text(FILE_SENT)
                        await _cleanup_user_session(user_id, context, session_token)
                        return
                    except telegram.error.BadRequest as e:
                        logger.warning("file_id устарел (key=%s): %s", cache_key, e)
                        telegram_cache.delete_by_file_id(cached.file_id)

            await safe_edit_message_text(
                query, DOWNLOADING_MESSAGE, reply_markup=cancel_markup
            )
            from utils.tiktok_instagram_utils import (
                download_tiktok_video,
                resolve_tiktok_video_handoff,
            )

            # Ролик до 20 МБ Telegram забирает по ссылке сам — это дешевле, чем
            # скачать его к себе и выгрузить обратно. При отказе идём ниже
            # обычным путём: резолвер будет вызван повторно, но только в этом
            # редком случае.
            plan = await run_blocking(
                resolve_tiktok_video_handoff,
                url,
                description="resolve_tiktok_video_handoff",
            )
            if await _deliver_plan(
                query, context, session_token, session_data, plan, cache_key
            ):
                return

            try:
                file_path = await run_blocking(
                    download_tiktok_video,
                    url,
                    session_id,
                    None,
                    False,
                    session_data.get("video_info"),
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
                    cache_format_id=_cache_format_id_for_main_action(
                        "tiktok", "tiktok_download"
                    ),
                )
            except Exception as e:
                error_code = _make_error_code(
                    "tiktok", _classify_internal_error_category("tiktok", str(e))
                )
                _schedule_platform_failure_log(
                    platform="tiktok",
                    stage="download_video",
                    url=url,
                    error_code=error_code,
                    exc=e,
                    session_id=session_id,
                )
                await query.edit_message_text(
                    _build_public_error_message("tiktok", error_code, str(e))
                )
                await _cleanup_user_session(user_id, context, session_token)
            return

        case "tiktok_audio":
            cache_key = _cache_format_id_for_main_action("tiktok", "tiktok_audio")
            if cache_key and await _deliver_cached_audio(query, url, cache_key):
                await query.edit_message_text(FILE_SENT)
                await _cleanup_user_session(user_id, context, session_token)
                return

            await safe_edit_message_text(
                query, DOWNLOADING_AUDIO_MESSAGE, reply_markup=cancel_markup
            )
            from utils.tiktok_instagram_utils import (
                download_tiktok_audio,
                resolve_tiktok_audio_handoff,
            )

            plan = await run_blocking(
                resolve_tiktok_audio_handoff,
                url,
                description="resolve_tiktok_audio_handoff",
            )
            if await _deliver_plan(
                query, context, session_token, session_data, plan, cache_key
            ):
                return

            try:
                file_path = await run_blocking(
                    download_tiktok_audio,
                    url,
                    session_id,
                    None,
                    False,
                    session_data.get("video_info"),
                    description="download_tiktok_audio",
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
                    cache_format_id=cache_key,
                )
            except PhotoPostAudioMissingError:
                await query.edit_message_text(
                    PHOTO_POST_AUDIO_UNAVAILABLE, reply_markup=back_markup
                )
                await _cleanup_user_session(user_id, context, session_token)
            except Exception as e:
                error_code = _make_error_code(
                    "tiktok", _classify_internal_error_category("tiktok", str(e))
                )
                _schedule_platform_failure_log(
                    platform="tiktok",
                    stage="download_audio",
                    url=url,
                    error_code=error_code,
                    exc=e,
                    session_id=session_id,
                )
                await query.edit_message_text(
                    _build_public_error_message("tiktok", error_code, str(e))
                )
                await _cleanup_user_session(user_id, context, session_token)
            return

        case "instagram_download":
            is_photo_post = bool(
                session_data.get("video_info", {}).get("_nuvio_instagram_photo_post")
            )
            if is_photo_post:
                await _send_photo_post_assets(
                    query, session_token, session_data, context
                )
                return

            # Проверяем кэш перед скачиванием
            cache_key = (
                None
                if is_photo_post
                else _cache_format_id_for_main_action("instagram", "instagram_download")
            )
            if cache_key:
                cached = telegram_cache.get(url, format_id=cache_key)
                if cached:
                    try:
                        await query.message.reply_video(
                            video=cached.file_id,
                            caption=None,
                            supports_streaming=True,
                        )
                        logger.info(
                            "Instagram видео доставлено из кэша (key=%s)", cache_key
                        )
                        await query.edit_message_text(FILE_SENT)
                        await _cleanup_user_session(user_id, context, session_token)
                        return
                    except telegram.error.BadRequest as e:
                        logger.warning("file_id устарел (key=%s): %s", cache_key, e)
                        telegram_cache.delete_by_file_id(cached.file_id)

            await safe_edit_message_text(
                query, DOWNLOADING_MESSAGE, reply_markup=cancel_markup
            )
            from utils.tiktok_instagram_utils import (
                download_instagram_video,
                resolve_instagram_video_handoff,
            )

            plan = await run_blocking(
                resolve_instagram_video_handoff,
                url,
                description="resolve_instagram_video_handoff",
            )
            if await _deliver_plan(
                query, context, session_token, session_data, plan, cache_key
            ):
                return

            try:
                file_path = await run_blocking(
                    download_instagram_video,
                    url,
                    session_id,
                    None,
                    False,
                    session_data.get("video_info"),
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
                    cache_format_id=_cache_format_id_for_main_action(
                        "instagram", "instagram_download"
                    ),
                )
            except Exception as e:
                if "фото-пост нужно отправлять" in str(e).lower():
                    await _send_photo_post_assets(
                        query, session_token, session_data, context
                    )
                    return
                error_code = _make_error_code(
                    "instagram", _classify_internal_error_category("instagram", str(e))
                )
                _schedule_platform_failure_log(
                    platform="instagram",
                    stage="download_video",
                    url=url,
                    error_code=error_code,
                    exc=e,
                    session_id=session_id,
                )
                await query.edit_message_text(
                    _build_public_error_message("instagram", error_code, str(e))
                )
                await _cleanup_user_session(user_id, context, session_token)
            return

        case "instagram_audio":
            cache_key = _cache_format_id_for_main_action(
                "instagram", "instagram_audio"
            )
            if cache_key and await _deliver_cached_audio(query, url, cache_key):
                await query.edit_message_text(FILE_SENT)
                await _cleanup_user_session(user_id, context, session_token)
                return

            await safe_edit_message_text(
                query, DOWNLOADING_AUDIO_MESSAGE, reply_markup=cancel_markup
            )
            from utils.tiktok_instagram_utils import download_instagram_audio

            try:
                file_path = await run_blocking(
                    download_instagram_audio,
                    url,
                    session_id,
                    None,
                    False,
                    session_data.get("video_info"),
                    description="download_instagram_audio",
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
                    cache_format_id=cache_key,
                )
            except PhotoPostAudioMissingError:
                await query.edit_message_text(
                    PHOTO_POST_AUDIO_UNAVAILABLE, reply_markup=back_markup
                )
                await _cleanup_user_session(user_id, context, session_token)
            except Exception as e:
                error_code = _make_error_code(
                    "instagram", _classify_internal_error_category("instagram", str(e))
                )
                _schedule_platform_failure_log(
                    platform="instagram",
                    stage="download_audio",
                    url=url,
                    error_code=error_code,
                    exc=e,
                    session_id=session_id,
                )
                await query.edit_message_text(
                    _build_public_error_message("instagram", error_code, str(e))
                )
                await _cleanup_user_session(user_id, context, session_token)
            return

        case "rutube_download":
            cache_key = _cache_format_id_for_main_action("rutube", "rutube_download")
            if cache_key:
                cached = telegram_cache.get(url, format_id=cache_key)
                if cached:
                    try:
                        await query.message.reply_video(
                            video=cached.file_id,
                            caption=None,
                            supports_streaming=True,
                        )
                        logger.info(
                            "Rutube видео доставлено из кэша (key=%s)", cache_key
                        )
                        await query.edit_message_text(FILE_SENT)
                        await _cleanup_user_session(user_id, context, session_token)
                        return
                    except telegram.error.BadRequest as e:
                        logger.warning("file_id устарел (key=%s): %s", cache_key, e)
                        telegram_cache.delete_by_file_id(cached.file_id)

            await safe_edit_message_text(
                query, DOWNLOADING_MESSAGE, reply_markup=cancel_markup
            )
            try:
                file_path = await run_blocking(
                    download_rutube_video,
                    url,
                    session_id,
                    description="download_rutube_video",
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
                    cache_format_id=_cache_format_id_for_main_action(
                        "rutube", "rutube_download"
                    ),
                )
            except Exception as e:
                error_code = _make_error_code(
                    "rutube", _classify_internal_error_category("rutube", str(e))
                )
                _schedule_platform_failure_log(
                    platform="rutube",
                    stage="download_video",
                    url=url,
                    error_code=error_code,
                    exc=e,
                    session_id=session_id,
                )
                await query.edit_message_text(
                    _build_public_error_message("rutube", error_code, str(e))
                )
                await _cleanup_user_session(user_id, context, session_token)
            return

        case "rutube_audio":
            cache_key = _cache_format_id_for_main_action("rutube", "rutube_audio")
            if cache_key and await _deliver_cached_audio(query, url, cache_key):
                await query.edit_message_text(FILE_SENT)
                await _cleanup_user_session(user_id, context, session_token)
                return

            await safe_edit_message_text(
                query, DOWNLOADING_AUDIO_MESSAGE, reply_markup=cancel_markup
            )
            try:
                file_path = await run_blocking(
                    download_rutube_audio,
                    url,
                    session_id,
                    description="download_rutube_audio",
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
                    cache_format_id=cache_key,
                )
            except Exception as e:
                error_code = _make_error_code(
                    "rutube", _classify_internal_error_category("rutube", str(e))
                )
                _schedule_platform_failure_log(
                    platform="rutube",
                    stage="download_audio",
                    url=url,
                    error_code=error_code,
                    exc=e,
                    session_id=session_id,
                )
                await query.edit_message_text(
                    _build_public_error_message("rutube", error_code, str(e))
                )
                await _cleanup_user_session(user_id, context, session_token)
            return

        case "vk_download":
            cache_key = _cache_format_id_for_main_action("vk", "vk_download")
            if cache_key:
                cached = telegram_cache.get(url, format_id=cache_key)
                if cached:
                    try:
                        await query.message.reply_video(
                            video=cached.file_id,
                            caption=None,
                            supports_streaming=True,
                        )
                        logger.info("VK видео доставлено из кэша (key=%s)", cache_key)
                        await query.edit_message_text(FILE_SENT)
                        await _cleanup_user_session(user_id, context, session_token)
                        return
                    except telegram.error.BadRequest as e:
                        logger.warning("file_id устарел (key=%s): %s", cache_key, e)
                        telegram_cache.delete_by_file_id(cached.file_id)

            await safe_edit_message_text(
                query, DOWNLOADING_MESSAGE, reply_markup=cancel_markup
            )
            try:
                file_path = await run_blocking(
                    download_vk_video,
                    url,
                    session_id,
                    description="download_vk_video",
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
                    cache_format_id=_cache_format_id_for_main_action(
                        "vk", "vk_download"
                    ),
                )
            except Exception as e:
                error_code = _make_error_code(
                    "vk", _classify_internal_error_category("vk", str(e))
                )
                _schedule_platform_failure_log(
                    platform="vk",
                    stage="download_video",
                    url=url,
                    error_code=error_code,
                    exc=e,
                    session_id=session_id,
                )
                await query.edit_message_text(
                    _build_public_error_message("vk", error_code, str(e))
                )
                await _cleanup_user_session(user_id, context, session_token)
            return

        case "vk_audio":
            cache_key = _cache_format_id_for_main_action("vk", "vk_audio")
            if cache_key and await _deliver_cached_audio(query, url, cache_key):
                await query.edit_message_text(FILE_SENT)
                await _cleanup_user_session(user_id, context, session_token)
                return

            await safe_edit_message_text(
                query, DOWNLOADING_AUDIO_MESSAGE, reply_markup=cancel_markup
            )
            try:
                file_path = await run_blocking(
                    download_vk_audio,
                    url,
                    session_id,
                    description="download_vk_audio",
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
                    cache_format_id=cache_key,
                )
            except Exception as e:
                error_code = _make_error_code(
                    "vk", _classify_internal_error_category("vk", str(e))
                )
                _schedule_platform_failure_log(
                    platform="vk",
                    stage="download_audio",
                    url=url,
                    error_code=error_code,
                    exc=e,
                    session_id=session_id,
                )
                await query.edit_message_text(
                    _build_public_error_message("vk", error_code, str(e))
                )
                await _cleanup_user_session(user_id, context, session_token)
            return

        case "audio_m4a":
            cache_key = _cache_format_id_for_main_action("youtube", "audio_m4a")
            if cache_key and await _deliver_cached_audio(query, url, cache_key):
                await query.edit_message_text(FILE_SENT)
                await _cleanup_user_session(user_id, context, session_token)
                return

            audio_only = formats.get("audio_only", [])
            native_audio = None
            for ext in ["m4a", "mp3", "ogg"]:
                native_audio = next(
                    (f for f in audio_only if f.get("ext") == ext), None
                )
                if native_audio:
                    logger.info(f"Найден нативный аудио формат: {ext}")
                    break

            if not native_audio and audio_only:
                logger.warning(
                    "Нативные форматы не найдены. Доступные: %s. Конвертируем в m4a.",
                    [f.get("ext") for f in audio_only],
                )
                await safe_edit_message_text(
                query, DOWNLOADING_AUDIO_MESSAGE, reply_markup=cancel_markup
            )
                file_path = await run_blocking(
                    functools.partial(download_audio, preferred_codec="m4a"),
                    url,
                    "bestaudio",
                    session_id,
                    description="download_audio_bestaudio",
                )
            elif native_audio:
                await safe_edit_message_text(
                query, DOWNLOADING_AUDIO_MESSAGE, reply_markup=cancel_markup
            )
                file_path = await run_blocking(
                    download_audio_native,
                    url,
                    native_audio["format_id"],
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

            await send_file(
                query,
                file_path,
                session_token,
                session_data,
                context,
                cache_format_id=cache_key,
            )
            return

        case "tg_video":
            cache_key = _cache_format_id_for_main_action("youtube", "tg_video")
            if cache_key and await _deliver_cached_video(query, url, cache_key):
                await query.edit_message_text(FILE_SENT)
                await _cleanup_user_session(user_id, context, session_token)
                return

            combined = formats.get("combined", [])
            tg_video = None

            logger.info("Доступные combined форматы для tg_video:")
            for i, fmt in enumerate(combined):
                logger.info(
                    "  %s: %s - %sp - %s - размер: %s байт",
                    i,
                    fmt.get("format_id"),
                    fmt.get("height"),
                    fmt.get("ext"),
                    fmt.get("filesize"),
                )

            video_only = formats.get("video_only", [])
            audio_only = formats.get("audio_only", [])
            # Бюджет берётся из режима доставки: 2000 МБ через локальный Bot API,
            # 50 МБ через облачный. Прежние 35 + 15 МБ были захардкожены под
            # облачный лимит и обесценивали локальный сервер.
            choice = select_tg_video_format(
                video_only, audio_only, combined, MAX_FILE_SIZE
            )

            if choice:
                tg_video = {
                    "format_id": choice.format_id,
                    "height": choice.height,
                    "ext": choice.ext,
                    "type": choice.kind,
                }
                logger.info(
                    "Выбран формат для Telegram: %s - %sp - %.1f МБ из бюджета %.0f МБ",
                    choice.format_id,
                    choice.height,
                    choice.total_size / 1024 / 1024,
                    MAX_FILE_SIZE / 1024 / 1024,
                )

                await safe_edit_message_text(
                query, DOWNLOADING_MESSAGE, reply_markup=cancel_markup
            )

                # Готовый файл до 20 МБ Telegram забирает по ссылке сам.
                # Пары «видео + аудио» так отдать нельзя: их ещё нужно склеить
                # FFmpeg, поэтому ссылка ищется только для combined-формата.
                if choice.kind == "combined":
                    plan = plan_url_handoff(
                        find_format_url(
                            session_data.get("video_info"), choice.format_id
                        ),
                        "video",
                        choice.total_size,
                    )
                    if await _deliver_plan(
                        query,
                        context,
                        session_token,
                        session_data,
                        plan,
                        _cache_format_id_for_main_action("youtube", "tg_video"),
                    ):
                        return

                try:
                    file_path = await download_content(
                        url, tg_video["format_id"], session_id, "combined"
                    )
                except Exception as e:
                    error_code = _youtube_error_code(str(e))
                    logger.warning(
                        "YT_DL_STAGE_FAIL code=%s stage=tg_video_manual_combined format_id=%s url=%s error=%s",
                        error_code,
                        tg_video["format_id"],
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
                        cache_format_id=_cache_format_id_for_main_action(
                            "youtube", "tg_video"
                        ),
                    )
                    return
                tg_video = None

            if not tg_video:
                # Форматы без известного размера сверить с бюджетом нельзя,
                # поэтому select_tg_video_format их не рассматривает. Берём
                # осторожно — из нижней трети по высоте, чтобы скачанное с
                # большей вероятностью прошло по лимиту доставки.
                formats_without_size = [
                    fmt for fmt in combined if fmt.get("filesize") is None
                ]
                if formats_without_size:
                    formats_without_size.sort(key=lambda x: x.get("height", 0))
                    tg_video = formats_without_size[len(formats_without_size) // 3]
                    logger.info(
                        "Выбран резервный формат (размер неизвестен): %s - %sp",
                        tg_video.get("format_id"),
                        tg_video.get("height"),
                    )

            if tg_video:
                await safe_edit_message_text(
                query, DOWNLOADING_MESSAGE, reply_markup=cancel_markup
            )
                file_path = await download_content(
                    url, tg_video["format_id"], session_id, "combined"
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
                    cache_format_id=_cache_format_id_for_main_action(
                        "youtube", "tg_video"
                    ),
                )
                return

            if any(fmt.get("filesize") is None for fmt in combined):
                await safe_edit_message_text(
                    query, NO_FILESIZE, reply_markup=back_markup
                )
            else:
                await safe_edit_message_text(
                    query, NO_TG_VIDEO, reply_markup=back_markup
                )
            return

        case "more":
            await safe_edit_message_text(
                query,
                CHOOSE_SECTION_MESSAGE,
                reply_markup=_build_more_menu(formats, session_token),
            )
            return

        case "video_menu":
            markup = _build_video_menu(formats, session_token)
            if markup is None:
                await safe_edit_message_text(
                    query, NO_VIDEO_OPTIONS_MESSAGE, reply_markup=back_markup
                )
                return
            await safe_edit_message_text(
                query, CHOOSE_RESOLUTION_MESSAGE, reply_markup=markup
            )
            return

        case "audio_menu":
            markup = _build_audio_menu(formats, session_token)
            if markup is None:
                await safe_edit_message_text(
                    query, NO_AUDIO_OPTIONS_MESSAGE, reply_markup=back_markup
                )
                return
            await safe_edit_message_text(
                query, CHOOSE_AUDIO_MESSAGE, reply_markup=markup
            )
            return

        case "subtitles":
            markup = _build_subtitle_language_menu(
                session_data.get("video_info") or {}, session_token
            )
            if markup is None:
                await safe_edit_message_text(
                    query, NO_SUBTITLE_LANGUAGES_MESSAGE, reply_markup=back_markup
                )
                return
            await safe_edit_message_text(
                query, CHOOSE_SUBTITLE_LANGUAGE_MESSAGE, reply_markup=markup
            )
            return

        case "cancel":
            # Отмена обязана останавливать саму работу, а не прятать результат:
            # признак читает progress hook yt-dlp и прерывает загрузку.
            request_cancellation(session_id)
            await safe_edit_message_text(query, CANCELLED_MESSAGE)
            await _cleanup_user_session(user_id, context, session_token)
            return

        case "back":
            text, reply_markup = _build_main_menu(
                platform, session_data["video_info"], session_token, formats
            )
            await safe_edit_message_text(
                query,
                text,
                reply_markup=reply_markup,
                parse_mode="Markdown",
            )
            return

        case _:
            await query.edit_message_text(ERROR_MESSAGE)
            await _cleanup_user_session(user_id, context, session_token)
            return


async def _download_and_send_subtitles(
    query: telegram.CallbackQuery,
    context: ContextTypes.DEFAULT_TYPE,
    user_id: int,
    session_token: str,
    session_data: dict,
    choice: str,
) -> None:
    """Скачивает субтитры выбранного языка и формата и отправляет их."""
    back_markup = _build_back_markup(session_token)
    parsed = parse_subtitle_choice(choice)
    if not parsed:
        # Значение пришло из callback_data, то есть от пользователя: молча
        # доверять ему нельзя.
        logger.warning("Некорректный выбор субтитров: %s", choice)
        await safe_edit_message_text(query, ERROR_MESSAGE, reply_markup=back_markup)
        return

    language, subtitle_format = parsed
    await safe_edit_message_text(
        query,
        DOWNLOADING_SUBTITLES_MESSAGE,
        reply_markup=_build_cancel_markup(session_token),
    )
    try:
        subtitle_file = await run_blocking(
            download_subtitles,
            session_data["url"],
            session_data["session_id"],
            language,
            subtitle_format,
            description="download_subtitles",
        )
    except Exception as e:
        logger.error(f"Ошибка скачивания субтитров: {e}", exc_info=True)
        await safe_edit_message_text(
            query, NO_SUBTITLES_AVAILABLE, reply_markup=back_markup
        )
        return

    if not (subtitle_file and subtitle_file.exists()):
        await safe_edit_message_text(
            query, NO_SUBTITLES_AVAILABLE, reply_markup=back_markup
        )
        return

    await safe_edit_message_text(query, FILE_PREPARING)
    with open(subtitle_file, "rb") as handle:
        await query.message.reply_document(
            document=handle,
            caption=f"{SUBTITLE_CAPTION} · {language.upper()} · {subtitle_format.upper()}",
        )
    await safe_edit_message_text(query, FILE_SENT)
    subtitle_file.unlink(missing_ok=True)
    await _cleanup_user_session(user_id, context, session_token)


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

    url = session_data["url"]
    session_id = session_data["session_id"]
    formats = session_data.get("formats", {})
    cancel_markup = _build_cancel_markup(session_token)

    if content_type == "subs_lang":
        await safe_edit_message_text(
            query,
            CHOOSE_SUBTITLE_FORMAT_MESSAGE,
            reply_markup=_build_subtitle_format_menu(format_id, session_token),
        )
        return

    if content_type == "subs":
        await _download_and_send_subtitles(
            query, context, user_id, session_token, session_data, format_id
        )
        return

    if content_type == "audio_only":
        await safe_edit_message_text(
            query, DOWNLOADING_AUDIO_MESSAGE, reply_markup=cancel_markup
        )
    else:
        await safe_edit_message_text(
            query, DOWNLOADING_MESSAGE, reply_markup=cancel_markup
        )

    try:
        file_path = None
        cache_format_id = _cache_format_id_for_format_selection(content_type, format_id)
        if cache_format_id and await _deliver_cached_video(
            query, url, cache_format_id
        ):
            await query.edit_message_text(FILE_SENT)
            await _cleanup_user_session(user_id, context, session_token)
            return

        match content_type:
            case "combined":
                selected = next(
                    (
                        fmt
                        for fmt in formats.get("combined", [])
                        if str(fmt.get("format_id")) == format_id
                    ),
                    None,
                )
                plan = plan_url_handoff(
                    find_format_url(session_data.get("video_info"), format_id),
                    "video",
                    (selected or {}).get("filesize"),
                )
                if await _deliver_plan(
                    query, context, session_token, session_data, plan, cache_format_id
                ):
                    return

                file_path = await download_content(
                    url, format_id, session_id, "combined"
                )
            case "audio_only":
                file_path = await download_content(
                    url, format_id, session_id, "audio_only"
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
            cache_format_id=cache_format_id,
        )
    except Exception as e:
        e.add_note(f"user_id={user_id}, url={url}, session_id={session_id}")
        error_code = _make_error_code(
            "youtube", _classify_internal_error_category("youtube", str(e))
        )
        _schedule_platform_failure_log(
            platform="youtube",
            stage="format_download",
            url=url,
            error_code=error_code,
            exc=e,
            session_id=session_id,
        )
        await query.edit_message_text(
            _build_public_error_message("youtube", error_code, str(e))
        )
        await _cleanup_user_session(user_id, context, session_token)


async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Новая версия callback-обработчика с независимыми пользовательскими сессиями."""
    query = update.callback_query
    if not query or not query.data:
        return

    user_id = update.effective_user.id
    session_token: str | None = None
    now = asyncio.get_running_loop().time()

    expensive = _should_rate_limit_callback(query.data)
    if expensive and _check_spam(user_id, context, now):
        await query.answer(text=SPAM_WARNING, show_alert=False)
        return

    try:
        await query.answer()
    except telegram.error.TelegramError:
        logger.debug("Не удалось подтвердить callback, продолжаем обработку")

    logger.info(f"Получен колбэк от пользователя {user_id}: {query.data}")

    try:
        event = CallbackEvent.parse(query.data)
        if event and event.scope == "main" and event.session_token:
            session_token = event.session_token
            # Скачивание и отправка идут секунды: пока они идут, в шапке чата
            # держится отметка активности — иначе пользователь смотрит в
            # неподвижный текст и не понимает, жив ли бот.
            async with _pulsing_chat_action(
                query.message.chat, _chat_action_for(event.action), expensive
            ):
                await _handle_main_callback(
                    query,
                    context,
                    user_id,
                    session_token,
                    event.action,
                )
        elif (
            event
            and event.scope == "format"
            and event.session_token
            and event.value
        ):
            session_token = event.session_token
            async with _pulsing_chat_action(
                query.message.chat, _chat_action_for(event.action), expensive
            ):
                await _handle_format_callback(
                    query,
                    context,
                    user_id,
                    session_token,
                    event.action,
                    event.value,
                )
        elif event and event.scope == "csi" and event.value:
            rating = int(event.value)
            csi_id = save_csi_rating(user_id, rating)
            await safe_edit_message_text(query, CSI_THANKS_MESSAGE)
            if rating < 7:
                context.user_data["awaiting_csi_feedback_id"] = csi_id
                await context.bot.send_message(
                    chat_id=user_id,
                    text=CSI_FEEDBACK_REQUEST,
                )
        elif query.data.startswith("csi|"):
            try:
                rating = int(query.data.split("|", maxsplit=1)[1])
                await query.answer(
                    "Оценка должна быть от 0 до 10"
                    if not 0 <= rating <= 10
                    else "Некорректная оценка"
                )
            except ValueError:
                await query.answer("Некорректная оценка")
        else:
            await safe_edit_message_text(query, SESSION_EXPIRED)
    except CancelledByUser:
        # Не ошибка, а сигнал управления: сообщение об отмене пользователь уже
        # видит, работа прервана в самом загрузчике.
        logger.info("Задача прервана пользователем")
        if session_token:
            await _cleanup_user_session(user_id, context, session_token)
        return
    except Exception as e:
        logger.error(f"Ошибка в button_callback: {e}", exc_info=True)
        error_msg = str(e)

        if "Can't parse entities" in error_msg:
            try:
                await safe_edit_message_text(
                    query,
                    "❌ Ошибка отображения информации о видео.\n"
                    "Попробуйте другую ссылку или повторите попытку.",
                    parse_mode=None,
                )
            except Exception:
                await safe_edit_message_text(query, ERROR_FALLBACK)
        elif classified := _classify_youtube_error(error_msg):
            try:
                await safe_edit_message_text(query, classified, parse_mode="Markdown")
            except Exception:
                await safe_edit_message_text(query, ERROR_FALLBACK)
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
                await safe_edit_message_text(
                    query,
                    USER_ERROR_WITH_CODE.format(error_code=error_code)
                )
            except Exception:
                await safe_edit_message_text(query, ERROR_FALLBACK)

        if session_token:
            await _cleanup_user_session(user_id, context, session_token)


async def download_content(
    url: str, format_id: str, session_id: str, content_type: str
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
                session_id=session_id,
            )
        if content_type == "combined":
            return await run_blocking(
                download_video,
                url,
                format_id,
                session_id,
                description="download_video_combined_simple",
                session_id=session_id,
            )
        if content_type == "audio_only":
            return await run_blocking(
                download_audio,
                url,
                format_id,
                session_id,
                description="download_audio_only",
                session_id=session_id,
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


async def send_file(
    query: telegram.CallbackQuery,
    file_path: Path,
    session_token: str,
    session_data: dict,
    context: ContextTypes.DEFAULT_TYPE,
    cache_format_id: str | None = None,
) -> None:
    """Новая версия отправки файла, привязанная к конкретной сессии."""
    user_id = query.from_user.id
    back_markup = _build_back_markup(session_token)
    platform = session_data.get("platform", "bot")
    url = session_data.get("url")
    success = False
    try:
        await safe_edit_message_text(query, FILE_PREPARING)
        await asyncio.sleep(1)
        success = await send_single_file(
            query,
            file_path,
            session_token,
            session_data,
            cache_format_id=cache_format_id,
        )
        if success:
            await safe_edit_message_text(query, FILE_SENT)
    except (FileNotFoundError, PermissionError) as e:
        error_code = _make_error_code("file", "ACCESS")
        _schedule_platform_failure_log(
            platform=platform,
            stage="send_file_access",
            url=url,
            error_code=error_code,
            exc=e,
            session_id=session_data.get("session_id"),
        )
        await safe_edit_message_text(
            query,
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
            session_id=session_data.get("session_id"),
        )
        await safe_edit_message_text(
            query,
            USER_NETWORK_ERROR_WITH_CODE.format(error_code=error_code),
            reply_markup=back_markup,
        )
    except telegram.error.TelegramError as e:
        error_code = _make_error_code("telegram", "API")
        _schedule_platform_failure_log(
            platform=platform,
            stage="send_file_telegram",
            url=url,
            error_code=error_code,
            exc=e,
            session_id=session_data.get("session_id"),
        )
        await safe_edit_message_text(
            query,
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
            session_id=session_data.get("session_id"),
        )
        await safe_edit_message_text(
            query,
            USER_ERROR_WITH_CODE.format(error_code=error_code),
            reply_markup=back_markup,
        )
    finally:
        if success:
            await _cleanup_user_session(user_id, context, session_token)
        elif session_id := session_data.get("session_id"):
            cleanup_temp_files(session_id)


async def _send_photo_post_assets(
    query: telegram.CallbackQuery,
    session_token: str | None,
    session_data: dict,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """Отправляет фото-пост по одной картинке и затем отдельным аудио."""
    user_id = query.from_user.id
    url = session_data["url"]
    session_id = session_data["session_id"]
    platform = session_data.get("platform", "tiktok")
    back_markup = (
        _build_back_markup(session_token)
        if session_token
        else InlineKeyboardMarkup(
            [[InlineKeyboardButton(BTN_BACK, callback_data="main|back")]]
        )
    )

    if platform == "instagram":
        from utils.tiktok_instagram_utils import (
            download_instagram_photo_post_assets as download_photo_post_assets,
        )

        downloading_photos_message = "⏳ Скачиваю фотографии..."
        empty_images_message = (
            "Не удалось получить изображения для Instagram фото-поста."
        )
        platform_for_errors = "instagram"
        images_key = "_nuvio_instagram_images"
        audio_key = "_nuvio_instagram_audio_url"
        referer = "https://www.instagram.com/"
    else:
        from utils.tiktok_instagram_utils import (
            download_tiktok_photo_post_assets as download_photo_post_assets,
        )

        downloading_photos_message = "⏳ Скачиваю фотографии..."
        empty_images_message = "Не удалось получить изображения для TikTok фото-поста."
        platform_for_errors = "tiktok"
        images_key = "_nuvio_tiktok_images"
        audio_key = "_nuvio_tiktok_audio_url"
        referer = "https://www.tiktok.com/"

    try:
        await safe_edit_message_text(query, downloading_photos_message)

        # Пост до 5 МБ на кадр Telegram забирает по ссылкам сам, и тогда диск не
        # трогается вовсе. Решение принимается до первой отправки: доставить
        # половину поста ссылками, а половину файлами нельзя.
        from utils.tiktok_instagram_utils import resolve_photo_post_handoff

        video_info = session_data.get("video_info") or {}
        photo_plan = await run_blocking(
            resolve_photo_post_handoff,
            list(video_info.get(images_key) or []),
            video_info.get(audio_key),
            referer,
            description=f"resolve_{platform}_photo_post_handoff",
        )
        if photo_plan and await _deliver_photo_post_by_url(query, photo_plan):
            await query.edit_message_text(FILE_SENT)
            await _cleanup_user_session(user_id, context, session_token)
            return

        assets = await run_blocking(
            download_photo_post_assets,
            url,
            session_id,
            session_data.get("video_info"),
            description=f"download_{platform}_photo_post_assets",
        )
        image_paths = list(assets.get("images") or [])
        audio_path = assets.get("audio")

        if not image_paths:
            raise Exception(empty_images_message)

        for image_path in image_paths:
            with open(image_path, "rb") as image_file:
                try:
                    await query.message.reply_photo(photo=image_file, caption=None)
                except telegram.error.BadRequest:
                    image_file.seek(0)
                    await query.message.reply_document(
                        document=image_file, caption=None
                    )

        if audio_path:
            await safe_edit_message_text(query, DOWNLOADING_AUDIO_MESSAGE)
            with open(audio_path, "rb") as audio_file:
                await query.message.reply_audio(audio=audio_file, caption=None)

        await query.edit_message_text(FILE_SENT)
        await _cleanup_user_session(user_id, context, session_token)
    except (FileNotFoundError, PermissionError) as e:
        error_code = _make_error_code("file", "ACCESS")
        _schedule_platform_failure_log(
            platform=platform_for_errors,
            stage="send_photo_post_access",
            url=url,
            error_code=error_code,
            exc=e,
            session_id=session_id,
        )
        await query.edit_message_text(
            USER_FILE_ERROR_WITH_CODE.format(error_code=error_code),
            reply_markup=back_markup,
        )
        cleanup_temp_files(session_id)
    except telegram.error.NetworkError as e:
        error_code = _make_error_code("telegram", "NETWORK")
        _schedule_platform_failure_log(
            platform=platform_for_errors,
            stage="send_photo_post_network",
            url=url,
            error_code=error_code,
            exc=e,
            session_id=session_id,
        )
        await query.edit_message_text(
            USER_NETWORK_ERROR_WITH_CODE.format(error_code=error_code),
            reply_markup=back_markup,
        )
        cleanup_temp_files(session_id)
    except telegram.error.TelegramError as e:
        error_code = _make_error_code("telegram", "API")
        _schedule_platform_failure_log(
            platform=platform_for_errors,
            stage="send_photo_post_telegram",
            url=url,
            error_code=error_code,
            exc=e,
            session_id=session_id,
        )
        await query.edit_message_text(
            USER_TELEGRAM_ERROR_WITH_CODE.format(error_code=error_code),
            reply_markup=back_markup,
        )
        cleanup_temp_files(session_id)
    except Exception as e:
        error_code = _make_error_code(
            platform_for_errors,
            _classify_internal_error_category(platform_for_errors, str(e)),
        )
        _schedule_platform_failure_log(
            platform=platform_for_errors,
            stage="send_photo_post_unexpected",
            url=url,
            error_code=error_code,
            exc=e,
            session_id=session_id,
        )
        await query.edit_message_text(
            _build_public_error_message(platform_for_errors, error_code, str(e)),
            reply_markup=back_markup,
        )
        cleanup_temp_files(session_id)


def _file_ready_to_send(file_path: Path) -> bool:
    """Проверяет, что отправлять действительно есть что.

    В локальном режиме путь уходит в PTB как объект, а `resolve()` файловую
    систему не трогает. Пропавший файл в этом случае превращался в
    `TypeError: Object of type PosixPath is not JSON serializable`: PTB
    конвертирует путь в ссылку только когда `is_file()` истинно, иначе кладёт
    объект в тело запроса. Пользователь получал «ошибку сети» вместо «нет
    файла».

    Нулевой размер считается отсутствием: Telegram пустой файл не примет.
    """
    try:
        return file_path.is_file() and file_path.stat().st_size > 0
    except OSError:
        return False


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
    platform = session_data.get("platform", "bot")
    url = session_data.get("url")

    # Повторять нечего: файла нет, и от третьей попытки он не появится.
    if not _file_ready_to_send(file_path):
        error_code = _make_error_code("file", "ACCESS")
        _schedule_platform_failure_log(
            platform=platform,
            stage="send_single_file_missing",
            url=url,
            error_code=error_code,
            exc=FileNotFoundError(str(file_path)),
            session_id=session_data.get("session_id"),
        )
        await safe_edit_message_text(
            query,
            USER_FILE_ERROR_WITH_CODE.format(error_code=error_code),
            reply_markup=back_markup,
        )
        return False

    for attempt in range(1, max_retries + 1):
        try:
            file_ext = file_path.suffix.lower()
            media_kind = media_kind_for_suffix(file_ext)
            message = None
            telegram_file = (
                file_path.resolve()
                if TELEGRAM_LOCAL_MODE
                else file_path.open("rb")
            )

            try:
                if media_kind == "video":
                    message = await query.message.reply_video(
                        video=telegram_file,
                        caption=None,
                        supports_streaming=True,
                        write_timeout=1800,
                        read_timeout=1800,
                    )
                elif media_kind == "audio":
                    message = await query.message.reply_audio(
                        audio=telegram_file,
                        caption=None,
                        write_timeout=1800,
                        read_timeout=1800,
                    )
                else:
                    message = await query.message.reply_document(
                        document=telegram_file,
                        caption=None,
                        write_timeout=1800,
                        read_timeout=1800,
                    )
            finally:
                if not TELEGRAM_LOCAL_MODE:
                    telegram_file.close()

            # Кэширование file_id для видео, аудио и документов
            if message and url and cache_format_id:
                _cache_sent_media(
                    message,
                    url,
                    platform,
                    cache_format_id,
                    session_data.get("video_info"),
                )

            return True
        except (telegram.error.NetworkError, telegram.error.TimedOut) as e:
            last_error = e
            logger.warning(f"Попытка {attempt}/{max_retries} неудачна: {e}")
            if attempt < max_retries:
                await asyncio.sleep(2**attempt)
            continue
        except (FileNotFoundError, PermissionError) as e:
            error_code = _make_error_code("file", "ACCESS")
            _schedule_platform_failure_log(
                platform=platform,
                stage="send_single_file_access",
                url=url,
                error_code=error_code,
                exc=e,
                session_id=session_data.get("session_id"),
            )
            await query.edit_message_text(
                USER_FILE_ERROR_WITH_CODE.format(error_code=error_code),
                reply_markup=back_markup,
            )
            return False
        except telegram.error.BadRequest as e:
            logger.error(
                f"Неверный запрос при отправке файла {file_path}: {e}", exc_info=True
            )
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
                session_id=session_data.get("session_id"),
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
                session_id=session_data.get("session_id"),
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
            session_id=session_data.get("session_id"),
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
    escape_chars = [
        "*",
        "_",
        "[",
        "]",
        "(",
        ")",
        "~",
        "`",
        ">",
        "#",
        "+",
        "-",
        "=",
        "|",
        "{",
        "}",
        ".",
        "!",
    ]
    for char in escape_chars:
        text = text.replace(char, f"\\{char}")

    return text
