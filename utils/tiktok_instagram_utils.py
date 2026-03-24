"""
Модуль для работы с TikTok и Instagram с использованием yt-dlp.
"""

import json
import mimetypes
import re
import time
from html import unescape
from pathlib import Path
from collections.abc import Callable
from typing import Any
from urllib.parse import urlparse

import httpx
import yt_dlp
from yt_dlp.extractor.instagram import _id_to_pk as _instagram_shortcode_to_pk
from utils.logger import setup_logger
from utils.temp_file_manager import get_temp_file_path
from utils.gokapi_utils import upload_to_gokapi, is_gokapi_configured
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
HTTP_USER_AGENT = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/137.0.0.0 Safari/537.36'
INSTAGRAM_PUBLIC_PAGE_USER_AGENT = HTTP_USER_AGENT
TIKWM_API_URL = "https://www.tikwm.com/api/"
INSTAGRAM_GRAPHQL_URL = "https://www.instagram.com/graphql/query/"
INSTAGRAM_GRAPHQL_WEB_INFO_DOC_ID = "26072308439129654"


class PhotoPostAudioMissingError(Exception):
    """У фото-поста нет отдельной аудиодорожки."""


def is_valid_tiktok_url(url: str) -> bool:
    return bool(re.match(TIKTOK_URL_PATTERN, url))


def is_tiktok_photo_url(url: str) -> bool:
    """Проверяет, указывает ли ссылка на TikTok-фото-пост."""
    return "/photo/" in (url or "").lower()

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


def _resolve_tiktok_url(url: str) -> str:
    """Разворачивает короткие TikTok-ссылки до конечного адреса."""
    try:
        response = httpx.get(
            url,
            headers={"User-Agent": HTTP_USER_AGENT},
            follow_redirects=True,
            timeout=15,
        )
        return str(response.url)
    except Exception as e:
        logger.warning("Не удалось развернуть TikTok URL %s: %s", url, e)
        return url


def _is_tiktok_photo_post_info(info: dict[str, Any] | None) -> bool:
    return bool(info and info.get("_nuvio_tiktok_photo_post"))


def _is_instagram_photo_post_info(info: dict[str, Any] | None) -> bool:
    return bool(info and info.get("_nuvio_instagram_photo_post"))


def _is_instagram_empty_playlist_result(info: dict[str, Any] | None) -> bool:
    if not info:
        return False
    if info.get("_type") != "playlist" and "entries" not in info:
        return False

    entries = [entry for entry in (info.get("entries") or []) if entry]
    if entries:
        return False

    return not bool(info.get("formats"))


def _normalize_filename_component(value: str, fallback: str) -> str:
    cleaned = re.sub(r'[<>:"/\\\\|?*]+', "_", (value or "").strip())
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" .")
    return cleaned[:80] or fallback


def _guess_extension(url: str, default_ext: str) -> str:
    suffix = Path(urlparse(url).path).suffix.lower()
    if suffix and len(suffix) <= 5:
        return suffix
    guessed = mimetypes.guess_extension(mimetypes.guess_type(url)[0] or "")
    if guessed:
        return guessed
    return default_ext


def _download_remote_file(url: str, destination: Path, referer: str | None = None) -> Path:
    with httpx.stream(
        "GET",
        url,
        headers={"User-Agent": HTTP_USER_AGENT, "Referer": referer or "https://www.tiktok.com/"},
        follow_redirects=True,
        timeout=60,
    ) as response:
        response.raise_for_status()
        with destination.open("wb") as file:
            for chunk in response.iter_bytes():
                if chunk:
                    file.write(chunk)
    return destination


def _fetch_tiktok_photo_post_data(url: str) -> dict[str, Any]:
    resolved_url = _resolve_tiktok_url(url)

    def _request() -> dict[str, Any]:
        response = httpx.get(
            TIKWM_API_URL,
            params={"url": resolved_url},
            headers={"User-Agent": HTTP_USER_AGENT},
            follow_redirects=True,
            timeout=20,
        )
        response.raise_for_status()
        payload = response.json()
        if payload.get("code") != 0:
            raise Exception(f"TikTok фото-пост недоступен: {payload.get('msg') or 'неизвестная ошибка'}")
        data = payload.get("data") or {}
        if not data.get("images"):
            raise Exception("Сервис не вернул изображения для TikTok фото-поста.")
        return data

    return _smart_retry(_request, max_attempts=3, context="TikTok photo fallback")


def _build_tiktok_photo_info(url: str, data: dict[str, Any]) -> dict[str, Any]:
    author = data.get("author") or {}
    music_info = data.get("music_info") or {}
    duration = music_info.get("duration") or 0
    title = (data.get("title") or "").strip() or f"TikTok фото-пост {data.get('id', '')}".strip()
    return {
        "id": data.get("id"),
        "title": title,
        "uploader": author.get("unique_id") or author.get("nickname") or "TikTok",
        "duration": int(duration or 0),
        "thumbnail": data.get("cover") or data.get("origin_cover"),
        "webpage_url": _resolve_tiktok_url(url),
        "extractor": "nuvio_tiktok_photo",
        "_nuvio_tiktok_photo_post": True,
        "_nuvio_tiktok_photo_data": data,
        "_nuvio_tiktok_audio_url": data.get("music") or music_info.get("play"),
        "_nuvio_tiktok_images": list(data.get("images") or []),
        "formats": [],
    }


def _collect_tiktok_photo_assets(
    url: str,
    session_id: str,
    cached_info: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], list[Path], Path | None]:
    info = cached_info if _is_tiktok_photo_post_info(cached_info) else None
    if info is None:
        info = _build_tiktok_photo_info(url, _fetch_tiktok_photo_post_data(url))

    title_seed = _normalize_filename_component(str(info.get("title") or "tiktok_photo_post"), "tiktok_photo_post")
    image_paths: list[Path] = []
    for index, image_url in enumerate(info.get("_nuvio_tiktok_images") or [], start=1):
        image_path = get_temp_file_path(session_id, f"{title_seed}_{index:02d}{_guess_extension(image_url, '.jpg')}")
        image_paths.append(_download_remote_file(image_url, image_path))

    audio_url = info.get("_nuvio_tiktok_audio_url")
    audio_path: Path | None = None
    if audio_url:
        audio_path = get_temp_file_path(session_id, f"{title_seed}_audio{_guess_extension(str(audio_url), '.mp3')}")
        audio_path = _download_remote_file(str(audio_url), audio_path)

    return info, image_paths, audio_path


def _extract_instagram_shortcode(url: str) -> str | None:
    match = re.search(r"instagram\.com/(?:[^/?#]+/)?(?:p|tv|reels?)/([^/?#&]+)", url, re.IGNORECASE)
    return match.group(1) if match else None


def _search_html_meta(webpage: str, *, attribute: str, name: str) -> str | None:
    patterns = (
        rf'<meta[^>]+{attribute}="{re.escape(name)}"[^>]+content="([^"]+)"',
        rf'<meta[^>]+content="([^"]+)"[^>]+{attribute}="{re.escape(name)}"',
    )
    for pattern in patterns:
        match = re.search(pattern, webpage, re.IGNORECASE)
        if match:
            return unescape(match.group(1))
    return None


def _extract_instagram_username_from_meta(*values: str | None) -> str | None:
    for value in values:
        if not value:
            continue
        match = re.search(r"-\s*([A-Za-z0-9._]+)\s+on\s+[A-Za-z]+\s+\d{1,2},\s+\d{4}:", value)
        if match:
            return match.group(1)
        match = re.search(r"\(@([A-Za-z0-9._]+)\)", value)
        if match:
            return match.group(1)
    return None


def _extract_instagram_media_id_from_meta(webpage: str) -> str | None:
    app_url = _search_html_meta(webpage, attribute="property", name="al:ios:url")
    if not app_url:
        return None
    match = re.search(r"instagram://media\?id=(\d+)", app_url)
    return match.group(1) if match else None


def _fetch_instagram_photo_page_media(canonical_url: str, shortcode: str) -> dict[str, Any]:
    response = httpx.get(
        canonical_url,
        headers={"User-Agent": INSTAGRAM_PUBLIC_PAGE_USER_AGENT, "Referer": "https://www.instagram.com/"},
        follow_redirects=True,
        timeout=20,
    )
    response.raise_for_status()
    webpage = response.text

    image_url = (
        _search_html_meta(webpage, attribute="property", name="og:image")
        or _search_html_meta(webpage, attribute="name", name="twitter:image")
    )
    if not image_url:
        raise Exception("Instagram не вернул изображение фото-поста.")

    description = (
        _search_html_meta(webpage, attribute="property", name="og:description")
        or _search_html_meta(webpage, attribute="name", name="description")
        or _search_html_meta(webpage, attribute="property", name="og:title")
    )
    title = (
        _search_html_meta(webpage, attribute="property", name="og:title")
        or _search_html_meta(webpage, attribute="name", name="twitter:title")
    )
    username = _extract_instagram_username_from_meta(description, title)
    media_id = _extract_instagram_media_id_from_meta(webpage)

    media: dict[str, Any] = {
        "shortcode": shortcode,
        "display_url": image_url,
        "owner": {"username": username} if username else {},
        "caption": description,
    }
    if title:
        media["title"] = title
    if media_id:
        media["id"] = media_id
    return media


def _is_instagram_no_video_error(error_msg: str) -> bool:
    msg = (error_msg or "").lower()
    return any(signature in msg for signature in (
        "there is no video in this post",
        "no video formats found",
        "фото-пост нужно отправлять",
    ))


def _extract_instagram_description(media: dict[str, Any]) -> str | None:
    caption = media.get("caption")
    if isinstance(caption, dict):
        text = caption.get("text")
        if isinstance(text, str) and text.strip():
            return text.strip()
    elif isinstance(caption, str) and caption.strip():
        return caption.strip()

    edges = ((media.get("edge_media_to_caption") or {}).get("edges") or [])
    for edge in edges:
        node = edge.get("node") or {}
        text = node.get("text")
        if isinstance(text, str) and text.strip():
            return text.strip()
    return None


def _build_instagram_photo_title(media: dict[str, Any], shortcode: str | None) -> str:
    title = media.get("title")
    if isinstance(title, str) and title.strip():
        return title.strip()

    description = _extract_instagram_description(media)
    if description:
        first_line = next((line.strip() for line in description.splitlines() if line.strip()), "")
        if first_line:
            return first_line[:120]

    return f"Instagram пост {shortcode or 'photo'}".strip()


def _choose_best_instagram_image_url(media: dict[str, Any]) -> str | None:
    candidates = list(((media.get("image_versions2") or {}).get("candidates") or []))
    if not candidates:
        candidates = list(media.get("display_resources") or [])

    if candidates:
        best = max(
            candidates,
            key=lambda item: item.get("width") or item.get("config_width") or 0,
        )
        return best.get("url") or best.get("src")

    for key in ("display_url", "thumbnail_src", "thumbnail"):
        value = media.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def _instagram_image_identity(image_url: str) -> str:
    parsed = urlparse(image_url)
    return f"{parsed.netloc}{parsed.path}".lower()


def _iter_instagram_photo_nodes(media: dict[str, Any]) -> list[dict[str, Any]]:
    carousel_media = media.get("carousel_media")
    if isinstance(carousel_media, list) and carousel_media:
        return [
            node for node in carousel_media
            if isinstance(node, dict)
            and not node.get("is_video")
            and not node.get("video_versions")
            and not node.get("video_url")
        ]

    edges = ((media.get("edge_sidecar_to_children") or {}).get("edges") or [])
    if edges:
        return [
            node for edge in edges
            if isinstance(edge, dict)
            for node in [edge.get("node") or {}]
            if isinstance(node, dict)
            and not node.get("is_video")
            and not node.get("video_url")
        ]

    return [media]


def _extract_instagram_photo_images(media: dict[str, Any]) -> list[str]:
    image_urls: list[str] = []
    seen_urls: set[str] = set()

    for node in _iter_instagram_photo_nodes(media):
        image_url = _choose_best_instagram_image_url(node)
        identity = _instagram_image_identity(image_url) if image_url else None
        if image_url and identity and identity not in seen_urls:
            seen_urls.add(identity)
            image_urls.append(image_url)

    return image_urls


def _iter_nested_leaves(value: Any, path: tuple[str, ...] = ()) -> list[tuple[tuple[str, ...], Any]]:
    if isinstance(value, dict):
        items: list[tuple[tuple[str, ...], Any]] = []
        for key, nested_value in value.items():
            items.extend(_iter_nested_leaves(nested_value, (*path, str(key))))
        return items
    if isinstance(value, list):
        items = []
        for index, nested_value in enumerate(value):
            items.extend(_iter_nested_leaves(nested_value, (*path, str(index))))
        return items
    return [(path, value)]


def _extract_instagram_audio_url(media: dict[str, Any]) -> str | None:
    direct_paths = (
        ("clips_metadata", "music_info", "music_asset_info", "progressive_download_url"),
        ("clips_metadata", "music_info", "music_asset_info", "url"),
        ("clips_metadata", "original_sound_info", "progressive_download_url"),
        ("clips_metadata", "original_sound_info", "url"),
        ("music_info", "music_asset_info", "progressive_download_url"),
        ("music_info", "music_asset_info", "url"),
        ("music_metadata", "music_info", "music_asset_info", "progressive_download_url"),
        ("audio_asset_info", "progressive_download_url"),
        ("audio_asset_info", "url"),
        ("audio_url",),
    )

    for path in direct_paths:
        current: Any = media
        for key in path:
            if not isinstance(current, dict):
                current = None
                break
            current = current.get(key)
        if isinstance(current, str) and current.startswith("http"):
            return current

    for path, value in _iter_nested_leaves(media):
        if not isinstance(value, str) or not value.startswith("http"):
            continue
        normalized_path = ".".join(path).lower()
        if "progressive_download_url" in normalized_path:
            return value
        if any(token in normalized_path for token in ("audio", "music", "sound", "song", "track")) and not any(
            token in normalized_path for token in ("display", "image", "thumbnail", "profile_pic")
        ):
            return value

    return None


def _fetch_public_instagram_graphql_media(canonical_url: str, shortcode: str) -> dict[str, Any]:
    with httpx.Client(
        headers={
            "User-Agent": INSTAGRAM_PUBLIC_PAGE_USER_AGENT,
            "X-IG-App-ID": "936619743392459",
            "X-ASBD-ID": "359341",
            "Accept": "*/*",
            "X-Requested-With": "XMLHttpRequest",
            "Referer": canonical_url,
        },
        follow_redirects=True,
        timeout=20,
    ) as client:
        response = client.get(
            INSTAGRAM_GRAPHQL_URL,
            params={
                "doc_id": INSTAGRAM_GRAPHQL_WEB_INFO_DOC_ID,
                "variables": json.dumps({"shortcode": shortcode}, separators=(",", ":")),
            },
        )
        response.raise_for_status()
        payload = response.json()

    web_info = ((payload.get("data") or {}).get("xdt_api__v1__media__shortcode__web_info") or {})
    items = web_info.get("items") or []
    if not items:
        raise Exception("Instagram не вернул данные фото-поста.")
    return items[0]


def _fetch_instagram_photo_post_media(url: str) -> dict[str, Any]:
    shortcode = _extract_instagram_shortcode(url)
    if not shortcode:
        raise Exception("Не удалось определить shortcode Instagram поста.")
    canonical_url = f"https://www.instagram.com/p/{shortcode}/"

    product_media = None
    if INSTAGRAM_COOKIES_FILE.exists():
        try:
            with yt_dlp.YoutubeDL({
                "quiet": True,
                "no_warnings": True,
                "cookiefile": str(INSTAGRAM_COOKIES_FILE),
            }) as ydl:
                ie = ydl.get_info_extractor("Instagram")
                if ie._get_cookies(canonical_url).get("sessionid"):
                    payload = ie._download_json(
                        f"{ie._API_BASE_URL}/media/{_instagram_shortcode_to_pk(shortcode)}/info/",
                        shortcode,
                        fatal=False,
                        errnote=False,
                        note="Downloading Instagram photo post info",
                        headers=ie._api_headers,
                    ) or {}
                    items = payload.get("items") or []
                    if items:
                        product_media = items[0]
        except Exception as e:
            logger.debug("Не удалось получить Instagram media/info для фото-поста %s: %s", url, e)

    if product_media and _extract_instagram_photo_images(product_media):
        return product_media

    try:
        media = _smart_retry(
            lambda: _fetch_public_instagram_graphql_media(canonical_url, shortcode),
            max_attempts=3,
            context="Instagram photo metadata",
        )
        if _extract_instagram_photo_images(media):
            return media
    except Exception as graph_error:
        logger.warning("Instagram GraphQL недоступен для %s: %s", url, graph_error)

    media = _smart_retry(
        lambda: _fetch_instagram_photo_page_media(canonical_url, shortcode),
        max_attempts=2,
        context="Instagram photo page",
    )
    if _extract_instagram_photo_images(media):
        return media
    raise Exception("Instagram не вернул изображение фото-поста.")


def _build_instagram_photo_info(url: str, media: dict[str, Any]) -> dict[str, Any]:
    shortcode = _extract_instagram_shortcode(url) or str(media.get("shortcode") or media.get("code") or "")
    owner = media.get("owner") or media.get("user") or {}
    description = _extract_instagram_description(media)
    images = _extract_instagram_photo_images(media)
    if not images:
        raise Exception("Не удалось получить изображения для Instagram фото-поста.")

    duration = media.get("video_duration") or media.get("music_metadata", {}).get("music_duration_in_ms") or 0
    duration = int(float(duration or 0) / 1000) if isinstance(duration, (int, float)) and duration > 1000 else int(float(duration or 0))

    return {
        "id": media.get("id") or shortcode,
        "title": _build_instagram_photo_title(media, shortcode),
        "uploader": owner.get("username") or owner.get("full_name") or "Instagram",
        "duration": duration,
        "thumbnail": images[0],
        "webpage_url": url,
        "description": description,
        "extractor": "nuvio_instagram_photo",
        "_nuvio_instagram_photo_post": True,
        "_nuvio_instagram_photo_data": media,
        "_nuvio_instagram_images": images,
        "_nuvio_instagram_audio_url": _extract_instagram_audio_url(media),
        "formats": [],
    }


def _try_get_instagram_photo_info(url: str) -> dict[str, Any] | None:
    try:
        media = _fetch_instagram_photo_post_media(url)
    except Exception as e:
        logger.debug("Не удалось собрать запасные данные Instagram фото-поста %s: %s", url, e)
        return None

    if media.get("video_url") or media.get("video_versions") or media.get("video_dash_manifest"):
        return None
    if media.get("is_video") is True and not media.get("edge_sidecar_to_children") and not media.get("carousel_media"):
        return None
    if not _extract_instagram_photo_images(media):
        return None
    return _build_instagram_photo_info(url, media)


def _collect_instagram_photo_assets(
    url: str,
    session_id: str,
    cached_info: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], list[Path], Path | None]:
    info = cached_info if _is_instagram_photo_post_info(cached_info) else None
    if info is None:
        info = _build_instagram_photo_info(url, _fetch_instagram_photo_post_media(url))

    title_seed = _normalize_filename_component(str(info.get("title") or "instagram_photo_post"), "instagram_photo_post")
    image_paths: list[Path] = []
    for index, image_url in enumerate(info.get("_nuvio_instagram_images") or [], start=1):
        image_path = get_temp_file_path(session_id, f"{title_seed}_{index:02d}{_guess_extension(image_url, '.jpg')}")
        image_paths.append(_download_remote_file(image_url, image_path, referer="https://www.instagram.com/"))

    audio_url = info.get("_nuvio_instagram_audio_url")
    audio_path: Path | None = None
    if audio_url:
        audio_path = get_temp_file_path(session_id, f"{title_seed}_audio{_guess_extension(str(audio_url), '.m4a')}")
        audio_path = _download_remote_file(str(audio_url), audio_path, referer="https://www.instagram.com/")

    return info, image_paths, audio_path


def _finalize_downloaded_file(file_path: Path, force_local: bool) -> Path | str:
    file_size = file_path.stat().st_size
    if not force_local and file_size > MAX_FILE_SIZE:
        success, link_or_error = upload_to_gokapi(file_path)
        if success:
            try:
                file_path.unlink()
            except Exception as e:
                logger.warning("Не удалось удалить локальный файл %s после загрузки: %s", file_path, e)
            return link_or_error
        raise Exception(f"Сервер загрузки недоступен: {link_or_error}")
    return file_path


def download_tiktok_photo_post_assets(
    url: str,
    session_id: str,
    cached_info: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Скачивает изображения и звук TikTok-фото-поста для поэтапной отправки."""
    info, image_paths, audio_path = _collect_tiktok_photo_assets(url, session_id, cached_info)
    return {
        "info": info,
        "images": image_paths,
        "audio": audio_path,
    }


def download_tiktok_photo_audio(
    url: str,
    session_id: str,
    output_dir: Path | None = None,
    force_local: bool = False,
    cached_info: dict[str, Any] | None = None,
) -> Path | str:
    """Скачивает аудиодорожку TikTok-фото-поста."""
    info, _image_paths, audio_path = _collect_tiktok_photo_assets(url, session_id, cached_info)
    if output_dir is not None:
        logger.debug("output_dir=%s передан для аудио фото-поста, используется временная директория сессии", output_dir)
    if audio_path is None:
        raise PhotoPostAudioMissingError(f"У TikTok фото-поста «{info.get('title') or 'без названия'}» нет отдельной аудиодорожки.")
    return _finalize_downloaded_file(audio_path, force_local)


def download_instagram_photo_post_assets(
    url: str,
    session_id: str,
    cached_info: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Скачивает изображения и звук Instagram фото-поста для поэтапной отправки."""
    info, image_paths, audio_path = _collect_instagram_photo_assets(url, session_id, cached_info)
    return {
        "info": info,
        "images": image_paths,
        "audio": audio_path,
    }


def download_instagram_photo_audio(
    url: str,
    session_id: str,
    output_dir: Path | None = None,
    force_local: bool = False,
    cached_info: dict[str, Any] | None = None,
) -> Path | str:
    """Скачивает аудиодорожку Instagram фото-поста."""
    info, _image_paths, audio_path = _collect_instagram_photo_assets(url, session_id, cached_info)
    if output_dir is not None:
        logger.debug("output_dir=%s передан для аудио фото-поста Instagram, используется временная директория сессии", output_dir)
    if audio_path is None:
        raise PhotoPostAudioMissingError(f"У Instagram фото-поста «{info.get('title') or 'без названия'}» нет отдельной аудиодорожки.")
    return _finalize_downloaded_file(audio_path, force_local)


def get_tiktok_info(url: str) -> dict[str, Any]:
    """
    Получает информацию о TikTok видео с умным retry механизмом.
    
    Args:
        url: URL TikTok видео
    
    Returns:
        Dict с метаданными видео
    """
    logger.info(f"Получение информации о TikTok видео: {url}")

    resolved_url = _resolve_tiktok_url(url)
    if is_tiktok_photo_url(resolved_url):
        logger.info("Определён TikTok фото-пост: %s", resolved_url)
        return _build_tiktok_photo_info(resolved_url, _fetch_tiktok_photo_post_data(resolved_url))
    
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

            if "unsupported url" in error_msg and is_tiktok_photo_url(resolved_url):
                logger.info("yt-dlp не поддержал TikTok фото-пост, используем запасной путь")
                return _build_tiktok_photo_info(resolved_url, _fetch_tiktok_photo_post_data(resolved_url))
            
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
        if _is_instagram_photo_post_info(info):
            return info
        if _is_instagram_empty_playlist_result(info):
            if photo_info := _try_get_instagram_photo_info(url):
                logger.info("Instagram вернул пустой плейлист, переключаемся на фото-пост: %s", url)
                return photo_info
        logger.info("Информация об Instagram видео успешно получена.")
        return info
    except Exception as e:
        error_msg = str(e).lower()
        logger.warning(f"Ошибка получения информации без cookies: {e}")
        if photo_info := _try_get_instagram_photo_info(url):
            logger.info("Определён Instagram фото-пост, используем запасной путь: %s", url)
            return photo_info
        
        # Проверяем на специфичные ошибки Instagram, требующие авторизации
        if any(keyword in error_msg for keyword in ['rate-limit', 'login required', 'not available', 'sign in', 'private']):
            # Пробуем с файлом cookies
            if INSTAGRAM_COOKIES_FILE.exists():
                try:
                    logger.info("Пробуем с cookies файлом...")
                    info = _get_info(True)
                    if _is_instagram_photo_post_info(info):
                        return info
                    if _is_instagram_empty_playlist_result(info):
                        if photo_info := _try_get_instagram_photo_info(url):
                            logger.info("Instagram вернул пустой плейлист после попытки с cookies, переключаемся на фото-пост: %s", url)
                            return photo_info
                    logger.info("Информация об Instagram видео успешно получена с cookies.")
                    return info
                except Exception as e_cookie:
                    if photo_info := _try_get_instagram_photo_info(url):
                        logger.info("Определён Instagram фото-пост после попытки с cookies: %s", url)
                        return photo_info
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
                    if _is_instagram_photo_post_info(info):
                        return info
                    if _is_instagram_empty_playlist_result(info):
                        if photo_info := _try_get_instagram_photo_info(url):
                            logger.info("Instagram вернул пустой плейлист после запасной попытки с cookies, переключаемся на фото-пост: %s", url)
                            return photo_info
                    logger.info("Информация об Instagram видео успешно получена с cookies.")
                    return info
                except Exception as e_cookie:
                    if photo_info := _try_get_instagram_photo_info(url):
                        logger.info("Определён Instagram фото-пост после запасной попытки с cookies: %s", url)
                        return photo_info
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

    if _is_tiktok_photo_post_info(cached_info) or is_tiktok_photo_url(_resolve_tiktok_url(url)):
        raise Exception("TikTok фото-пост нужно отправлять как набор изображений и отдельное аудио.")

    # Предварительная проверка: если размер файла известен и превышает лимит, а Gokapi не настроен — отказ
    if cached_info and not force_local:
        filesize = cached_info.get('filesize') or cached_info.get('filesize_approx', 0)
        if filesize and filesize > MAX_FILE_SIZE and not is_gokapi_configured():
            raise Exception(
                "Файл превышает лимит Telegram (50 МБ). "
                "Выберите формат с меньшим размером."
            )

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
                    raise Exception(f"Сервер загрузки недоступен: {link_or_error}")
            
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


def download_instagram_video(
    url: str,
    session_id: str,
    output_dir: Path | None = None,
    force_local: bool = False,
    cached_info: dict[str, Any] | None = None,
) -> Path | str:
    logger.info(f"Скачивание Instagram видео: {url}")

    if _is_instagram_photo_post_info(cached_info) or _is_instagram_empty_playlist_result(cached_info):
        raise Exception("Instagram фото-пост нужно отправлять как набор изображений и отдельное аудио.")

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
            if _is_instagram_empty_playlist_result(info):
                raise Exception("Instagram фото-пост нужно отправлять как набор изображений и отдельное аудио.")
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
        if _is_instagram_no_video_error(error_msg):
            raise Exception("Instagram фото-пост нужно отправлять как набор изображений и отдельное аудио.") from e
        
        # Проверяем на специфичные ошибки Instagram, требующие авторизации
        if any(keyword in error_msg for keyword in ['rate-limit', 'login required', 'not available', 'sign in', 'private']):
            # Пробуем с файлом cookies
            if INSTAGRAM_COOKIES_FILE.exists():
                try:
                    logger.info("Пробуем скачать с cookies файлом...")
                    return _download(True)
                except Exception as e_cookie:
                    if _is_instagram_no_video_error(str(e_cookie)):
                        raise Exception("Instagram фото-пост нужно отправлять как набор изображений и отдельное аудио.") from e_cookie
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
                    if _is_instagram_no_video_error(str(e_cookie)):
                        raise Exception("Instagram фото-пост нужно отправлять как набор изображений и отдельное аудио.") from e_cookie
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
    if _is_tiktok_photo_post_info(video_info):
        return {
            'video_only': [],
            'audio_only': [],
            'combined': [],
        }

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

    if _is_tiktok_photo_post_info(cached_info) or is_tiktok_photo_url(_resolve_tiktok_url(url)):
        logger.info("Определён TikTok фото-пост, скачиваем только аудио")
        return download_tiktok_photo_audio(url, session_id, output_dir, force_local, cached_info)
    
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
                    raise Exception(f"Сервер загрузки недоступен: {link_or_error}")
            
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


def download_instagram_audio(
    url: str,
    session_id: str,
    output_dir: Path | None = None,
    force_local: bool = False,
    cached_info: dict[str, Any] | None = None,
) -> Path | str:
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

    if _is_instagram_photo_post_info(cached_info) or _is_instagram_empty_playlist_result(cached_info):
        logger.info("Определён Instagram фото-пост, скачиваем только аудио")
        return download_instagram_photo_audio(url, session_id, output_dir, force_local, cached_info)
    
    # Сначала скачиваем видео
    video_file = download_instagram_video(url, session_id, output_dir, force_local=True, cached_info=cached_info)
    
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
