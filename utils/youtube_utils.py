"""
Модуль для работы с YouTube с использованием yt-dlp.
"""

import re
import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any, TypedDict, NotRequired

import yt_dlp
from config import (
    MAX_FILE_SIZE,
    MAX_VIDEO_DURATION,
    YOUTUBE_COOKIES_FILE,
    YTDLP_CLI_FALLBACK,
)
from utils.logger import setup_logger
from utils.temp_file_manager import get_temp_file_path
from utils.media_processor import convert_webm_to_mp4
from utils.gokapi_utils import upload_to_gokapi
from utils.ytdlp_runtime import extract_cli_output_path, run_yt_dlp_cli

logger = setup_logger(__name__)

DEFAULT_YTDLP_NETWORK_OPTS: dict[str, Any] = {
    'retries': 5,
    'socket_timeout': 40,
    'http_chunk_size': 10_485_760,  # 10 МБ
    'fragment_retries': 5,
    'skip_unavailable_fragments': True,
    'abort_on_unavailable_fragments': False,
    'concurrent_fragment_downloads': 4,
    'continuedl': False,
    'noplaylist': True,
    # EJS: YouTube требует JS runtime для решения n-parameter challenge (с yt-dlp 2025.11+)
    'remote_components': ['ejs:github'],
}

_NETWORK_TIMEOUT_SIGNATURES = (
    'Read timed out',
    'Connection timed out',
    'Timed out',
    'Connection reset by peer',
    'UNEXPECTED_EOF_WHILE_READING',
    'EOF occurred in violation of protocol',
    'fragment not found',
    'HTTP Error 403',
)


def _classify_download_error_kind(message: str) -> str:
    """Классифицирует тип DownloadError для корректного уровня логирования."""
    msg_lower = message.lower()
    if 'requested format is not available' in msg_lower:
        return 'FORMAT_UNAVAILABLE'
    if any(signature in msg_lower for signature in ('http error 403', 'forbidden', 'login required', 'private video')):
        return 'ACCESS_RESTRICTED'
    if any(
        signature in msg_lower
        for signature in (
            'requires a javascript runtime',
            'nsig extraction failed',
            'signature extraction failed',
            'unable to extract initial player response',
            'remote components',
        )
    ):
        return 'EXTRACTOR_RUNTIME'
    if any(signature.lower() in msg_lower for signature in _NETWORK_TIMEOUT_SIGNATURES):
        return 'NETWORK_TIMEOUT'
    return 'UNKNOWN'

# Регулярное выражение для проверки YouTube URL (включая Shorts)
YOUTUBE_URL_PATTERN = r'(?:https?:\/\/)?(?:www\.)?(?:youtube\.com|youtu\.be)\/(?:watch\?v=|shorts\/)?([a-zA-Z0-9_-]{11})'

class FormatInfoDict(TypedDict, total=False):
    format_id: str
    format: str
    ext: str
    height: NotRequired[int]
    width: NotRequired[int]
    filesize: NotRequired[int]
    audio_channels: NotRequired[int]
    vcodec: NotRequired[str]
    acodec: NotRequired[str]
    type: NotRequired[str]

def is_valid_youtube_url(url: str) -> bool:
    """
    Проверяет, является ли URL действительной ссылкой на YouTube видео.
    
    Args:
        url (str): URL для проверки.
        
    Returns:
        bool: True, если URL является допустимой ссылкой на YouTube, иначе False.
    """
    return bool(re.match(YOUTUBE_URL_PATTERN, url))

def extract_video_id(url: str) -> str | None:
    """
    Извлекает идентификатор видео из YouTube URL.
    
    Args:
        url (str): YouTube URL.
        
    Returns:
        str | None: ID видео или None, если URL некорректен.
    """
    match = re.search(YOUTUBE_URL_PATTERN, url)
    if match:
        return match.group(1)
    return None

def get_video_info(url: str) -> dict[str, Any]:
    """
    Получает информацию о видео.
    Сначала пробует без cookies, при ошибке — повторяет с cookies (если файл есть).
    """
    logger.info(f"Получение информации о видео: {url}")
    
    def _get_info(use_cookies: bool) -> dict[str, Any]:
        ydl_opts = {
            'quiet': True,
            'no_warnings': True,
            'skip_download': True,
        }
        _apply_network_opts(ydl_opts)
        if use_cookies and YOUTUBE_COOKIES_FILE and Path(YOUTUBE_COOKIES_FILE).is_file():
            logger.info(f"Использование файла cookies: {YOUTUBE_COOKIES_FILE}")
            ydl_opts['cookiefile'] = YOUTUBE_COOKIES_FILE
        elif use_cookies:
            logger.warning(f"Файл cookies указан ({YOUTUBE_COOKIES_FILE}), но не найден. Запрос будет выполнен без cookies.")
        else:
            logger.info("Пробуем получить информацию о видео без cookies.")
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            duration = info.get('duration')
            if duration and duration > MAX_VIDEO_DURATION:
                logger.warning(f"Видео слишком длинное: {duration} секунд")
                raise Exception(f"Видео слишком длинное. Максимальная длительность: {MAX_VIDEO_DURATION // 60} минут.")
            logger.info("Информация о видео успешно получена.")
            return info

    # Cookies-first стратегия: YouTube в 2025-2026 почти всегда требует cookies
    if YOUTUBE_COOKIES_FILE and Path(YOUTUBE_COOKIES_FILE).is_file():
        try:
            return _get_info(True)
        except (yt_dlp.utils.DownloadError, yt_dlp.cookies.CookieLoadError) as e:
            logger.warning(f"Ошибка с cookies: {e}. Пробуем без cookies как fallback...")
            try:
                return _get_info(False)
            except Exception as e2:
                logger.error(f"Ошибка при получении информации о видео даже без cookies: {e2}", exc_info=True)
                raise
    else:
        try:
            return _get_info(False)
        except Exception as e:
            logger.error(f"Ошибка при получении информации о видео без cookies: {e}", exc_info=True)
            raise

def get_available_formats(video_info: dict[str, Any], filter_by_size: bool = True) -> dict[str, list[FormatInfoDict]]:
    """
    Получает список доступных форматов видео с опциональной фильтрацией по размеру.
    
    Args:
        video_info (Dict[str, Any]): Информация о видео.
        filter_by_size (bool): Фильтровать форматы по MAX_FILE_SIZE (по умолчанию True).
        
    Returns:
        Dict[str, List[Dict[str, Any]]]: Словарь с группами форматов.
    """
    formats = video_info.get('formats', [])
    video_formats: list[FormatInfoDict] = []
    audio_formats: list[FormatInfoDict] = []
    combined_formats: list[FormatInfoDict] = []
    
    for format_info in formats:
        # Логируем, что получили по filesize
        filesize = format_info.get('filesize') or format_info.get('filesize_approx')
        logger.debug(f"FormatID={format_info.get('format_id')}, ext={format_info.get('ext')}, height={format_info.get('height')}, filesize={filesize}")
        
        if not format_info.get('height') and not format_info.get('audio_channels'):
            continue
        
        # Применяем фильтрацию по размеру файла (если включена)
        if filter_by_size and filesize:
            if filesize > MAX_FILE_SIZE:
                logger.debug(f"Формат {format_info.get('format_id')} пропущен: размер {filesize} превышает {MAX_FILE_SIZE}")
                continue
        
        format_id = format_info.get('format_id')
        
        if format_info.get('vcodec') != 'none' and format_info.get('acodec') == 'none':
            video_formats.append({
                'format_id': format_id,
                'format': format_info.get('format'),
                'ext': format_info.get('ext'),
                'height': format_info.get('height'),
                'width': format_info.get('width'),
                'filesize': filesize,
                'type': 'video_only'
            })
        elif format_info.get('vcodec') == 'none' and format_info.get('acodec') != 'none':
            audio_formats.append({
                'format_id': format_id,
                'format': format_info.get('format'),
                'ext': format_info.get('ext'),
                'filesize': filesize,
                'type': 'audio_only'
            })
        elif format_info.get('vcodec') != 'none' and format_info.get('acodec') != 'none':
            combined_formats.append({
                'format_id': format_id,
                'format': format_info.get('format'),
                'ext': format_info.get('ext'),
                'height': format_info.get('height'),
                'width': format_info.get('width'),
                'filesize': filesize,
                'type': 'combined'
            })
    
    video_formats.sort(key=lambda x: x.get('height', 0), reverse=True)
    audio_formats.sort(key=lambda x: x.get('filesize', 0) or 0, reverse=True)
    combined_formats.sort(key=lambda x: x.get('height', 0), reverse=True)
    
    logger.info(f"Найдено форматов: video_only={len(video_formats)}, audio_only={len(audio_formats)}, combined={len(combined_formats)}")
    
    return {
        'video_only': video_formats,
        'audio_only': audio_formats,
        'combined': combined_formats
    }

def _apply_network_opts(options: dict[str, Any]) -> None:
    """Добавляет в options дефолтные параметры сети для yt-dlp."""
    options.update(DEFAULT_YTDLP_NETWORK_OPTS)


def _execute_with_backoff(description: str, func: Callable[[], Path | str], max_attempts: int = 3) -> Path | str:
    """Запускает функцию с экспоненциальным backoff при сетевых таймаутах."""
    for attempt in range(1, max_attempts + 1):
        try:
            return func()
        except yt_dlp.utils.DownloadError as e:
            message = str(e)
            error_kind = _classify_download_error_kind(message)
            if error_kind == 'NETWORK_TIMEOUT':
                if attempt == max_attempts:
                    logger.error(
                        f"{description} не удалось после {attempt} попыток из-за таймаута: {message}",
                        exc_info=True,
                    )
                    raise
                delay = min(2 ** attempt, 30)
                logger.warning(
                    f"{description}: таймаут (попытка {attempt}/{max_attempts}). Повтор через {delay}с",
                )
                time.sleep(delay)
                continue
            if error_kind in {'FORMAT_UNAVAILABLE', 'ACCESS_RESTRICTED'}:
                logger.warning(
                    f"{description}: ожидаемая ошибка yt-dlp ({error_kind}): {message}"
                )
            else:
                logger.error(f"{description}: ошибка скачивания: {message}", exc_info=True)
            raise


def _convert_webm_if_needed(downloaded_file: Path, session_id: str) -> Path:
    """Конвертирует webm в mp4 для совместимости Telegram."""
    if downloaded_file.suffix.lower() != ".webm":
        return downloaded_file

    logger.info(f"Обнаружен webm файл, конвертируем в mp4: {downloaded_file}")
    try:
        converted = convert_webm_to_mp4(downloaded_file, session_id)
        logger.info(f"Конвертация webm в mp4 завершена: {converted}")
        return converted
    except Exception as e:
        logger.warning(
            f"Не удалось конвертировать webm в mp4: {e}. Используем исходный файл.",
            exc_info=True,
        )
        return downloaded_file


def _resolve_output_template(session_id: str, output_dir: Path | None) -> Path:
    """Возвращает шаблон пути для yt-dlp."""
    if output_dir is None:
        return get_temp_file_path(session_id, "%(title)s.%(ext)s")
    return output_dir / "%(title)s.%(ext)s"


def _cookiefile_if_available(use_cookies: bool) -> str | None:
    """Возвращает путь к cookies, если он реально доступен."""
    if use_cookies and YOUTUBE_COOKIES_FILE and Path(YOUTUBE_COOKIES_FILE).is_file():
        return YOUTUBE_COOKIES_FILE
    return None


def _maybe_upload_large_file(downloaded_file: Path, force_local: bool) -> Path | str:
    """Отдаёт файл локально или выгружает его через Gokapi при превышении лимита."""
    file_size = downloaded_file.stat().st_size
    if force_local or file_size <= MAX_FILE_SIZE:
        return downloaded_file

    logger.warning(
        "Размер файла %s (%s байт) превышает лимит Telegram. Пробуем Gokapi.",
        downloaded_file,
        file_size,
    )
    try:
        success, link_or_error = upload_to_gokapi(downloaded_file)
        if success:
            logger.info("Файл загружен на Gokapi: %s", link_or_error)
            return link_or_error
        raise Exception(f"Сервер загрузки недоступен: {link_or_error}")
    finally:
        try:
            if downloaded_file.exists():
                downloaded_file.unlink()
                logger.info("Локальный файл %s удалён после попытки выгрузки.", downloaded_file)
        except Exception as e_del:
            logger.error("Ошибка при удалении локального файла %s: %s", downloaded_file, e_del)


def _build_cli_download_command(
    *,
    url: str,
    output_path_template: Path,
    format_selector: str,
    cookiefile: str | None = None,
    merge_output_format: str | None = None,
    extract_audio_codec: str | None = None,
) -> list[str]:
    """Собирает локальную CLI-команду yt-dlp для fallback-сценария."""
    command = [
        sys.executable,
        "-m",
        "yt_dlp",
        "--no-warnings",
        "--no-progress",
        "--newline",
        "--no-playlist",
        "--retries",
        str(DEFAULT_YTDLP_NETWORK_OPTS["retries"]),
        "--fragment-retries",
        str(DEFAULT_YTDLP_NETWORK_OPTS["fragment_retries"]),
        "--socket-timeout",
        str(DEFAULT_YTDLP_NETWORK_OPTS["socket_timeout"]),
        "--concurrent-fragments",
        str(DEFAULT_YTDLP_NETWORK_OPTS["concurrent_fragment_downloads"]),
        "--skip-unavailable-fragments",
        "--no-continue",
        "--remote-components",
        "ejs:github",
        "--print",
        "after_move:filepath",
        "-o",
        str(output_path_template),
        "-f",
        format_selector,
    ]
    if cookiefile:
        command.extend(["--cookies", cookiefile])
    if merge_output_format:
        command.extend(["--merge-output-format", merge_output_format])
    if extract_audio_codec == "mp3":
        command.extend(["-x", "--audio-format", "mp3", "--audio-quality", "192K"])
    command.append(url)
    return command


def _download_with_cli_fallback(
    *,
    url: str,
    session_id: str,
    format_selector: str,
    use_cookies: bool,
    output_dir: Path | None = None,
    force_local: bool = False,
    merge_output_format: str | None = None,
    extract_audio_codec: str | None = None,
) -> Path | str:
    """Локальный fallback на `python -m yt_dlp`, если встроенный API дал сбой."""
    output_path_template = _resolve_output_template(session_id, output_dir)
    cookiefile = _cookiefile_if_available(use_cookies)
    command = _build_cli_download_command(
        url=url,
        output_path_template=output_path_template,
        format_selector=format_selector,
        cookiefile=cookiefile,
        merge_output_format=merge_output_format,
        extract_audio_codec=extract_audio_codec,
    )
    result = run_yt_dlp_cli(command)
    if result.returncode != 0:
        raise RuntimeError(
            "CLI fallback yt-dlp завершился ошибкой: "
            f"{(result.stderr or result.stdout).strip()[:1000]}"
        )

    downloaded_file = extract_cli_output_path(result.stdout)
    if not downloaded_file:
        raise RuntimeError("CLI fallback yt-dlp не вернул путь к итоговому файлу.")

    if extract_audio_codec != "mp3":
        downloaded_file = _convert_webm_if_needed(downloaded_file, session_id)
    return _maybe_upload_large_file(downloaded_file, force_local)


def download_video(
    url: str, 
    format_id: str, 
    session_id: str, 
    output_dir: Path | None = None,
    force_local: bool = False
) -> Path | str:
    logger.info(f"Скачивание видео: {url}, формат: {format_id}")
    output_path_template = _resolve_output_template(session_id, output_dir)
    fallback_non_hls = (
        "bestvideo[protocol!=m3u8_dash][protocol!=http_dash_segments]"
        "+bestaudio[protocol!=m3u8_dash][protocol!=http_dash_segments]/"
        f"best[protocol!=m3u8_dash][protocol!=http_dash_segments][ext=mp4][filesize<=?{MAX_FILE_SIZE}]"
    )

    def _resolve_format_selector(
        *,
        prefer_non_hls: bool = False,
        override_format: str | None = None,
    ) -> str:
        format_to_use = override_format or format_id
        if prefer_non_hls:
            logger.info("Фолбек на non-HLS формат: %s", fallback_non_hls)
            return fallback_non_hls

        if not override_format and '+' in format_to_use:
            logger.info("Комбинированный формат %s, добавляем приоритет русского аудио", format_to_use)
            parts = format_to_use.split('+')
            if len(parts) == 2:
                video_id, audio_id = parts
                audio_base = audio_id.split('-')[0] if '-' in audio_id else audio_id
                format_to_use = f"{video_id}+({audio_base}-1/{audio_base}-0/{audio_id})"
                logger.info("Итоговый combined selector: %s", format_to_use)
        elif not override_format and not force_local and '[' not in format_to_use:
            format_to_use = f"{format_to_use}[filesize<=?{MAX_FILE_SIZE}]"
            logger.info("Применяем фильтр по размеру: %s", format_to_use)

        return format_to_use

    def _download(use_cookies: bool, prefer_non_hls: bool = False, override_format: str | None = None) -> Path | str:
        ydl_opts = {
            'format': _resolve_format_selector(
                prefer_non_hls=prefer_non_hls,
                override_format=override_format,
            ),
            'outtmpl': str(output_path_template),
            'quiet': False,
            'no_warnings': True,
            'progress_hooks': [lambda d: logger.debug(f"Скачивание: {d['status']} - {d.get('_percent_str', '0%')}")],
            'merge_output_format': 'mp4',
        }
        _apply_network_opts(ydl_opts)
        cookiefile = _cookiefile_if_available(use_cookies)
        if cookiefile:
            logger.info("Использование файла cookies для скачивания: %s", cookiefile)
            ydl_opts['cookiefile'] = cookiefile
        elif use_cookies:
            logger.warning("Файл cookies указан (%s), но не найден. Скачивание будет без cookies.", YOUTUBE_COOKIES_FILE)
        else:
            logger.info("Пробуем скачать видео без cookies.")

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            downloaded_file = Path(ydl.prepare_filename(info))
            if not downloaded_file.exists():
                raise Exception("Файл не был загружен, хотя ydl.extract_info завершился.")
            logger.info("Видео успешно скачано. Файл: %s", downloaded_file)
            downloaded_file = _convert_webm_if_needed(downloaded_file, session_id)
            result = _maybe_upload_large_file(downloaded_file, force_local)
            logger.info("Видео готово к выдаче: %s", result)
            return result

    def _try_with_fallback(use_cookies: bool) -> Path | str:
        try:
            return _download(use_cookies)
        except yt_dlp.utils.DownloadError as e:
            message = str(e)
            if '403' in message or 'HTTP Error 403' in message or 'fragment' in message:
                logger.warning("Пробуем non-HLS фолбек после 403/fragment ошибки")
                return _download(use_cookies, prefer_non_hls=True)
            if 'Requested format is not available' in message:
                logger.warning("Формат недоступен, пробуем generic bestvideo+bestaudio/best")
                return _download(use_cookies, override_format="bestvideo+bestaudio/best")
            raise

    final_error: Exception | None = None
    if YOUTUBE_COOKIES_FILE and Path(YOUTUBE_COOKIES_FILE).is_file():
        logger.info("Используем cookies для YouTube с первой попытки")
        try:
            return _execute_with_backoff(
                "Скачивание видео с cookies",
                lambda: _try_with_fallback(True),
            )
        except (yt_dlp.utils.DownloadError, yt_dlp.cookies.CookieLoadError, FileNotFoundError, PermissionError) as e:
            final_error = e
            logger.warning("Ошибка с cookies: %s. Пробуем без cookies как fallback...", e)

    else:
        logger.warning("Cookies не найдены, скачиваем без авторизации")

    try:
        return _execute_with_backoff(
            "Скачивание видео без cookies",
            lambda: _try_with_fallback(False),
        )
    except (yt_dlp.utils.DownloadError, yt_dlp.cookies.CookieLoadError, FileNotFoundError, PermissionError) as e:
        final_error = e
        logger.error("Ошибка скачивания видео встроенным API: %s", e, exc_info=True)

    if YTDLP_CLI_FALLBACK:
        error_kind = _classify_download_error_kind(str(final_error or ""))
        if error_kind != 'ACCESS_RESTRICTED':
            logger.warning("Переключаемся на локальный CLI fallback yt-dlp")
            cli_overrides: list[tuple[bool, str | None, bool]] = [
                (True, None, False),
                (False, None, False),
                (True, "bestvideo+bestaudio/best", False),
                (False, "bestvideo+bestaudio/best", False),
                (True, None, True),
                (False, None, True),
            ]
            for use_cookies, override_format, prefer_non_hls in cli_overrides:
                if use_cookies and not (YOUTUBE_COOKIES_FILE and Path(YOUTUBE_COOKIES_FILE).is_file()):
                    continue
                try:
                    return _download_with_cli_fallback(
                        url=url,
                        session_id=session_id,
                        format_selector=_resolve_format_selector(
                            prefer_non_hls=prefer_non_hls,
                            override_format=override_format,
                        ),
                        use_cookies=use_cookies,
                        output_dir=output_dir,
                        force_local=force_local,
                        merge_output_format='mp4',
                    )
                except Exception as cli_error:
                    logger.warning("CLI fallback не удался: %s", cli_error)

    if final_error:
        raise final_error
    raise RuntimeError("Не удалось скачать видео: неизвестная ошибка")

def download_audio_native(url: str, format_id: str, session_id: str, force_local: bool = False, output_dir: Path | None = None) -> Path | str:
    """
    Скачивает только аудио в оригинальном формате (m4a/ogg) БЕЗ конвертации.
    Для нативного воспроизведения в Telegram.
    
    Args:
        url: URL YouTube видео
        format_id: ID формата аудио
        session_id: ID сессии
        force_local: Принудительное локальное сохранение
        output_dir: Директория для сохранения
    
    Returns:
        Path к аудио файлу или ссылка на Gokapi
    """
    logger.info(f"Скачивание нативного аудио: {url}, формат: {format_id}")
    output_path_template = _resolve_output_template(session_id, output_dir)

    def _resolve_native_audio_selector(override_format: str | None = None) -> str:
        effective_format = override_format or format_id
        if not override_format and not force_local and '[' not in effective_format and '+' not in effective_format:
            filtered = f"{effective_format}[filesize<=?{MAX_FILE_SIZE}]"
            logger.info("Применяем фильтр по размеру для нативного аудио: %s", filtered)
            return filtered
        return effective_format

    def _download_audio_native(use_cookies: bool, override_format: str | None = None) -> Path | str:
        ydl_opts = {
            'format': _resolve_native_audio_selector(override_format),
            'outtmpl': str(output_path_template),
            'quiet': False,
            'no_warnings': True,
            'progress_hooks': [lambda d: logger.debug(f"Скачивание нативного аудио: {d['status']} - {d.get('_percent_str', '0%')}")],
        }
        _apply_network_opts(ydl_opts)

        cookiefile = _cookiefile_if_available(use_cookies)
        if cookiefile:
            logger.info("Использование файла cookies для скачивания нативного аудио: %s", cookiefile)
            ydl_opts['cookiefile'] = cookiefile
        elif use_cookies:
            logger.warning("Файл cookies указан (%s), но не найден.", YOUTUBE_COOKIES_FILE)
        else:
            logger.info("Пробуем скачать нативное аудио без cookies.")

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            downloaded_file = Path(ydl.prepare_filename(info))
            if not downloaded_file.exists():
                raise Exception("Аудио файл не был создан.")
            logger.info("Нативное аудио успешно скачано: %s", downloaded_file)
            return _maybe_upload_large_file(downloaded_file, force_local)

    def _try_audio_native(use_cookies: bool) -> Path | str:
        try:
            return _download_audio_native(use_cookies)
        except yt_dlp.utils.DownloadError as e:
            if 'Requested format is not available' in str(e):
                logger.warning("Формат нативного аудио недоступен, пробуем generic bestaudio")
                return _download_audio_native(use_cookies, override_format="bestaudio")
            raise

    final_error: Exception | None = None
    try:
        return _execute_with_backoff(
            "Скачивание нативного аудио без cookies",
            lambda: _try_audio_native(False),
        )
    except (yt_dlp.utils.DownloadError, yt_dlp.cookies.CookieLoadError, FileNotFoundError, PermissionError) as e:
        final_error = e
        logger.warning("Ошибка скачивания нативного аудио без cookies: %s. Пробуем с cookies...", e)

    if YOUTUBE_COOKIES_FILE and Path(YOUTUBE_COOKIES_FILE).is_file():
        try:
            return _execute_with_backoff(
                "Скачивание нативного аудио с cookies",
                lambda: _try_audio_native(True),
            )
        except (yt_dlp.utils.DownloadError, yt_dlp.cookies.CookieLoadError, FileNotFoundError, PermissionError) as e:
            final_error = e
            logger.error("Ошибка при скачивании нативного аудио даже с cookies: %s", e, exc_info=True)

    if YTDLP_CLI_FALLBACK and _classify_download_error_kind(str(final_error or "")) != 'ACCESS_RESTRICTED':
        logger.warning("Переключаемся на CLI fallback для нативного аудио")
        for use_cookies, override_format in ((False, None), (True, None), (False, "bestaudio"), (True, "bestaudio")):
            if use_cookies and not (YOUTUBE_COOKIES_FILE and Path(YOUTUBE_COOKIES_FILE).is_file()):
                continue
            try:
                return _download_with_cli_fallback(
                    url=url,
                    session_id=session_id,
                    format_selector=_resolve_native_audio_selector(override_format),
                    use_cookies=use_cookies,
                    output_dir=output_dir,
                    force_local=force_local,
                )
            except Exception as cli_error:
                logger.warning("CLI fallback нативного аудио не удался: %s", cli_error)

    if final_error:
        raise final_error
    raise RuntimeError("Не удалось скачать нативное аудио: неизвестная ошибка")

def download_audio(url: str, format_id: str, session_id: str, force_local: bool = False, output_dir: Path | None = None, preferred_codec: str = 'mp3') -> Path | str:
    """
    Скачивает только аудио и конвертирует через FFmpegExtractAudio.
    Оптимизировано: использует yt-dlp postprocessor для прямого извлечения аудио.

    Args:
        url: URL YouTube видео
        format_id: ID формата аудио (обычно 'bestaudio' или конкретный ID)
        session_id: ID сессии
        force_local: Принудительное локальное сохранение
        output_dir: Директория для сохранения
        preferred_codec: Выходной аудио-кодек (по умолчанию 'mp3')
    
    Returns:
        Path к MP3 файлу или ссылка на Gokapi
    """
    logger.info(f"Скачивание аудио с конвертацией в MP3: {url}, формат: {format_id}")
    output_path_template = _resolve_output_template(session_id, output_dir)

    def _resolve_audio_selector(override_format: str | None = None) -> str:
        effective_format = override_format or format_id
        if not override_format and not force_local and '[' not in effective_format and '+' not in effective_format:
            filtered = f"{effective_format}[filesize<=?{MAX_FILE_SIZE}]"
            logger.info("Применяем фильтр по размеру для аудио: %s", filtered)
            return filtered
        if '+' in effective_format:
            logger.info("Комбинированный формат аудио %s - фильтр не применяется", effective_format)
        return effective_format

    def _download_audio(use_cookies: bool, override_format: str | None = None) -> Path | str:
        ydl_opts = {
            'format': _resolve_audio_selector(override_format),
            'outtmpl': str(output_path_template),
            'quiet': False,
            'no_warnings': True,
            'postprocessors': [{
                'key': 'FFmpegExtractAudio',
                'preferredcodec': preferred_codec,
                'preferredquality': '192',
            }],
            'progress_hooks': [lambda d: logger.debug(f"Скачивание аудио: {d['status']} - {d.get('_percent_str', '0%')}")],
        }
        _apply_network_opts(ydl_opts)

        cookiefile = _cookiefile_if_available(use_cookies)
        if cookiefile:
            logger.info("Использование файла cookies для скачивания аудио: %s", cookiefile)
            ydl_opts['cookiefile'] = cookiefile
        elif use_cookies:
            logger.warning("Файл cookies указан (%s), но не найден. Скачивание аудио будет без cookies.", YOUTUBE_COOKIES_FILE)
        else:
            logger.info("Пробуем скачать аудио без cookies.")

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            base_filename = Path(ydl.prepare_filename(info))
            downloaded_file = base_filename.with_suffix(f'.{preferred_codec}')
            if not downloaded_file.exists():
                raise Exception("Аудио файл не был создан после postprocessing.")
            logger.info("Аудио успешно извлечено и конвертировано в %s: %s", preferred_codec, downloaded_file)
            return _maybe_upload_large_file(downloaded_file, force_local)

    def _try_audio(use_cookies: bool) -> Path | str:
        try:
            return _download_audio(use_cookies)
        except yt_dlp.utils.DownloadError as e:
            if 'Requested format is not available' in str(e):
                logger.warning("Формат аудио недоступен, пробуем generic bestaudio")
                return _download_audio(use_cookies, override_format="bestaudio")
            raise

    final_error: Exception | None = None
    try:
        return _execute_with_backoff(
            "Скачивание аудио без cookies",
            lambda: _try_audio(False),
        )
    except (yt_dlp.utils.DownloadError, yt_dlp.cookies.CookieLoadError, FileNotFoundError, PermissionError) as e:
        final_error = e
        logger.warning("Ошибка скачивания аудио без cookies: %s. Пробуем с cookies...", e)

    if YOUTUBE_COOKIES_FILE and Path(YOUTUBE_COOKIES_FILE).is_file():
        try:
            return _execute_with_backoff(
                "Скачивание аудио с cookies",
                lambda: _try_audio(True),
            )
        except (yt_dlp.utils.DownloadError, yt_dlp.cookies.CookieLoadError, FileNotFoundError, PermissionError) as e:
            final_error = e
            logger.error("Ошибка при скачивании аудио даже с cookies: %s", e, exc_info=True)

    if YTDLP_CLI_FALLBACK and _classify_download_error_kind(str(final_error or "")) != 'ACCESS_RESTRICTED':
        logger.warning("Переключаемся на CLI fallback для %s-аудио", preferred_codec)
        for use_cookies, override_format in ((False, None), (True, None), (False, "bestaudio"), (True, "bestaudio")):
            if use_cookies and not (YOUTUBE_COOKIES_FILE and Path(YOUTUBE_COOKIES_FILE).is_file()):
                continue
            try:
                return _download_with_cli_fallback(
                    url=url,
                    session_id=session_id,
                    format_selector=_resolve_audio_selector(override_format),
                    use_cookies=use_cookies,
                    output_dir=output_dir,
                    force_local=force_local,
                    extract_audio_codec=preferred_codec,
                )
            except Exception as cli_error:
                logger.warning("CLI fallback %s-аудио не удался: %s", preferred_codec, cli_error)

    if final_error:
        raise final_error
    raise RuntimeError("Не удалось скачать аудио: неизвестная ошибка")

def download_subtitles(url: str, session_id: str, output_dir: Path | None = None) -> Path | None:
    """
    Скачивает субтитры в формате SRT.
    Приоритет: русские -> английские -> первые доступные.
    
    Args:
        url: URL YouTube видео
        session_id: ID сессии
        output_dir: Директория для сохранения
    
    Returns:
        Path к SRT файлу или None если субтитры недоступны
    """
    logger.info(f"Скачивание субтитров: {url}")
    
    def _download_subs(use_cookies: bool) -> Path | None:
        if output_dir is None:
            output_path_template = get_temp_file_path(session_id, "%(title)s.%(ext)s")
        else:
            output_path_template = output_dir / "%(title)s.%(ext)s"
        
        ydl_opts = {
            'skip_download': True,  # Не скачиваем видео
            'writesubtitles': True,  # Скачиваем субтитры
            'writeautomaticsub': True,  # Включаем автоматические субтитры
            'subtitlesformat': 'srt',  # Формат SRT
            'outtmpl': str(output_path_template),
            'quiet': False,
            'no_warnings': True,
        }
        _apply_network_opts(ydl_opts)
        
        if use_cookies and YOUTUBE_COOKIES_FILE and Path(YOUTUBE_COOKIES_FILE).is_file():
            logger.info(f"Использование cookies для субтитров: {YOUTUBE_COOKIES_FILE}")
            ydl_opts['cookiefile'] = YOUTUBE_COOKIES_FILE
        
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            
            # Проверяем доступные субтитры
            available_subs = info.get('subtitles', {})
            auto_subs = info.get('automatic_captions', {})
            
            # Объединяем ручные и автоматические субтитры
            all_subs = {**auto_subs, **available_subs}
            
            if not all_subs:
                logger.warning("Субтитры не найдены для этого видео")
                return None
            
            # Определяем приоритетный язык: ru -> en -> первый доступный
            lang = None
            if 'ru' in all_subs:
                lang = 'ru'
                logger.info("Найдены русские субтитры")
            elif 'en' in all_subs:
                lang = 'en'
                logger.info("Найдены английские субтитры")
            else:
                lang = list(all_subs.keys())[0]
                logger.info(f"Используем субтитры на языке: {lang}")
            
            # Настраиваем скачивание конкретного языка
            ydl_opts['subtitleslangs'] = [lang]
            
            # Скачиваем субтитры
            with yt_dlp.YoutubeDL(ydl_opts) as ydl_download:
                ydl_download.download([url])
            
            # Формируем путь к файлу субтитров
            base_filename = Path(ydl.prepare_filename(info))
            subtitle_file = base_filename.with_suffix(f'.{lang}.srt')
            
            if not subtitle_file.exists():
                logger.error(f"Файл субтитров не найден: {subtitle_file}")
                return None
            
            logger.info(f"Субтитры успешно скачаны: {subtitle_file}")
            return subtitle_file
    
    try:
        return _download_subs(False)
    except Exception as e:
        logger.warning(f"Ошибка скачивания субтитров без cookies: {e}. Пробуем с cookies...")
        if not (YOUTUBE_COOKIES_FILE and Path(YOUTUBE_COOKIES_FILE).is_file()):
            raise
        try:
            return _download_subs(True)
        except Exception as e2:
            logger.error(f"Ошибка скачивания субтитров даже с cookies: {e2}", exc_info=True)
            raise
