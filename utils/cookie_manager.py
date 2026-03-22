"""Admin-only cookie management for Telegram uploads."""

from __future__ import annotations

import asyncio
import logging
import os

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from config import ADMIN_IDS, SECRETS_DIR
from utils.cookie_health import CookieHealthResult, check_all_cookie_health

logger = logging.getLogger(__name__)

ADMIN_UPLOAD_TARGET_KEY = "admin_expected_cookie_file"
MAX_COOKIE_FILE_SIZE = 1 * 1024 * 1024  # 1 MiB
ALLOWED_MIME_TYPES = {
    "text/plain",
    "application/octet-stream",
    "application/x-netscape-cookie",
}
COOKIE_TARGETS = {
    "youtube": "www.youtube.com_cookies.txt",
    "instagram": "www.instagram.com_cookies.txt",
    "tiktok": "www.tiktok.com_cookies.txt",
}
COOKIE_LABELS = {
    "youtube": "YouTube",
    "instagram": "Instagram",
    "tiktok": "TikTok",
}
ALLOWED_COOKIE_FILES = set(COOKIE_TARGETS.values())
NON_ADMIN_DOCUMENT_MESSAGE = (
    "🔒 Бот не принимает файлы от пользователей. "
    "Поддерживаются только ссылки на видео и админская загрузка cookies."
)
ADMIN_ONLY_MESSAGE = "🔒 Эта функция доступна только администраторам."
ADMIN_UPLOAD_REQUIRED_MESSAGE = (
    "Сначала откройте /admin и выберите, cookies какой платформы хотите обновить."
)


def is_admin(user_id: int | None) -> bool:
    """Checks whether the user is an administrator."""
    return user_id is not None and user_id in ADMIN_IDS


def build_admin_entry_markup() -> InlineKeyboardMarkup:
    """Small entry point shown to admins on /start."""
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton("Admin Panel", callback_data="admin|cookies|panel")]]
    )


def _build_admin_panel_markup(expected_file_name: str | None = None) -> InlineKeyboardMarkup:
    keyboard = [
        [
            InlineKeyboardButton("YouTube", callback_data="admin|cookies|upload|youtube"),
            InlineKeyboardButton("Instagram", callback_data="admin|cookies|upload|instagram"),
        ],
        [
            InlineKeyboardButton("TikTok", callback_data="admin|cookies|upload|tiktok"),
            InlineKeyboardButton("Check Cookies", callback_data="admin|cookies|check"),
        ],
        [InlineKeyboardButton("Refresh", callback_data="admin|cookies|panel")],
    ]
    if expected_file_name:
        keyboard.append(
            [InlineKeyboardButton("Cancel Upload Mode", callback_data="admin|cookies|cancel")]
        )
    return InlineKeyboardMarkup(keyboard)


def _format_cookie_status(file_name: str) -> str:
    file_path = SECRETS_DIR / file_name
    if not file_path.exists():
        return f"missing - {file_name}"

    size_kib = max(1, round(file_path.stat().st_size / 1024))
    return f"configured - {file_name} ({size_kib} KiB)"


def _build_admin_panel_text(expected_file_name: str | None = None) -> str:
    lines = [
        "Admin panel",
        "",
        "Cookie status:",
        f"- YouTube: {_format_cookie_status(COOKIE_TARGETS['youtube'])}",
        f"- Instagram: {_format_cookie_status(COOKIE_TARGETS['instagram'])}",
        f"- TikTok: {_format_cookie_status(COOKIE_TARGETS['tiktok'])}",
    ]
    if expected_file_name:
        lines.extend(
            [
                "",
                f"Upload mode enabled for: {expected_file_name}",
                "Send exactly this .txt file as a Telegram document in the next message.",
            ]
        )
    else:
        lines.extend(
            [
                "",
                "Choose a platform below to arm cookie upload mode.",
            ]
        )
    return "\n".join(lines)


def _build_upload_instruction(file_name: str) -> str:
    return "\n".join(
        [
            "Upload mode enabled.",
            "",
            f"Expected file: {file_name}",
            "Send it as a Telegram document.",
            "Any other filename will be rejected.",
            "Only .txt Netscape cookies are accepted.",
        ]
    )


def _format_health_icon(status: str) -> str:
    if status == "valid":
        return "✅"
    if status in {"expired", "stale", "invalid_format"}:
        return "❌"
    if status in {"rate_limited", "probe_failed"}:
        return "⚠️"
    return "ℹ️"


def _build_cookie_health_text(
    results: dict[str, CookieHealthResult],
    expected_file_name: str | None = None,
) -> str:
    lines = [
        "Cookie health check",
        "",
    ]
    for platform in ("youtube", "instagram", "tiktok"):
        result = results[platform]
        label = COOKIE_LABELS[platform]
        lines.append(f"{_format_health_icon(result.status)} {label}: {result.status} - {result.summary}")

    if expected_file_name:
        lines.extend(
            [
                "",
                f"Upload mode is still enabled for: {expected_file_name}",
            ]
        )

    lines.extend(
        [
            "",
            "Use Refresh to return to the normal admin panel.",
        ]
    )
    return "\n".join(lines)


async def admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Shows the admin cookie panel."""
    user_id = update.effective_user.id if update.effective_user else None
    if not is_admin(user_id):
        await update.message.reply_text(ADMIN_ONLY_MESSAGE)
        return

    context.user_data.pop(ADMIN_UPLOAD_TARGET_KEY, None)
    await update.message.reply_text(
        _build_admin_panel_text(),
        reply_markup=_build_admin_panel_markup(),
    )


async def handle_admin_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handles admin-only inline actions for cookie management."""
    query = update.callback_query
    user_id = query.from_user.id if query and query.from_user else None

    if not query:
        return

    if not is_admin(user_id):
        await query.answer(ADMIN_ONLY_MESSAGE, show_alert=True)
        return

    data = query.data or ""
    await query.answer()

    if data in {"admin|cookies|panel", "admin|cookies|refresh"}:
        expected_file_name = context.user_data.get(ADMIN_UPLOAD_TARGET_KEY)
        await query.edit_message_text(
            _build_admin_panel_text(expected_file_name),
            reply_markup=_build_admin_panel_markup(expected_file_name),
        )
        return

    if data == "admin|cookies|check":
        expected_file_name = context.user_data.get(ADMIN_UPLOAD_TARGET_KEY)
        await query.edit_message_text("Checking cookie health...", reply_markup=_build_admin_panel_markup(expected_file_name))
        results = await asyncio.to_thread(check_all_cookie_health)
        await query.edit_message_text(
            _build_cookie_health_text(results, expected_file_name),
            reply_markup=_build_admin_panel_markup(expected_file_name),
        )
        return

    if data == "admin|cookies|cancel":
        context.user_data.pop(ADMIN_UPLOAD_TARGET_KEY, None)
        await query.edit_message_text(
            _build_admin_panel_text(),
            reply_markup=_build_admin_panel_markup(),
        )
        return

    parts = data.split("|")
    if len(parts) == 4 and parts[:3] == ["admin", "cookies", "upload"]:
        platform = parts[3]
        file_name = COOKIE_TARGETS.get(platform)
        if not file_name:
            await query.edit_message_text(
                _build_admin_panel_text(),
                reply_markup=_build_admin_panel_markup(),
            )
            return

        context.user_data[ADMIN_UPLOAD_TARGET_KEY] = file_name
        await query.edit_message_text(
            _build_upload_instruction(file_name),
            reply_markup=_build_admin_panel_markup(file_name),
        )
        return

    await query.edit_message_text(
        _build_admin_panel_text(),
        reply_markup=_build_admin_panel_markup(),
    )


async def handle_document_upload(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handles document uploads with strict admin-only gating."""
    user = update.effective_user
    message = update.message
    document = message.document if message else None
    user_id = user.id if user else None

    if not message or not document:
        return

    if not is_admin(user_id):
        logger.warning("Ignoring document from non-admin user %s", user_id)
        await message.reply_text(NON_ADMIN_DOCUMENT_MESSAGE)
        return

    expected_file_name = context.user_data.get(ADMIN_UPLOAD_TARGET_KEY)
    if not expected_file_name:
        await message.reply_text(ADMIN_UPLOAD_REQUIRED_MESSAGE)
        return

    file_name = document.file_name or ""
    if file_name != expected_file_name:
        await message.reply_text(
            f"❌ Сейчас ожидается файл `{expected_file_name}`. "
            f"Отправлен `{file_name or 'без имени'}`.",
            parse_mode="Markdown",
        )
        return

    if file_name not in ALLOWED_COOKIE_FILES:
        await message.reply_text(
            "❌ Недопустимое имя файла. Разрешены только:\n" + "\n".join(sorted(ALLOWED_COOKIE_FILES))
        )
        return

    if document.file_size and document.file_size > MAX_COOKIE_FILE_SIZE:
        await message.reply_text(
            f"❌ Файл слишком большой ({document.file_size / (1024 * 1024):.1f} MiB). "
            f"Максимум {MAX_COOKIE_FILE_SIZE // (1024 * 1024)} MiB."
        )
        logger.warning(
            "Admin %s attempted to upload an oversized cookie file (%s bytes)",
            user_id,
            document.file_size,
        )
        return

    if document.mime_type and document.mime_type not in ALLOWED_MIME_TYPES:
        await message.reply_text(
            "❌ Неверный тип файла. Принимаются только текстовые cookie-файлы (.txt)."
        )
        logger.warning(
            "Admin %s sent a cookie file with unsupported MIME type %s",
            user_id,
            document.mime_type,
        )
        return

    try:
        telegram_file = await document.get_file()
        file_path = SECRETS_DIR / file_name
        await telegram_file.download_to_drive(file_path)

        if os.name != "nt":
            file_path.chmod(0o600)

        context.user_data.pop(ADMIN_UPLOAD_TARGET_KEY, None)
        logger.info("Admin %s updated cookie file %s", user_id, file_name)
        await message.reply_text(
            f"✅ Файл {file_name} успешно обновлён.",
        )
        await message.reply_text(
            _build_admin_panel_text(),
            reply_markup=_build_admin_panel_markup(),
        )
    except Exception as exc:  # noqa: BLE001
        logger.error("Failed to update cookie file %s: %s", file_name, exc, exc_info=True)
        await message.reply_text(f"❌ Произошла ошибка при сохранении файла: {exc}")
