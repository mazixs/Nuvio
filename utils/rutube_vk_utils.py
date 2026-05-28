"""
Модуль для работы с Rutube и VK Video с использованием yt-dlp.
"""

import re
from pathlib import Path
from typing import Any

import yt_dlp
from config import MAX_FILE_SIZE, MAX_VIDEO_DURATION
from utils.logger import setup_logger
from utils.media_processor import convert_webm_to_mp4
from utils.temp_file_manager import get_temp_file_path
from utils.youtube_utils import (
    _apply_network_opts,
    _execute_with_backoff,
    _maybe_upload_large_file,
)

logger = setup_logger(__name__)

RUTUBE_URL_PATTERN = (
    r'(?:https?:\/\/)?(?:www\.)?(?:rutube\.ru|ru\.tube|rutu\.be)'
    r'\/(?:video|(?:embed|play)\/|shorts\/|y\/|a\/)?[a-zA-Z0-9_-]+'
)
VK_URL_PATTERN = (
    r'(?:https?:\/\/)?(?:www\.)?(?:vk\.com|vkvideo\.ru|m\.vk\.com)'
    r'\/(?:video|clip|@[\w.]+|wall-?\d+_\d+)(?:[?#\/].*)?'
)


def is_valid_rutube_url(url: str) -> bool:
    return bool(re.match(RUTUBE_URL_PATTERN, url))


def is_valid_vk_url(url: str) -> bool:
    return bool(re.match(VK_URL_PATTERN, url))


def _get_info(url: str, platform: str) -> dict[str, Any]:
    logger.info(f"Получение информации о {platform} видео: {url}")
    ydl_opts = {
        'quiet': True,
        'no_warnings': True,
        'skip_download': True,
    }
    _apply_network_opts(ydl_opts)
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=False)
        duration = info.get('duration')
        if duration and duration > MAX_VIDEO_DURATION:
            logger.warning(f"Видео слишком длинное: {duration} секунд")
            raise Exception(f"Видео слишком длинное. Максимальная длительность: {MAX_VIDEO_DURATION // 60} минут.")
        logger.info(f"Информация о {platform} видео успешно получена.")
        return info


def get_rutube_info(url: str) -> dict[str, Any]:
    return _get_info(url, "Rutube")


def get_vk_info(url: str) -> dict[str, Any]:
    return _get_info(url, "VK")


def _get_available_formats(video_info: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    formats = video_info.get('formats', [])
    video_formats: list[dict[str, Any]] = []
    audio_formats: list[dict[str, Any]] = []
    combined_formats: list[dict[str, Any]] = []

    for format_info in formats:
        filesize = format_info.get('filesize') or format_info.get('filesize_approx')
        if not format_info.get('height') and not format_info.get('audio_channels'):
            continue
        if filesize and filesize > MAX_FILE_SIZE:
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
                'type': 'video_only',
            })
        elif format_info.get('vcodec') == 'none' and format_info.get('acodec') != 'none':
            audio_formats.append({
                'format_id': format_id,
                'format': format_info.get('format'),
                'ext': format_info.get('ext'),
                'filesize': filesize,
                'type': 'audio_only',
            })
        elif format_info.get('vcodec') != 'none' and format_info.get('acodec') != 'none':
            combined_formats.append({
                'format_id': format_id,
                'format': format_info.get('format'),
                'ext': format_info.get('ext'),
                'height': format_info.get('height'),
                'width': format_info.get('width'),
                'filesize': filesize,
                'type': 'combined',
            })

    video_formats.sort(key=lambda x: x.get('height', 0), reverse=True)
    audio_formats.sort(key=lambda x: x.get('filesize', 0) or 0, reverse=True)
    combined_formats.sort(key=lambda x: x.get('height', 0), reverse=True)

    logger.info(
        f"Найдено форматов: video_only={len(video_formats)}, "
        f"audio_only={len(audio_formats)}, combined={len(combined_formats)}"
    )
    return {
        'video_only': video_formats,
        'audio_only': audio_formats,
        'combined': combined_formats,
    }


def get_available_formats_rutube(video_info: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    return _get_available_formats(video_info)


def get_available_formats_vk(video_info: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    return _get_available_formats(video_info)


def _resolve_output_template(session_id: str, output_dir: Path | None) -> Path:
    if output_dir is None:
        return get_temp_file_path(session_id, "%(title)s.%(ext)s")
    return output_dir / "%(title)s.%(ext)s"


def _convert_webm_if_needed(downloaded_file: Path, session_id: str) -> Path:
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


def _download_video(
    url: str,
    session_id: str,
    output_dir: Path | None = None,
    force_local: bool = False,
    format_selector: str = 'bestvideo+bestaudio/best',
) -> Path | str:
    output_path_template = _resolve_output_template(session_id, output_dir)

    def _download() -> Path | str:
        ydl_opts = {
            'format': format_selector,
            'outtmpl': str(output_path_template),
            'quiet': False,
            'no_warnings': True,
            'merge_output_format': 'mp4',
            'progress_hooks': [lambda d: logger.debug(f"Скачивание: {d['status']} - {d.get('_percent_str', '0%')}")],
        }
        _apply_network_opts(ydl_opts)
        logger.info(f"Скачивание {url} через yt-dlp без cookies, формат: {format_selector}")
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            downloaded_file = Path(ydl.prepare_filename(info))
            if not downloaded_file.exists():
                raise Exception("Файл не был загружен.")
            logger.info(f"Видео успешно скачано: {downloaded_file}")
            downloaded_file = _convert_webm_if_needed(downloaded_file, session_id)
            return _maybe_upload_large_file(downloaded_file, force_local)

    return _execute_with_backoff(f"Скачивание видео {url}", _download)


def download_rutube_video(
    url: str,
    session_id: str,
    output_dir: Path | None = None,
    force_local: bool = False,
) -> Path | str:
    logger.info(f"Скачивание Rutube видео: {url}")
    return _download_video(url, session_id, output_dir, force_local, format_selector='bestvideo+bestaudio/best')


def download_vk_video(
    url: str,
    session_id: str,
    output_dir: Path | None = None,
    force_local: bool = False,
) -> Path | str:
    logger.info(f"Скачивание VK видео: {url}")
    return _download_video(url, session_id, output_dir, force_local, format_selector='best[protocol=https]/best')


def _download_audio(
    url: str,
    session_id: str,
    output_dir: Path | None = None,
    force_local: bool = False,
    preferred_codec: str = 'mp3',
    format_selector: str = 'bestaudio/best',
) -> Path | str:
    output_path_template = _resolve_output_template(session_id, output_dir)

    def _download() -> Path | str:
        ydl_opts = {
            'format': format_selector,
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
        logger.info(f"Скачивание аудио {url} через yt-dlp без cookies, формат: {format_selector}")
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            base_filename = Path(ydl.prepare_filename(info))
            downloaded_file = base_filename.with_suffix(f'.{preferred_codec}')
            if not downloaded_file.exists():
                raise Exception("Аудио файл не был создан после postprocessing.")
            logger.info(f"Аудио успешно извлечено: {downloaded_file}")
            return _maybe_upload_large_file(downloaded_file, force_local)

    return _execute_with_backoff(f"Скачивание аудио {url}", _download)


def download_rutube_audio(
    url: str,
    session_id: str,
    output_dir: Path | None = None,
    force_local: bool = False,
) -> Path | str:
    logger.info(f"Скачивание Rutube аудио: {url}")
    return _download_audio(url, session_id, output_dir, force_local, format_selector='bestaudio/best')


def download_vk_audio(
    url: str,
    session_id: str,
    output_dir: Path | None = None,
    force_local: bool = False,
) -> Path | str:
    logger.info(f"Скачивание VK аудио: {url}")
    return _download_audio(url, session_id, output_dir, force_local, format_selector='bestaudio[protocol=https]/bestaudio/best')
