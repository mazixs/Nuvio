"""Проба cookies обязана мерить то, что действительно важно.

История двух ошибок подряд, обе замерены на проде.

Сначала проба читала 8192 байта тела и не находила признаков
неавторизованности, потому что на странице размером 583 077 байт они лежали на
позициях 18107, 18391 и 510926. Админу сообщалось «probe succeeded», пока
YouTube отбивал каждую ссылку.

Затем выяснилось, что для YouTube HTTP-проба непригодна в принципе: с этого
сервера `/feed/subscriptions` отдаёт стену согласия `consent.youtube.com`
**независимо от авторизации**, и на самой этой странице есть и
`accounts.google.com`, и `ServiceLogin`. С полным рабочим набором cookies, на
котором скачивание заведомо работает, проба всё равно рапортовала `stale`.

Поэтому cookies YouTube проверяются тем же способом, которым ими пользуются, —
попыткой извлечь видео. Для Instagram HTTP-проба работает и остаётся.
"""

import urllib.request

import pytest

from utils import cookie_health


pytestmark = pytest.mark.unit

MEASURED_MARKER_OFFSET = 18107


@pytest.fixture
def cookie_file(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    path = tmp_path / "cookies.txt"
    # Набор полный: иначе вердикт станет `degraded` ещё до сетевой пробы, и
    # тесты пробы будут проверять не то, что заявляют.
    rows = "".join(
        f".instagram.com\tTRUE\t/\tTRUE\t0\t{name}\tvalue\n"
        for name in ("sessionid", "csrftoken", "ds_user_id")
    )
    path.write_text("# Netscape HTTP Cookie File\n" + rows, encoding="utf-8")
    return path


@pytest.fixture(autouse=True)
def clear_cache():
    cookie_health._COOKIE_HEALTH_CACHE.clear()
    yield
    cookie_health._COOKIE_HEALTH_CACHE.clear()


# --- проба YouTube: реальное извлечение --------------------------------------


def _youtube_probe(monkeypatch, cookie_file, failure: Exception | None) -> str:
    def _extract(url, cookiefile):
        if failure:
            raise failure

    monkeypatch.setattr(cookie_health, "_extract_with_cookies", _extract)
    return cookie_health._probe_authenticated_session("youtube", cookie_file)


def test_successful_extraction_means_cookies_work(monkeypatch, cookie_file):
    assert _youtube_probe(monkeypatch, cookie_file, None) == "valid"


def test_bot_check_means_cookies_are_stale(monkeypatch, cookie_file):
    """Ровно то сообщение, которым YouTube отбивал бота на проде."""
    failure = Exception(
        "ERROR: [youtube] q_kjm-MPlps: Sign in to confirm you're not a bot. "
        "Use --cookies-from-browser or --cookies for the authentication."
    )

    assert _youtube_probe(monkeypatch, cookie_file, failure) == "stale"


def test_rate_limit_is_not_blamed_on_cookies(monkeypatch, cookie_file):
    failure = Exception("HTTP Error 429: Too Many Requests")

    assert _youtube_probe(monkeypatch, cookie_file, failure) == "rate_limited"


def test_unrelated_failure_does_not_accuse_the_cookies(monkeypatch, cookie_file):
    """Удалённое видео или сетевой сбой — не повод объявлять cookies мёртвыми.

    Иначе проба превращается в постоянную ложную тревогу, как это уже случилось
    со стеной согласия.
    """
    failure = Exception("Video unavailable. This video has been removed by the uploader")

    assert _youtube_probe(monkeypatch, cookie_file, failure) == "probe_failed"


def test_youtube_no_longer_relies_on_the_consent_walled_page():
    """Стена согласия отдаётся независимо от логина — HTTP-проба тут бесполезна."""
    assert "youtube" not in cookie_health.PROBE_CONFIG
    assert "youtube" in cookie_health.YTDLP_PROBE_URLS


# --- проба Instagram: HTTP, но с достаточным окном чтения --------------------


def _fake_opener(url: str, body: str):
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


def _instagram_probe(monkeypatch, cookie_file, *, url: str, body: str) -> str:
    monkeypatch.setattr(
        urllib.request, "build_opener", lambda *a, **k: _fake_opener(url, body)
    )
    return cookie_health._probe_authenticated_session("instagram", cookie_file)


def test_marker_beyond_the_first_kilobytes_is_found(monkeypatch, cookie_file):
    """Тот же замеренный случай: маркер на позиции 18107, а не в первых 8 КБ."""
    body = "x" * MEASURED_MARKER_OFFSET + "/accounts/login" + "y" * 400_000

    status = _instagram_probe(
        monkeypatch,
        cookie_file,
        url="https://www.instagram.com/accounts/edit/",
        body=body,
    )

    assert status == "stale"


def test_clean_page_counts_as_valid(monkeypatch, cookie_file):
    status = _instagram_probe(
        monkeypatch,
        cookie_file,
        url="https://www.instagram.com/accounts/edit/",
        body="страница настроек без признаков логина",
    )

    assert status == "valid"


def test_probe_verdict_reaches_the_admin(monkeypatch, cookie_file):
    """Проба может быть честной, но бесполезной, если вердикт её игнорирует."""
    monkeypatch.setitem(cookie_health.COOKIE_PATHS, "instagram", cookie_file)
    monkeypatch.setattr(
        cookie_health, "_probe_authenticated_session", lambda *a: "stale"
    )

    result = cookie_health.check_cookie_health("instagram", force=True)

    assert result.status == "stale"
