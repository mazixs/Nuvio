"""Cookie health checks for admin diagnostics."""

from __future__ import annotations

import http.cookiejar
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from config import INSTAGRAM_COOKIES_PATH, TIKTOK_COOKIES_PATH, YOUTUBE_COOKIES_PATH
from utils.logger import setup_logger

logger = setup_logger(__name__)

COOKIE_HEALTH_CACHE_TTL_SECONDS = 600
HTTP_PROBE_TIMEOUT_SECONDS = 8
USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/137.0.0.0 Safari/537.36"
)

COOKIE_PATHS = {
    "youtube": YOUTUBE_COOKIES_PATH,
    "instagram": INSTAGRAM_COOKIES_PATH,
    "tiktok": TIKTOK_COOKIES_PATH,
}
AUTH_COOKIE_NAMES = {
    "youtube": {"SID", "HSID", "SSID", "SAPISID", "__Secure-1PSID", "__Secure-3PSID"},
    "instagram": {"sessionid", "csrftoken", "ds_user_id"},
    "tiktok": {"sessionid", "sessionid_ss", "sid_tt", "uid_tt"},
}
PROBE_CONFIG = {
    "youtube": {
        "url": "https://www.youtube.com/feed/subscriptions",
        "unauth_markers": ("accounts.google.com", "ServiceLogin", "Sign in"),
        "rate_limit_markers": ("unusual traffic", "try again later"),
    },
    "instagram": {
        "url": "https://www.instagram.com/accounts/edit/",
        "unauth_markers": ("/accounts/login", "loginForm", "Log in"),
        "rate_limit_markers": ("Please wait a few minutes", "Try again later"),
    },
}


@dataclass(frozen=True)
class CookieHealthResult:
    platform: str
    status: str
    summary: str
    checked_at: float
    auth_cookie_count: int
    active_auth_cookie_count: int


@dataclass
class _CookieHealthCacheEntry:
    result: CookieHealthResult
    file_size: int | None
    file_mtime_ns: int | None


_COOKIE_HEALTH_CACHE: dict[str, _CookieHealthCacheEntry] = {}


def _read_netscape_cookies(file_path: Path) -> list[dict[str, object]]:
    records: list[dict[str, object]] = []
    with file_path.open("r", encoding="utf-8", errors="ignore") as file:
        for raw_line in file:
            line = raw_line.strip()
            if not line:
                continue
            if line.startswith("#HttpOnly_"):
                line = line[len("#HttpOnly_") :]
            elif line.startswith("#"):
                continue

            parts = line.split("\t")
            if len(parts) != 7:
                raise ValueError(f"Invalid Netscape cookie row: {raw_line.rstrip()}")

            expires_raw = parts[4].strip()
            try:
                expires = int(expires_raw)
            except ValueError as exc:
                raise ValueError(f"Invalid cookie expiration value: {expires_raw}") from exc

            records.append(
                {
                    "domain": parts[0],
                    "path": parts[2],
                    "expires": expires,
                    "name": parts[5],
                    "value": parts[6],
                }
            )
    return records


def _get_file_signature(file_path: Path) -> tuple[int | None, int | None]:
    if not file_path.exists():
        return None, None
    stat = file_path.stat()
    return stat.st_size, stat.st_mtime_ns


def _is_cache_valid(platform: str, file_path: Path, now: float) -> bool:
    entry = _COOKIE_HEALTH_CACHE.get(platform)
    if not entry:
        return False
    if now - entry.result.checked_at > COOKIE_HEALTH_CACHE_TTL_SECONDS:
        return False
    file_size, file_mtime_ns = _get_file_signature(file_path)
    return entry.file_size == file_size and entry.file_mtime_ns == file_mtime_ns


def _cache_result(platform: str, file_path: Path, result: CookieHealthResult) -> CookieHealthResult:
    file_size, file_mtime_ns = _get_file_signature(file_path)
    _COOKIE_HEALTH_CACHE[platform] = _CookieHealthCacheEntry(
        result=result,
        file_size=file_size,
        file_mtime_ns=file_mtime_ns,
    )
    return result


def _probe_authenticated_session(platform: str, file_path: Path) -> str:
    config = PROBE_CONFIG.get(platform)
    if not config:
        return "not_supported"

    cookie_jar = http.cookiejar.MozillaCookieJar(str(file_path))
    try:
        cookie_jar.load(ignore_discard=True, ignore_expires=True)
    except (OSError, http.cookiejar.LoadError) as exc:
        logger.warning("Failed to load cookie jar for %s: %s", platform, exc)
        return "probe_failed"

    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cookie_jar))
    request = urllib.request.Request(
        config["url"],
        headers={
            "User-Agent": USER_AGENT,
            "Accept-Language": "en-US,en;q=0.9",
        },
    )

    try:
        with opener.open(request, timeout=HTTP_PROBE_TIMEOUT_SECONDS) as response:
            final_url = response.geturl()
            body = response.read(8192).decode("utf-8", errors="ignore")
    except urllib.error.HTTPError as exc:
        body = exc.read(4096).decode("utf-8", errors="ignore")
        if exc.code in {401, 403}:
            return "stale"
        if exc.code == 429:
            return "rate_limited"
        if any(marker.lower() in body.lower() for marker in config["rate_limit_markers"]):
            return "rate_limited"
        return "probe_failed"
    except OSError as exc:
        logger.warning("Cookie probe failed for %s: %s", platform, exc)
        return "probe_failed"

    final_url_lower = final_url.lower()
    body_lower = body.lower()
    if any(marker.lower() in final_url_lower or marker.lower() in body_lower for marker in config["rate_limit_markers"]):
        return "rate_limited"
    if any(marker.lower() in final_url_lower or marker.lower() in body_lower for marker in config["unauth_markers"]):
        return "stale"
    return "valid"


def _result(
    platform: str,
    status: str,
    summary: str,
    auth_cookie_count: int = 0,
    active_auth_cookie_count: int = 0,
) -> CookieHealthResult:
    return CookieHealthResult(
        platform=platform,
        status=status,
        summary=summary,
        checked_at=time.time(),
        auth_cookie_count=auth_cookie_count,
        active_auth_cookie_count=active_auth_cookie_count,
    )


def check_cookie_health(platform: str, *, force: bool = False) -> CookieHealthResult:
    """Checks the health of a cookie file for the requested platform."""
    if platform not in COOKIE_PATHS:
        raise ValueError(f"Unsupported platform: {platform}")

    file_path = COOKIE_PATHS[platform]
    now = time.time()
    if not force and _is_cache_valid(platform, file_path, now):
        return _COOKIE_HEALTH_CACHE[platform].result

    if not file_path.exists():
        return _cache_result(platform, file_path, _result(platform, "missing", "file not found"))

    try:
        records = _read_netscape_cookies(file_path)
    except ValueError as exc:
        return _cache_result(platform, file_path, _result(platform, "invalid_format", str(exc)))

    if not records:
        return _cache_result(platform, file_path, _result(platform, "invalid_format", "no cookie records found"))

    auth_names = AUTH_COOKIE_NAMES[platform]
    auth_records = [record for record in records if record["name"] in auth_names and record["value"]]
    if not auth_records:
        return _cache_result(
            platform,
            file_path,
            _result(platform, "invalid_format", "required auth cookies are missing"),
        )

    active_auth_records = [
        record
        for record in auth_records
        if int(record["expires"]) <= 0 or int(record["expires"]) > now
    ]
    if not active_auth_records:
        return _cache_result(
            platform,
            file_path,
            _result(
                platform,
                "expired",
                "all auth cookies are expired",
                auth_cookie_count=len(auth_records),
                active_auth_cookie_count=0,
            ),
        )

    probe_status = _probe_authenticated_session(platform, file_path)
    if probe_status == "valid":
        result = _result(
            platform,
            "valid",
            "auth cookies are active and probe succeeded",
            auth_cookie_count=len(auth_records),
            active_auth_cookie_count=len(active_auth_records),
        )
    elif probe_status == "stale":
        result = _result(
            platform,
            "stale",
            "auth cookies exist but authenticated probe failed",
            auth_cookie_count=len(auth_records),
            active_auth_cookie_count=len(active_auth_records),
        )
    elif probe_status == "rate_limited":
        result = _result(
            platform,
            "rate_limited",
            "platform temporarily rate-limited the validation probe",
            auth_cookie_count=len(auth_records),
            active_auth_cookie_count=len(active_auth_records),
        )
    elif probe_status == "not_supported":
        result = _result(
            platform,
            "valid",
            "auth cookies are active; live probe is not available for this platform",
            auth_cookie_count=len(auth_records),
            active_auth_cookie_count=len(active_auth_records),
        )
    else:
        result = _result(
            platform,
            "probe_failed",
            "auth cookies are active but live probe could not complete",
            auth_cookie_count=len(auth_records),
            active_auth_cookie_count=len(active_auth_records),
        )

    return _cache_result(platform, file_path, result)


def check_all_cookie_health(*, force: bool = False) -> dict[str, CookieHealthResult]:
    """Checks cookie health for all supported platforms."""
    return {
        platform: check_cookie_health(platform, force=force)
        for platform in COOKIE_PATHS
    }
