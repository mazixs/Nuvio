"""
Модуль для работы с TikTok и Instagram с использованием yt-dlp.
"""

import glob as glob_module
import re
import time
from pathlib import Path
from collections.abc import Callable
from typing import Any
import yt_dlp
from utils.logger import setup_logger
from utils.temp_file_manager import get_temp_file_path
from utils.gokapi_utils import upload_to_gokapi
from config import INSTAGRAM_COOKIES_PATH, MAX_FILE_SIZE, TIKTOK_COOKIES_PATH

logger = setup_logger(__name__)

TIKTOK_URL_PATTERN = r'(?:https?:\/\/)?(?:(?:www\.|vt\.)?tiktok\.com|vm\.tiktok\.com)\/.+'
INSTAGRAM_URL_PATTERN = r'(?:https?:\/\/)?(?:www\.)?instagram\.com\/.+'
INSTAGRAM_AUDIO_URL_PATTERN = r'(?:https?:\/\/)?(?:www\.)?instagram\.com\/reels\/audio\/\d+\/?'
INSTAGRAM_STORY_URL_PATTERN = r'(?:https?:\/\/)?(?:www\.)?instagram\.com\/stories\/.+'

# Пути к файлам cookies
INSTAGRAM_COOKIES_FILE = INSTAGRAM_COOKIES_PATH
TIKTOK_COOKIES_FILE = TIKTOK_COOKIES_PATH

# Константы для retry механизма
MAX_RETRY_ATTEMPTS = 3
RETRY_DELAY_BASE = 1  # секунды


def is_valid_tiktok_url(url: str) -> bool:
    return bool(re.match(TIKTOK_URL_PATTERN, url))

def is_valid_instagram_url(url: str) -> bool:
    """Проверяет, является ли URL валидной ссылкой Instagram (исключая аудио ссылки)."""
    return bool(re.match(INSTAGRAM_URL_PATTERN, url)) and not is_instagram_audio_url(url)

def is_instagram_audio_url(url: str) -> bool:
    """Проверяет, является ли URL ссылкой на Instagram аудио."""
    return bool(re.match(INSTAGRAM_AUDIO_URL_PATTERN, url))

def is_instagram_story_url(url: str) -> bool:
    """Проверяет, является ли URL ссылкой на Instagram Story."""
    return bool(re.match(INSTAGRAM_STORY_URL_PATTERN, url))


def _smart_retry(func: Callable, max_attempts: int = MAX_RETRY_ATTEMPTS, context: str = "") -> Any:
    """
    Умный retry механизм с экспоненциальной задержкой.
    
    Args:
        func: Функция для выполнения
        max_attempts: Максимальное количество попыток
        context: Контекст для логирования
    
    Returns:
        Результат выполнения функции
    """
    last_exception = None
    
    for attempt in range(1, max_attempts + 1):
        try:
            return func()
        except Exception as e:
            last_exception = e
            error_msg = str(e).lower()
            
            # Проверяем тип ошибки
            if 'rate-limit' in error_msg or 'too many requests' in error_msg:
                if attempt < max_attempts:
                    delay = RETRY_DELAY_BASE * (2 ** (attempt - 1))
                    logger.warning(f"{context} - Rate-limit обнаружен, ожидание {delay}s перед попыткой {attempt + 1}/{max_attempts}")
                    time.sleep(delay)
                    continue
            # SSL/EOF ошибки — не retry, сразу пробрасываем (вызывающий код перейдёт к след. конфигурации)
            elif 'ssl' in error_msg or 'unexpected eof' in error_msg:
                logger.warning(f"{context} - SSL/EOF ошибка, пропускаем конфигурацию: {e}")
                raise
            elif any(keyword in error_msg for keyword in ['blocked', 'forbidden', 'unavailable']):
                logger.error(f"{context} - Критическая ошибка: {e}. Дальнейшие попытки бесполезны.")
                raise
            
            if attempt < max_attempts:
                logger.warning(f"{context} - Попытка {attempt}/{max_attempts} неудачна: {e}")
            else:
                logger.error(f"{context} - Все {max_attempts} попытки неудачны")
    
    raise last_exception


def _get_tiktok_base_configs() -> list[dict]:
    """
    Возвращает оптимизированный список конфигураций для TikTok.
    Только самые эффективные настройки.
    """
    return [
        # Конфигурация 1: Новый API hostname (самая эффективная)
        {
            'quiet': True,
            'no_warnings': True,
            'extractor_args': {
                'tiktok': {
                    'api_hostname': 'api22-normal-c-useast2a.tiktokv.com'
                }
            }
        },
        # Конфигурация 2: С расширенными заголовками (резервная)
        {
            'quiet': True,
            'no_warnings': True,
            'http_headers': {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/137.0.0.0 Safari/537.36',
                'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8',
                'Accept-Language': 'en-US,en;q=0.9',
            },
            'extractor_args': {
                'tiktok': {
                    'api_hostname': 'api16-normal-c-useast1a.tiktokv.com'
                }
            }
        },
        # Конфигурация 3: Базовая (последний fallback)
        {
            'quiet': True,
            'no_warnings': True,
        }
    ]


def get_tiktok_info(url: str) -> dict[str, Any]:
    """
    Получает информацию о TikTok видео с умным retry механизмом.
    
    Args:
        url: URL TikTok видео
    
    Returns:
        Dict с метаданными видео
    """
    logger.info(f"Получение информации о TikTok видео: {url}")
    
    def _try_get_info(use_cookies: bool, config: dict) -> dict[str, Any]:
        """Внутренняя функция для получения информации"""
        opts = config.copy()
        opts['skip_download'] = True
        
        if use_cookies and TIKTOK_COOKIES_FILE.exists():
            opts['cookiefile'] = str(TIKTOK_COOKIES_FILE)
            logger.info(f"Использование cookies: {TIKTOK_COOKIES_FILE}")
        
        with yt_dlp.YoutubeDL(opts) as ydl:
            return ydl.extract_info(url, download=False)
    
    # Получаем оптимизированные конфигурации
    configurations = _get_tiktok_base_configs()
    
    # Стратегия: сначала пробуем с cookies (если есть), затем без
    use_cookies_first = TIKTOK_COOKIES_FILE.exists()
    
    for attempt, config in enumerate(configurations, 1):
        try:
            # Сначала пробуем с cookies, если файл существует
            if use_cookies_first:
                try:
                    logger.info(f"Конфигурация {attempt}/{len(configurations)} с cookies")
                    return _smart_retry(
                        lambda: _try_get_info(True, config),
                        max_attempts=2,
                        context=f"TikTok info (config {attempt}, с cookies)"
                    )
                except Exception as e:
                    logger.warning(f"Конфигурация {attempt} с cookies неудачна: {e}")
            
            # Затем пробуем без cookies
            logger.info(f"Конфигурация {attempt}/{len(configurations)} без cookies")
            return _smart_retry(
                lambda: _try_get_info(False, config),
                max_attempts=2,
                context=f"TikTok info (config {attempt}, без cookies)"
            )
        
        except Exception as e:
            error_msg = str(e).lower()
            logger.warning(f"Конфигурация {attempt} неудачна: {e}")
            
            # Если это последняя конфигурация, выдаем детальную ошибку
            if attempt == len(configurations):
                if any(keyword in error_msg for keyword in ['unable to extract', 'login required', 'blocked', 'unavailable']):
                    if not TIKTOK_COOKIES_FILE.exists():
                        raise Exception(
                            "TikTok ограничил доступ к этому контенту.\n\n"
                            "Возможные причины:\n"
                            "• Превышен лимит запросов (rate-limit)\n"
                            "• Региональные ограничения\n"
                            "• Контент требует авторизации\n\n"
                            "Рекомендации:\n"
                            "• Подождите 5-10 минут перед повторной попыткой\n"
                            "• Добавьте cookies файл в `.secrets/www.tiktok.com_cookies.txt`\n"
                            "• Проверьте, что контент публичный"
                        ) from e
                    else:
                        raise Exception(
                            "TikTok ограничил доступ даже с авторизацией.\n\n"
                            "Возможные причины:\n"
                            "• Превышен лимит запросов\n"
                            "• Региональные блокировки\n\n"
                            "Рекомендации:\n"
                            "• Подождите 10-15 минут\n"
                            "• Обновите cookies файл\n"
                            "• Используйте VPN"
                        ) from e
                raise
            continue
    
    # Этот код не должен достигаться
    raise Exception("Не удалось получить информацию о TikTok видео после всех попыток")


def get_instagram_info(url: str) -> dict[str, Any]:
    logger.info(f"Получение информации об Instagram видео: {url}")
    
    def _get_info(use_cookies: bool) -> dict[str, Any]:
        """Внутренняя функция для получения информации с/без cookies"""
        ydl_opts = {
            'quiet': True,
            'no_warnings': True,
            'skip_download': True,
            'http_headers': {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8',
                'Accept-Language': 'en-US,en;q=0.9',
                'Accept-Encoding': 'gzip, deflate, br',
                'X-IG-App-ID': '936619743392459',  # Instagram Web App ID
                'DNT': '1',
                'Connection': 'keep-alive',
                'Upgrade-Insecure-Requests': '1',
                'Sec-Fetch-Dest': 'document',
                'Sec-Fetch-Mode': 'navigate',
                'Sec-Fetch-Site': 'none',
            }
        }
        
        if use_cookies and INSTAGRAM_COOKIES_FILE.exists():
            ydl_opts['cookiefile'] = str(INSTAGRAM_COOKIES_FILE)
            logger.info(f"Использование файла cookies для Instagram: {INSTAGRAM_COOKIES_FILE}")
        
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            return ydl.extract_info(url, download=False)
    
    # Сначала пробуем без cookies
    try:
        logger.info("Пробуем получить информацию об Instagram видео без cookies.")
        info = _get_info(False)
        logger.info("Информация об Instagram видео успешно получена.")
        return info
    except Exception as e:
        error_msg = str(e).lower()
        logger.warning(f"Ошибка получения информации без cookies: {e}")
        
        # Проверяем на специфичные ошибки Instagram, требующие авторизации
        if any(keyword in error_msg for keyword in ['rate-limit', 'login required', 'not available', 'sign in', 'private']):
            # Пробуем с файлом cookies
            if INSTAGRAM_COOKIES_FILE.exists():
                try:
                    logger.info("Пробуем с cookies файлом...")
                    info = _get_info(True)
                    logger.info("Информация об Instagram видео успешно получена с cookies.")
                    return info
                except Exception as e_cookie:
                    logger.error(f"Ошибка даже с cookies: {e_cookie}")
                    raise Exception(
                        "Instagram ограничил доступ к этому контенту даже с авторизацией. "
                        "Возможные причины:\n"
                        "• Превышен лимит запросов (rate-limit)\n"
                        "• Контент требует специальной авторизации\n"
                        "• Региональные ограничения\n"
                        "• Приватный аккаунт\n\n"
                        "Попробуйте:\n"
                        "• Подождать 5-10 минут перед повторной попыткой\n"
                        "• Обновить файл cookies в `.secrets/www.instagram.com_cookies.txt`\n"
                        "• Использовать другую ссылку\n"
                        "• Проверить, что контент публичный"
                    ) from e_cookie
            else:
                raise Exception(
                    "Instagram ограничил доступ к этому контенту. "
                    "Возможные причины:\n"
                    "• Превышен лимит запросов (rate-limit)\n"
                    "• Контент требует авторизации\n"
                    "• Региональные ограничения\n"
                    "• Приватный аккаунт\n\n"
                    "Попробуйте:\n"
                    "• Подождать некоторое время перед повторной попыткой\n"
                    "• Добавить файл cookies в `.secrets/www.instagram.com_cookies.txt`\n"
                    "• Использовать другую ссылку\n"
                    "• Проверить, что контент публичный"
                ) from e
        else:
            # Для других ошибок пробуем с файлом cookies
            if INSTAGRAM_COOKIES_FILE.exists():
                try:
                    logger.info("Пробуем с cookies файлом для других ошибок...")
                    info = _get_info(True)
                    logger.info("Информация об Instagram видео успешно получена с cookies.")
                    return info
                except Exception as e_cookie:
                    logger.error(f"Ошибка даже с cookies: {e_cookie}")
                    raise
            else:
                raise


def download_tiktok_video(
    url: str, 
    session_id: str, 
    output_dir: Path | None = None, 
    force_local: bool = False,
    cached_info: dict[str, Any] | None = None
) -> Path | str:
    """
    Скачивает TikTok видео с оптимизированной логикой.
    
    Args:
        url: URL TikTok видео
        session_id: ID сессии
        output_dir: Директория для сохранения
        force_local: Принудительное локальное сохранение
        cached_info: Кэшированные метаданные (для пропуска повторного запроса)
    
    Returns:
        Path к файлу или ссылка на Gokapi
    """
    logger.info(f"Скачивание TikTok видео: {url}")
    
    if output_dir is None:
        output_path_template = get_temp_file_path(session_id, "%(title)s.%(ext)s")
    else:
        output_path_template = output_dir / "%(title)s.%(ext)s"
    
    def _download_with_config(use_cookies: bool, config: dict) -> Path | str:
        """Внутренняя функция для скачивания"""
        opts = config.copy()
        opts['outtmpl'] = str(output_path_template)
        opts['quiet'] = False
        opts['no_warnings'] = True
        
        if use_cookies and TIKTOK_COOKIES_FILE.exists():
            opts['cookiefile'] = str(TIKTOK_COOKIES_FILE)
            logger.info(f"Использование cookies для скачивания: {TIKTOK_COOKIES_FILE}")
        
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=True)
            downloaded_file = Path(ydl.prepare_filename(info))
            
            if not downloaded_file.exists():
                raise Exception("Файл не был загружен.")
            
            logger.info(f"Видео успешно скачано: {downloaded_file}")
            file_size = downloaded_file.stat().st_size
            
            # Загрузка на Gokapi при превышении лимита
            if not force_local and file_size > MAX_FILE_SIZE:
                logger.warning(f"Размер {file_size} байт превышает лимит. Загрузка на Gokapi...")
                success, link_or_error = upload_to_gokapi(downloaded_file)
                if success:
                    logger.info(f"Загружено на Gokapi: {link_or_error}")
                    try:
                        downloaded_file.unlink()
                    except Exception as e:
                        logger.error(f"Ошибка удаления локального файла: {e}")
                    return link_or_error
                else:
                    raise Exception(f"Ошибка Gokapi: {link_or_error}")
            
            return downloaded_file
    
    # Получаем оптимизированные конфигурации
    configurations = _get_tiktok_base_configs()
    use_cookies_first = TIKTOK_COOKIES_FILE.exists()
    
    # Пробуем каждую конфигурацию
    for attempt, config in enumerate(configurations, 1):
        try:
            # Сначала с cookies (если есть)
            if use_cookies_first:
                try:
                    logger.info(f"Скачивание: конфигурация {attempt}/{len(configurations)} с cookies")
                    return _smart_retry(
                        lambda: _download_with_config(True, config),
                        max_attempts=2,
                        context=f"TikTok download (config {attempt}, с cookies)"
                    )
                except Exception as e:
                    logger.warning(f"Конфигурация {attempt} с cookies неудачна: {e}")
            
            # Затем без cookies
            logger.info(f"Скачивание: конфигурация {attempt}/{len(configurations)} без cookies")
            return _smart_retry(
                lambda: _download_with_config(False, config),
                max_attempts=2,
                context=f"TikTok download (config {attempt}, без cookies)"
            )
        
        except Exception as e:
            logger.warning(f"Конфигурация {attempt} неудачна: {e}")
            if attempt == len(configurations):
                raise Exception(f"Не удалось скачать TikTok видео после всех попыток. Последняя ошибка: {e}") from e
            continue
    
    raise Exception("Не удалось скачать TikTok видео")


def download_instagram_video(url: str, session_id: str, output_dir: Path | None = None, force_local: bool = False) -> Path | str:
    logger.info(f"Скачивание Instagram видео: {url}")
    if output_dir is None:
        output_path_template = get_temp_file_path(session_id, "%(title)s.%(ext)s")
    else:
        output_path_template = output_dir / "%(title)s.%(ext)s"
    
    def _download(use_cookies: bool) -> Path | str:
        """Внутренняя функция для скачивания с/без cookies"""
        ydl_opts = {
            'outtmpl': str(output_path_template),
            'quiet': False,
            'no_warnings': True,
            'progress_hooks': [lambda d: logger.debug(f"Скачивание: {d['status']} - {d.get('_percent_str', '0%')}")],
            'http_headers': {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8',
                'Accept-Language': 'en-US,en;q=0.9',
                'Accept-Encoding': 'gzip, deflate, br',
                'X-IG-App-ID': '936619743392459',  # Instagram Web App ID
                'DNT': '1',
                'Connection': 'keep-alive',
                'Upgrade-Insecure-Requests': '1',
                'Sec-Fetch-Dest': 'document',
                'Sec-Fetch-Mode': 'navigate',
                'Sec-Fetch-Site': 'none',
            }
        }
        
        if use_cookies and INSTAGRAM_COOKIES_FILE.exists():
            ydl_opts['cookiefile'] = str(INSTAGRAM_COOKIES_FILE)
            logger.info(f"Использование файла cookies для скачивания Instagram: {INSTAGRAM_COOKIES_FILE}")
        
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)

            # Для stories и плейлистов yt-dlp может вернуть entries
            actual_info = info
            if info.get('_type') == 'playlist' or 'entries' in info:
                entries = list(info.get('entries', []))
                if entries:
                    actual_info = entries[0]
                    logger.info(f"Instagram вернул playlist с {len(entries)} записями, используем первую.")

            downloaded_file = Path(ydl.prepare_filename(actual_info))

            # Если файл не найден по prepare_filename, ищем в директории
            if not downloaded_file.exists():
                parent_dir = downloaded_file.parent
                found_files = sorted(
                    parent_dir.glob("*.*"),
                    key=lambda f: f.stat().st_mtime,
                    reverse=True,
                ) if parent_dir.exists() else []
                media_files = [f for f in found_files if f.suffix.lower() in ('.mp4', '.webm', '.mkv', '.mov', '.avi', '.flv')]
                if media_files:
                    downloaded_file = media_files[0]
                    logger.info(f"Файл найден через поиск в директории: {downloaded_file}")
                else:
                    if is_instagram_story_url(url):
                        raise Exception(
                            "Не удалось скачать Instagram Story. "
                            "Stories — это временный контент (24 часа), "
                            "и Instagram ограничивает их загрузку через API. "
                            "К сожалению, скачивание Stories в данный момент не поддерживается."
                        )
                    raise Exception("Файл не был загружен, хотя ydl.extract_info завершился.")
            
            logger.info(f"Видео успешно скачано. Файл: {downloaded_file}")
            file_size = downloaded_file.stat().st_size
            
            if not force_local and file_size > MAX_FILE_SIZE:
                logger.warning(f"Размер файла ({downloaded_file}) превышает лимит: {file_size} байт. Загружаем на Gokapi.")
                success, link_or_error = upload_to_gokapi(downloaded_file)
                if success:
                    logger.info(f"Файл загружен на Gokapi: {link_or_error}")
                    try:
                        downloaded_file.unlink()
                        logger.info(f"Локальный файл {downloaded_file} удален после загрузки на Gokapi.")
                    except Exception as e_del:
                        logger.error(f"Ошибка при удалении локального файла {downloaded_file} после загрузки на Gokapi: {e_del}")
                    return link_or_error
                else:
                    logger.error(f"Не удалось загрузить файл на Gokapi: {link_or_error}")
                    raise Exception(f"Сервер загрузки недоступен: {link_or_error}")
            
            logger.info(f"Видео ({downloaded_file}) успешно загружено (для прямой отправки).")
            return downloaded_file
    
    # Сначала пробуем без cookies
    try:
        logger.info("Пробуем скачать Instagram видео без cookies.")
        return _download(False)
    except Exception as e:
        error_msg = str(e).lower()
        logger.warning(f"Ошибка скачивания без cookies: {e}")
        
        # Проверяем на специфичные ошибки Instagram, требующие авторизации
        if any(keyword in error_msg for keyword in ['rate-limit', 'login required', 'not available', 'sign in', 'private']):
            # Пробуем с файлом cookies
            if INSTAGRAM_COOKIES_FILE.exists():
                try:
                    logger.info("Пробуем скачать с cookies файлом...")
                    return _download(True)
                except Exception as e_cookie:
                    logger.error(f"Ошибка скачивания даже с cookies: {e_cookie}")
                    raise Exception(
                        "Instagram ограничил доступ к этому контенту даже с авторизацией. "
                        "Возможные причины:\n"
                        "• Превышен лимит запросов (rate-limit)\n"
                        "• Контент требует специальной авторизации\n"
                        "• Региональные ограничения\n"
                        "• Приватный аккаунт\n\n"
                        "Попробуйте:\n"
                        "• Подождать 5-10 минут перед повторной попыткой\n"
                        "• Обновить файл cookies в `.secrets/www.instagram.com_cookies.txt`\n"
                        "• Использовать другую ссылку\n"
                        "• Проверить, что контент публичный"
                    ) from e_cookie
            else:
                raise Exception(
                    "Instagram ограничил доступ к этому контенту. "
                    "Для скачивания требуется авторизация. "
                    "Добавьте файл cookies в `.secrets/www.instagram.com_cookies.txt`."
                ) from e
        else:
            # Для других ошибок пробуем с файлом cookies
            if INSTAGRAM_COOKIES_FILE.exists():
                try:
                    logger.info("Пробуем скачать с cookies файлом для других ошибок...")
                    return _download(True)
                except Exception as e_cookie:
                    logger.error(f"Ошибка скачивания даже с cookies: {e_cookie}")
                    raise
            else:
                raise


def get_available_formats_tiktok(video_info: dict) -> dict:
    """
    Получает список доступных форматов TikTok-видео.
    Args:
        video_info (dict): Информация о видео (yt-dlp extract_info).
    Returns:
        dict: Словарь с группами форматов (video_only, audio_only, combined).
    """
    formats = video_info.get('formats', [])
    video_formats = []
    audio_formats = []
    combined_formats = []
    for format_info in formats:
        if not format_info.get('height') and not format_info.get('audio_channels'):
            continue
        format_id = format_info.get('format_id')
        if format_info.get('vcodec') != 'none' and format_info.get('acodec') == 'none':
            video_formats.append({
                'format_id': format_id,
                'format': format_info.get('format'),
                'ext': format_info.get('ext'),
                'height': format_info.get('height'),
                'width': format_info.get('width'),
                'filesize': format_info.get('filesize'),
                'type': 'video_only'
            })
        elif format_info.get('vcodec') == 'none' and format_info.get('acodec') != 'none':
            audio_formats.append({
                'format_id': format_id,
                'format': format_info.get('format'),
                'ext': format_info.get('ext'),
                'filesize': format_info.get('filesize'),
                'type': 'audio_only'
            })
        elif format_info.get('vcodec') != 'none' and format_info.get('acodec') != 'none':
            combined_formats.append({
                'format_id': format_id,
                'format': format_info.get('format'),
                'ext': format_info.get('ext'),
                'height': format_info.get('height'),
                'width': format_info.get('width'),
                'filesize': format_info.get('filesize'),
                'type': 'combined'
            })
    video_formats.sort(key=lambda x: x.get('height', 0), reverse=True)
    audio_formats.sort(key=lambda x: x.get('filesize', 0), reverse=True)
    combined_formats.sort(key=lambda x: x.get('height', 0), reverse=True)
    return {
        'video_only': video_formats,
        'audio_only': audio_formats,
        'combined': combined_formats
    }


def download_tiktok_audio(
    url: str, 
    session_id: str, 
    output_dir: Path | None = None, 
    force_local: bool = False,
    cached_info: dict[str, Any] | None = None
) -> Path | str:
    """
    Скачивает только аудио из TikTok видео в нативном формате M4A (AAC).
    Приоритет: M4A (нативный) > MP3 (fallback при конвертации).
    
    Args:
        url: URL TikTok видео
        session_id: ID сессии
        output_dir: Директория для сохранения
        force_local: Принудительное локальное сохранение
        cached_info: Кэшированные метаданные
    
    Returns:
        Path к M4A файлу или ссылка на Gokapi
    """
    logger.info(f"Скачивание нативного аудио (M4A) из TikTok: {url}")
    
    if output_dir is None:
        output_path_template = get_temp_file_path(session_id, "%(title)s.%(ext)s")
    else:
        output_path_template = output_dir / "%(title)s.%(ext)s"
    
    def _download_audio_with_config(use_cookies: bool, config: dict) -> Path | str:
        """Скачивание аудио с указанной конфигурацией"""
        opts = config.copy()
        opts['outtmpl'] = str(output_path_template)
        # TikTok обычно не имеет отдельного audio-only формата, используем best
        opts['format'] = 'bestaudio/best'
        opts['postprocessors'] = [{
            'key': 'FFmpegExtractAudio',
            'preferredcodec': 'm4a',  # M4A для нативного AAC
            'preferredquality': '192',
        }]
        opts['quiet'] = False
        opts['no_warnings'] = True
        
        if use_cookies and TIKTOK_COOKIES_FILE.exists():
            opts['cookiefile'] = str(TIKTOK_COOKIES_FILE)
            logger.info(f"Использование cookies для аудио: {TIKTOK_COOKIES_FILE}")
        
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=True)
            # После postprocessor файл будет иметь расширение .m4a
            base_filename = Path(ydl.prepare_filename(info))
            downloaded_file = base_filename.with_suffix('.m4a')
            
            if not downloaded_file.exists():
                raise Exception("Аудио файл не был создан.")
            
            logger.info(f"Нативное M4A аудио успешно извлечено: {downloaded_file}")
            file_size = downloaded_file.stat().st_size
            
            # Загрузка на Gokapi при превышении лимита
            if not force_local and file_size > MAX_FILE_SIZE:
                logger.warning(f"Размер {file_size} байт превышает лимит. Загрузка на Gokapi...")
                success, link_or_error = upload_to_gokapi(downloaded_file)
                if success:
                    logger.info(f"Загружено на Gokapi: {link_or_error}")
                    try:
                        downloaded_file.unlink()
                    except Exception as e:
                        logger.error(f"Ошибка удаления: {e}")
                    return link_or_error
                else:
                    raise Exception(f"Ошибка Gokapi: {link_or_error}")
            
            return downloaded_file
    
    # Получаем конфигурации и пробуем скачать
    configurations = _get_tiktok_base_configs()
    use_cookies_first = TIKTOK_COOKIES_FILE.exists()
    
    for attempt, config in enumerate(configurations, 1):
        try:
            # Сначала с cookies
            if use_cookies_first:
                try:
                    logger.info(f"Аудио M4A: конфигурация {attempt}/{len(configurations)} с cookies")
                    return _smart_retry(
                        lambda: _download_audio_with_config(True, config),
                        max_attempts=2,
                        context=f"TikTok audio M4A (config {attempt}, с cookies)"
                    )
                except Exception as e:
                    logger.warning(f"Конфигурация {attempt} с cookies неудачна: {e}")
            
            # Затем без cookies
            logger.info(f"Аудио M4A: конфигурация {attempt}/{len(configurations)} без cookies")
            return _smart_retry(
                lambda: _download_audio_with_config(False, config),
                max_attempts=2,
                context=f"TikTok audio M4A (config {attempt}, без cookies)"
            )
        
        except Exception as e:
            logger.warning(f"Конфигурация {attempt} неудачна: {e}")
            if attempt == len(configurations):
                raise Exception(f"Не удалось скачать аудио после всех попыток. Последняя ошибка: {e}") from e
            continue
    
    raise Exception("Не удалось скачать TikTok аудио")


def download_instagram_audio(url: str, session_id: str, output_dir: Path | None = None, force_local: bool = False) -> Path | str:
    """
    Скачивает только аудио из Instagram видео в нативном формате M4A (AAC).
    Приоритет: M4A с copy (без перекодирования) > MP3 (fallback).
    
    Args:
        url (str): URL Instagram видео.
        session_id (str): Идентификатор сессии.
        output_dir (Optional[Path]): Директория для сохранения.
        force_local (bool): Принудительное локальное сохранение.
        
    Returns:
        Union[Path, str]: Путь к M4A файлу или ссылка на Gokapi.
    """
    import subprocess
    
    logger.info(f"Скачивание нативного аудио (M4A) из Instagram: {url}")
    
    # Сначала скачиваем видео
    video_file = download_instagram_video(url, session_id, output_dir, force_local=True)
    
    # Если получили ссылку вместо файла, возвращаем её
    if isinstance(video_file, str) and video_file.startswith("http"):
        return video_file
    
    # Извлекаем аудио с помощью ffmpeg
    video_path = Path(video_file)
    audio_path_m4a = video_path.with_suffix('.m4a')
    
    try:
        logger.info(f"Извлечение нативного AAC аудио из {video_path} в {audio_path_m4a}")
        
        # Сначала пробуем извлечь AAC без перекодирования (copy)
        cmd_copy = [
            'ffmpeg', '-i', str(video_path),
            '-vn',  # Без видео
            '-acodec', 'copy',  # Копируем аудио без перекодирования
            '-y',  # Перезаписать файл если существует
            str(audio_path_m4a)
        ]
        
        result = subprocess.run(cmd_copy, capture_output=True, text=True)
        
        # Если copy не сработал (не AAC кодек), конвертируем в AAC
        if result.returncode != 0:
            logger.warning(f"Извлечение AAC через copy не удалось, конвертируем в AAC: {result.stderr}")
            cmd_convert = [
                'ffmpeg', '-i', str(video_path),
                '-vn',  # Без видео
                '-acodec', 'aac',  # Кодек AAC
                '-b:a', '192k',  # Битрейт 192k
                '-y',  # Перезаписать файл если существует
                str(audio_path_m4a)
            ]
            
            result_convert = subprocess.run(cmd_convert, capture_output=True, text=True)
            
            if result_convert.returncode != 0:
                logger.error(f"Ошибка конвертации в AAC: {result_convert.stderr}")
                # Fallback на MP3
                logger.warning("Fallback на MP3...")
                audio_path_mp3 = video_path.with_suffix('.mp3')
                cmd_mp3 = [
                    'ffmpeg', '-i', str(video_path),
                    '-vn',
                    '-acodec', 'mp3',
                    '-ab', '192k',
                    '-y',
                    str(audio_path_mp3)
                ]
                result_mp3 = subprocess.run(cmd_mp3, capture_output=True, text=True)
                if result_mp3.returncode != 0:
                    raise Exception(f"Не удалось извлечь аудио даже в MP3: {result_mp3.stderr}")
                audio_path = audio_path_mp3
                logger.info(f"Аудио извлечено в MP3 (fallback): {audio_path}")
            else:
                audio_path = audio_path_m4a
                logger.info(f"Аудио сконвертировано в AAC M4A: {audio_path}")
        else:
            audio_path = audio_path_m4a
            logger.info(f"Нативное AAC аудио извлечено (copy): {audio_path}")
        
        # Удаляем исходное видео
        try:
            video_path.unlink()
            logger.info(f"Исходное видео {video_path} удалено")
        except Exception as e:
            logger.warning(f"Не удалось удалить исходное видео: {e}")
        
        if not audio_path.exists():
            raise Exception("Аудио файл не был создан.")
            
        logger.info(f"Аудио успешно извлечено. Файл: {audio_path}")
        file_size = audio_path.stat().st_size
        
        if not force_local and file_size > MAX_FILE_SIZE:
            logger.warning(f"Размер файла ({audio_path}) превышает лимит: {file_size} байт. Загружаем на Gokapi.")
            success, link_or_error = upload_to_gokapi(audio_path)
            if success:
                logger.info(f"Файл загружен на Gokapi: {link_or_error}")
                try:
                    audio_path.unlink()
                    logger.info(f"Локальный файл {audio_path} удален после загрузки на Gokapi.")
                except Exception as e_del:
                    logger.error(f"Ошибка при удалении локального файла {audio_path} после загрузки на Gokapi: {e_del}")
                return link_or_error
            else:
                logger.error(f"Не удалось загрузить файл на Gokapi: {link_or_error}")
                raise Exception(f"Сервер загрузки недоступен: {link_or_error}")
        
        logger.info(f"Аудио ({audio_path}) успешно загружено (для прямой отправки).")
        return audio_path
        
    except Exception as e:
        # Если что-то пошло не так, удаляем временные файлы
        try:
            if video_path.exists():
                video_path.unlink()
            if audio_path_m4a.exists():
                audio_path_m4a.unlink()
        except Exception:
            pass
        
        # Проверяем на специфичные ошибки Instagram
        error_msg = str(e).lower()
        if any(keyword in error_msg for keyword in ['rate-limit', 'login required', 'not available', 'sign in']):
            raise Exception(
                "Instagram ограничил доступ к этому контенту. "
                "Возможные причины:\n"
                "• Превышен лимит запросов (rate-limit)\n"
                "• Контент требует авторизации\n"
                "• Региональные ограничения\n"
                "• Приватный аккаунт\n\n"
                "Попробуйте:\n"
                "• Подождать некоторое время перед повторной попыткой\n"
                "• Использовать другую ссылку\n"
                "• Проверить, что контент публичный"
            ) from e
        else:
            raise


def handle_instagram_audio_url(url: str) -> str:
    """
    Обрабатывает Instagram аудио ссылки и возвращает информативное сообщение.
    
    Args:
        url (str): URL Instagram аудио.
        
    Returns:
        str: Информативное сообщение об ограничениях.
    """
    logger.info(f"Обработка Instagram аудио ссылки: {url}")
    
    # Извлекаем ID аудио из URL
    audio_id_match = re.search(r'/audio/(\d+)', url)
    audio_id = audio_id_match.group(1) if audio_id_match else "неизвестен"
    
    return f"""🎵 **Instagram Audio - Ограничения**

К сожалению, прямое скачивание аудио по ссылкам вида `/reels/audio/` не поддерживается.

**ID аудио:** `{audio_id}`

**Что можно сделать:**
1. 🔍 Найдите конкретный пост/reel, который использует это аудио
2. 📱 Отправьте мне ссылку на пост (например: `instagram.com/p/...` или `instagram.com/reel/...`)
3. 🎧 Я смогу извлечь аудио из видео поста

**Поддерживаемые форматы ссылок:**
• `instagram.com/p/ABC123/` - обычные посты
• `instagram.com/reel/ABC123/` - reels
• `instagram.com/username/reel/ABC123/` - reels пользователя

**Альтернативные инструменты:**
Для скачивания аудио по таким ссылкам можно использовать специализированные инструменты, такие как gallery-dl или instaloader."""
