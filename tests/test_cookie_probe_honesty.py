"""Проба cookies обязана замечать, что сессия не авторизована.

Замерено на проде. Проба ходила на `/feed/subscriptions`, получала редирект на
`consent.youtube.com`, читала первые 8192 байта страницы размером 583 077 байт
и не находила там ни одного признака неавторизованности — потому что они лежали
дальше:

    accounts.google.com  → позиция 18107
    ServiceLogin         → позиция 18391
    Sign in              → позиция 510926

В итоге админу сообщалось «auth cookies are active and probe succeeded», пока
YouTube отвечал боту «Sign in to confirm you're not a bot» на каждую ссылку.
"""

import urllib.request

import pytest

from utils import cookie_health


pytestmark = pytest.mark.unit

COOKIE_LINE = ".youtube.com\tTRUE\t/\tTRUE\t0\t__Secure-3PSID\tvalue\n"
MEASURED_MARKER_OFFSET = 18107


@pytest.fixture
def cookie_file(tmp_path):
    path = tmp_path / "www.youtube.com_cookies.txt"
    path.write_text(f"# Netscape HTTP Cookie File\n{COOKIE_LINE}", encoding="utf-8")
    return path


@pytest.fixture(autouse=True)
def clear_cache():
    cookie_health._COOKIE_HEALTH_CACHE.clear()
    yield
    cookie_health._COOKIE_HEALTH_CACHE.clear()


def _fake_response(url: str, body: str):
    class _Response:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def geturl(self):
            return url

        def read(self, size=None):
            return body.encode("utf-8")[:size] if size else body.encode("utf-8")

    class _Opener:
        def open(self, _request, timeout=None):
            return _Response()

    return _Opener()


def _probe(monkeypatch, cookie_file, *, url: str, body: str) -> str:
    monkeypatch.setattr(
        urllib.request, "build_opener", lambda *a, **k: _fake_response(url, body)
    )
    return cookie_health._probe_authenticated_session("youtube", cookie_file)


def test_marker_beyond_the_first_kilobytes_is_found(monkeypatch, cookie_file):
    """Ровно тот случай, что был на проде: маркер на позиции 18107."""
    body = "x" * MEASURED_MARKER_OFFSET + "accounts.google.com" + "y" * 400_000

    status = _probe(
        monkeypatch,
        cookie_file,
        url="https://www.youtube.com/feed/subscriptions",
        body=body,
    )

    assert status == "stale"


def test_consent_wall_is_not_an_authenticated_session(monkeypatch, cookie_file):
    """На стену согласия попадает только тот, кто не залогинен."""
    status = _probe(
        monkeypatch,
        cookie_file,
        url="https://consent.youtube.com/m?continue=https%3A%2F%2Fwww.youtube.com",
        body="чистое тело без маркеров",
    )

    assert status == "stale"


def test_logged_in_page_still_counts_as_valid(monkeypatch, cookie_file):
    status = _probe(
        monkeypatch,
        cookie_file,
        url="https://www.youtube.com/feed/subscriptions",
        body="лента подписок без признаков логина",
    )

    assert status == "valid"


def test_status_reaches_the_admin_verdict(monkeypatch, cookie_file):
    """Проба может быть честной, но бесполезной, если вердикт её игнорирует."""
    monkeypatch.setitem(cookie_health.COOKIE_PATHS, "youtube", cookie_file)
    monkeypatch.setattr(
        cookie_health, "_probe_authenticated_session", lambda *a: "stale"
    )

    result = cookie_health.check_cookie_health("youtube", force=True)

    assert result.status == "stale"
