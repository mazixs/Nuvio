"""Общие примитивы быстрых путей доставки.

Ссылки на медиа приходят от третьей стороны — резолвера TikTok или GraphQL
Instagram. Проверка домена одна для всех платформ намеренно: разойдись эти
реализации, и одна из них рано или поздно начала бы пропускать адрес,
который другая отвергает.
"""

from __future__ import annotations

from urllib.parse import urlparse


class FastPathUnavailable(Exception):
    """Быстрый путь неприменим — вызывающий код обязан использовать yt-dlp."""


def is_allowed_media_url(url: str, allowed_domains: frozenset[str]) -> bool:
    """Проверяет, что ссылка ведёт на разрешённый CDN по HTTPS.

    Сравнение идёт по суффиксу домена, а не по подстроке, иначе хост вида
    ``tiktokcdn-us.com.evil.test`` прошёл бы проверку.
    """
    try:
        parsed = urlparse(url or "")
    except ValueError:
        return False

    if parsed.scheme != "https":
        return False

    host = (parsed.hostname or "").lower().rstrip(".")
    if not host:
        return False

    return any(
        host == domain or host.endswith(f".{domain}") for domain in allowed_domains
    )
