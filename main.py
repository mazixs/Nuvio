#!/usr/bin/env python3
"""
Telegram бот для скачивания видео с YouTube, TikTok и Instagram.
"""
import asyncio
import signal
from contextlib import suppress
from pathlib import Path

import telegram
from dotenv import load_dotenv
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, filters, ContextTypes

# Load file-based environment variables from canonical to legacy paths.
_BASE_DIR = Path(__file__).parent
for _dotenv_path in (
    _BASE_DIR / ".secrets" / ".env",
    _BASE_DIR / ".env.local",
    _BASE_DIR / ".env",
):
    if _dotenv_path.exists():
        load_dotenv(dotenv_path=_dotenv_path, override=False)

from config import TELEGRAM_TOKEN, LOG_LEVEL, validate_config  # noqa: E402
from utils.logger import setup_logger  # noqa: E402
from utils.temp_file_manager import cleanup_temp_files  # noqa: E402
from utils.cache_commands import stats_command, cleanup_cache_command, search_cache_command  # noqa: E402
from utils.video_cache import telegram_cache  # noqa: E402
from utils.cookie_manager import admin_command, handle_admin_callback, handle_document_upload  # noqa: E402
from utils.ytdlp_runtime import ensure_latest_yt_dlp, get_installed_yt_dlp_version  # noqa: E402

# Настройка логирования
logger = setup_logger(__name__, level=LOG_LEVEL)


def _classify_polling_error(exc: telegram.error.TelegramError) -> tuple[str, str]:
    """Классифицирует типичные сбои polling по шаблону ошибки."""
    message = str(exc)
    msg_lower = message.lower()

    if isinstance(exc, telegram.error.Conflict):
        return (
            "POLLING_CONFLICT",
            "Параллельный polling другим экземпляром или сервером с тем же токеном.",
        )

    if "server disconnected without sending a response" in msg_lower or "remoteprotocolerror" in msg_lower:
        return (
            "REMOTE_DISCONNECT",
            "Bot API закрыл long polling без ответа. Частый сценарий: другой сервер перехватил polling или произошёл обрыв на переключении маршрута.",
        )

    if "connection refused" in msg_lower or "connecterror" in msg_lower:
        return (
            "CONNECT_REFUSED",
            "Удалённая сторона отказала в подключении или маршрут до Bot API недоступен.",
        )

    if "timed out" in msg_lower:
        return (
            "TIMEOUT",
            "Превышен таймаут ожидания ответа Bot API.",
        )

    if isinstance(exc, telegram.error.NetworkError):
        return (
            "NETWORK",
            "Сетевой сбой при long polling Bot API.",
        )

    return (
        "UNKNOWN",
        "Неожиданная ошибка при long polling Bot API.",
    )


def _polling_error_callback(exc: telegram.error.TelegramError) -> None:
    """Пишет в лог интерпретацию типовых ошибок polling-цикла."""
    category, summary = _classify_polling_error(exc)
    logger.warning(
        "Ошибка polling [%s]: %s | исходное сообщение: %s",
        category,
        summary,
        exc,
    )

async def scheduled_cache_cleanup(context: ContextTypes.DEFAULT_TYPE):
    """Периодическая очистка кеша (запускается раз в сутки)."""
    try:
        deleted = telegram_cache.cleanup_expired(ttl_days=90)
        if deleted > 0:
            logger.info(f"🧹 Автоматическая очистка кэша: удалено {deleted} записей")
    except Exception as e:
        logger.error(f"Ошибка при автоматической очистке кэша: {e}")
async def scheduled_cache_vacuum(context: ContextTypes.DEFAULT_TYPE):
    """Еженедельная оптимизация SQLite кэша."""
    try:
        db_path = telegram_cache.db_path
        before = db_path.stat().st_size if db_path.exists() else 0
        telegram_cache.vacuum()
        after = db_path.stat().st_size if db_path.exists() else 0
        logger.info(
            "🧽 VACUUM кэша завершён: размер %.2f МБ → %.2f МБ",
            before / (1024 * 1024),
            after / (1024 * 1024),
        )
    except Exception as e:
        logger.error(f"Ошибка при оптимизации кэша: {e}")

def _build_application() -> Application:
    """Создаёт и конфигурирует экземпляр Application."""
    from utils.telegram_utils import (
        button_callback,
        download_command,
        help_command,
        process_url,
        start_command,
        set_bot_instance,
        _notify_admins_crash,
    )

    application = (
        Application.builder()
        .token(TELEGRAM_TOKEN)
        .connect_timeout(10.0)
        .read_timeout(120.0)
        .write_timeout(120.0)
        .get_updates_connect_timeout(10.0)
        .get_updates_read_timeout(120.0)
        .get_updates_write_timeout(30.0)
        .get_updates_pool_timeout(5.0)
        .http_version("1.1")
        .get_updates_http_version("1.1")
        .build()
    )

    set_bot_instance(application.bot)

    async def _global_error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Глобальный обработчик необработанных исключений — шлёт краш-репорт админам."""
        logger.error("Необработанное исключение:", exc_info=context.error)
        if context.error:
            from utils.telegram_utils import _make_error_code
            error_code = _make_error_code("bot", "GLOBAL")
            await _notify_admins_crash(
                error_code=error_code,
                platform="bot",
                stage="global_error_handler",
                url=None,
                exc=context.error,
            )

    application.add_error_handler(_global_error_handler)

    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("download", download_command))
    application.add_handler(CommandHandler("admin", admin_command))

    application.add_handler(CommandHandler("cache_stats", stats_command))
    application.add_handler(CommandHandler("cleanup_cache", cleanup_cache_command))
    application.add_handler(CommandHandler("search_cache", search_cache_command))

    application.add_handler(MessageHandler(filters.Document.ALL, handle_document_upload))
    application.add_handler(CallbackQueryHandler(handle_admin_callback, pattern=r"^admin\|"))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, process_url))
    application.add_handler(CallbackQueryHandler(button_callback))

    if application.job_queue:
        application.job_queue.run_repeating(scheduled_cache_cleanup, interval=86400, first=60)
        application.job_queue.run_repeating(scheduled_cache_vacuum, interval=604800, first=600)
        logger.info("🕒 Планировщик задач инициализирован (автоочистка кэша активна)")

    return application


async def _shutdown_application(application: Application) -> None:
    """Аккуратно останавливает Application и связанные ресурсы."""
    logger.info("⏹️ Остановка бота...")
    if application.updater:
        with suppress(RuntimeError):
            await application.updater.stop()
    with suppress(RuntimeError):
        await application.stop()
    with suppress(RuntimeError):
        await application.shutdown()


async def run_bot() -> None:
    """Основной цикл с graceful shutdown (SIGINT/SIGTERM)."""
    try:
        validate_config()
        logger.info("Конфигурация валидна")
    except ValueError as exc:
        logger.critical(f"Ошибка конфигурации: {exc}")
        return

    logger.info("Запуск бота...")
    update_result = ensure_latest_yt_dlp(reason="startup")
    if not update_result.succeeded:
        logger.warning(
            "Автообновление yt-dlp не подтвердилось. Продолжаем с локальной версией %s",
            update_result.version_after or update_result.version_before or "unknown",
        )
    logger.info("Текущая версия yt-dlp: %s", get_installed_yt_dlp_version() or "unknown")
    application = _build_application()
    try:
        await application.initialize()
        await application.start()
        if not application.updater:
            raise RuntimeError("Updater не инициализирован")
        # Явно запрашиваем все типы апдейтов, чтобы колбэки кнопок гарантированно приходили
        await application.updater.start_polling(
            allowed_updates=telegram.Update.ALL_TYPES,
            bootstrap_retries=3,
            error_callback=_polling_error_callback,
        )

        cache_stats = telegram_cache.get_stats()
        logger.info(f"💾 В кэше {cache_stats['total_videos']} видео")
        logger.info("✅ Бот запущен и готов к работе!")
        logger.info("⚡ Система быстрой доставки активна")

        stop_event = asyncio.Event()

        def _request_stop(sig: signal.Signals) -> None:
            logger.info("Получен сигнал %s, начинаем остановку...", sig.name)
            stop_event.set()

        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            with suppress(NotImplementedError):
                loop.add_signal_handler(sig, lambda s=sig: _request_stop(s))

        await stop_event.wait()
    finally:
        await _shutdown_application(application)
        cleanup_temp_files()
        logger.info("🧹 Временные файлы очищены")


def main() -> None:
    """Точка входа с asyncio.run и обработкой ошибок верхнего уровня."""
    try:
        asyncio.run(run_bot())
    except KeyboardInterrupt:
        logger.info("Бот остановлен пользователем (KeyboardInterrupt)")
    except (ConnectionError, OSError) as exc:
        logger.error(f"Ошибка сети или системы: {exc}")
    except ImportError as exc:
        logger.error(f"Ошибка импорта модулей: {exc}")
    except Exception as exc:  # noqa: BLE001
        exc.add_note("main.py: глобальный обработчик ошибок")
        logger.error(f"Неожиданная ошибка: {exc}", exc_info=True)
    finally:
        cleanup_temp_files()

if __name__ == "__main__":
    main()
