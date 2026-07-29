"""Неполный набор auth-cookies обязан быть виден до того, как всё сломается.

Замер с прода: в рабочем файле YouTube осталась одна auth-cookie из шести, и всё
это время статус был зелёным. Сетевая проба тут не помощник — она отвечает на
вопрос «скачивается ли прямо сейчас», а скачиваться может и анонимно: то же
видео в разные часы одного дня и отбивалось с бот-чеком, и извлекалось без
cookies вовсе. Полнота набора — сигнал устойчивый, его и проверяем.
"""

import pytest

from utils import cookie_health


pytestmark = pytest.mark.unit

HEADER = "# Netscape HTTP Cookie File\n"


def _cookie_file(tmp_path, names):
    path = tmp_path / "www.youtube.com_cookies.txt"
    rows = "".join(
        f".youtube.com\tTRUE\t/\tTRUE\t0\t{name}\tvalue\n" for name in names
    )
    path.write_text(HEADER + rows, encoding="utf-8")
    return path


@pytest.fixture(autouse=True)
def clear_cache():
    cookie_health._COOKIE_HEALTH_CACHE.clear()
    yield
    cookie_health._COOKIE_HEALTH_CACHE.clear()


@pytest.fixture
def probe_calls(monkeypatch):
    calls = []
    monkeypatch.setattr(
        cookie_health,
        "_probe_authenticated_session",
        lambda platform, path: calls.append(platform) or "valid",
    )
    return calls


def test_eroded_set_is_reported_as_degraded(tmp_path, monkeypatch, probe_calls):
    """Ровно замеренный случай: осталась одна auth-cookie из шести."""
    path = _cookie_file(tmp_path, ["__Secure-3PSID", "PREF", "YSC"])
    monkeypatch.setitem(cookie_health.COOKIE_PATHS, "youtube", path)

    result = cookie_health.check_cookie_health("youtube", force=True)

    assert result.status == "degraded"
    assert result.auth_cookie_count == 1


def test_degraded_verdict_does_not_waste_a_network_probe(
    tmp_path, monkeypatch, probe_calls
):
    """Набор уже неполон — идти в сеть незачем, вердикт от этого не изменится."""
    path = _cookie_file(tmp_path, ["__Secure-3PSID"])
    monkeypatch.setitem(cookie_health.COOKIE_PATHS, "youtube", path)

    cookie_health.check_cookie_health("youtube", force=True)

    assert probe_calls == []


def test_full_set_is_probed_as_before(tmp_path, monkeypatch, probe_calls):
    path = _cookie_file(
        tmp_path,
        ["SID", "HSID", "SSID", "SAPISID", "__Secure-1PSID", "__Secure-3PSID"],
    )
    monkeypatch.setitem(cookie_health.COOKIE_PATHS, "youtube", path)

    result = cookie_health.check_cookie_health("youtube", force=True)

    assert probe_calls == ["youtube"]
    assert result.status == "valid"
    assert result.auth_cookie_count == 6


def test_most_of_the_set_still_counts_as_healthy(tmp_path, monkeypatch, probe_calls):
    """Экспорты слегка различаются — нельзя дёргать админа из-за одной cookie."""
    path = _cookie_file(
        tmp_path, ["SID", "HSID", "SSID", "SAPISID", "__Secure-1PSID"]
    )
    monkeypatch.setitem(cookie_health.COOKIE_PATHS, "youtube", path)

    assert cookie_health.check_cookie_health("youtube", force=True).status == "valid"


def test_admin_panel_can_render_the_new_status():
    """Статус, который админ увидит как сырое английское слово, бесполезен."""
    from utils import cookie_manager

    source = __import__("inspect").getsource(cookie_manager._build_cookie_health_text)

    assert '"degraded"' in source
    assert cookie_manager._format_health_icon("degraded") != ""
