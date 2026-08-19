"""Перехват вывода yt-dlp: предупреждения доходят, прогресс не мешает.

18.08.2026 YouTube сломал скачивание по прямым ссылкам, и объяснения отказа
лежали в предупреждениях yt-dlp, а `no_warnings: True` глушил их все. Тесты
закрепляют обратное: предупреждения попадают в лог и в отчёт, строки прогресса
в отчёт не попадают вовсе, а сломанная диагностика не роняет загрузку.
"""

import logging

import pytest
import yt_dlp

from utils import download_report
from utils.ytdlp_common import (
    YtdlpOutputLogger,
    apply_network_opts,
    is_progress_line,
    output_capture_opts,
)

SESSION = "42_capture"
MODULE_LOGGER = "utils.ytdlp_common"

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def clean_registry():
    download_report._OUTPUT.clear()
    yield
    download_report._OUTPUT.clear()


def test_warning_lands_in_the_report():
    YtdlpOutputLogger(SESSION).warning(
        "The provided YouTube account cookies are no longer valid"
    )

    assert download_report.output_tail(SESSION) == [
        "The provided YouTube account cookies are no longer valid"
    ]


def test_error_lands_in_the_report():
    YtdlpOutputLogger(SESSION).error("ERROR: unable to download video data: 403")

    assert download_report.output_tail(SESSION) == [
        "ERROR: unable to download video data: 403"
    ]


def test_progress_does_not_crowd_out_the_warning():
    """Прогресс идёт десятками строк в секунду и вытеснил бы кольцо целиком."""
    adapter = YtdlpOutputLogger(SESSION)
    adapter.warning("Some formats require a GVS PO Token")
    for percent in range(download_report.OUTPUT_TAIL * 3):
        adapter.debug(f"[download]  {percent}% of 10.00MiB at 1.00MiB/s ETA 00:08")

    assert download_report.output_tail(SESSION) == [
        "Some formats require a GVS PO Token"
    ]


def test_numbered_progress_is_dropped_too():
    """Многострочный прогресс yt-dlp нумерует, но это всё тот же счётчик."""
    YtdlpOutputLogger(SESSION).debug("1: [download]  50% of 10.00MiB")

    assert download_report.output_tail(SESSION) == []


def test_useful_screen_output_is_kept():
    """Выбранный клиент и итоговый формат объясняют отказ не хуже предупреждений."""
    adapter = YtdlpOutputLogger(SESSION)
    adapter.debug("[youtube] jNQXAC9IVRw: Downloading visionos player API JSON")
    adapter.debug("[info] jNQXAC9IVRw: Downloading 1 format(s): 137+140")

    assert download_report.output_tail(SESSION) == [
        "[youtube] jNQXAC9IVRw: Downloading visionos player API JSON",
        "[info] jNQXAC9IVRw: Downloading 1 format(s): 137+140",
    ]


def test_progress_line_detection():
    assert is_progress_line("[download]  12.3% of 10.00MiB")
    assert is_progress_line("  [download] Destination: video.mp4")
    assert is_progress_line("2: [download]  99% of 10.00MiB")
    assert not is_progress_line("[youtube] Extracting URL: https://youtu.be/x")
    assert not is_progress_line("WARNING: [download] что-то важное")


def test_warning_reaches_the_module_log(caplog):
    with caplog.at_level(logging.WARNING, logger=MODULE_LOGGER):
        YtdlpOutputLogger(SESSION).warning("forcing SABR streaming")

    assert "forcing SABR streaming" in caplog.text
    assert caplog.records[-1].levelno == logging.WARNING


def test_error_reaches_the_module_log(caplog):
    with caplog.at_level(logging.WARNING, logger=MODULE_LOGGER):
        YtdlpOutputLogger(SESSION).error("ERROR: Requested format is not available")

    assert caplog.records[-1].levelno == logging.ERROR


def test_progress_stays_at_debug_level(caplog):
    """В логе прогресс допустим, но только на уровне debug."""
    with caplog.at_level(logging.DEBUG, logger=MODULE_LOGGER):
        YtdlpOutputLogger(SESSION).debug("[download]  1% of 10.00MiB")

    assert [record.levelno for record in caplog.records] == [logging.DEBUG]


def test_unprintable_message_does_not_break_the_download():
    class Unprintable:
        def __str__(self):
            raise RuntimeError("строка не собралась")

    adapter = YtdlpOutputLogger(SESSION)
    adapter.warning(Unprintable())
    adapter.debug(None)
    adapter.error(b"\xff\xfe")

    # Несобираемая строка просто теряется, остальное приводится к тексту:
    # роняет загрузку только исключение, а его здесь нет.
    assert download_report.output_tail(SESSION) == ["None", "b'\\xff\\xfe'"]


def test_broken_report_does_not_break_the_download(monkeypatch):
    """Диагностика — побочный канал: её поломка не имеет права ронять загрузку."""

    def explode(*args, **kwargs):
        raise RuntimeError("регистр недоступен")

    monkeypatch.setattr(download_report, "record_output", explode)

    YtdlpOutputLogger(SESSION).warning("cookies are no longer valid")


def test_apply_network_opts_installs_the_adapter():
    options: dict = {}

    apply_network_opts(options, session_id=SESSION)

    assert isinstance(options["logger"], YtdlpOutputLogger)
    assert options["logger"].session_id == SESSION


def test_apply_network_opts_does_not_mute_warnings():
    options: dict = {"no_warnings": True}

    apply_network_opts(options)

    assert options["no_warnings"] is False
    assert options["logger"].session_id is None


def test_capture_opts_route_lines_to_their_session():
    YtdlpOutputLogger(SESSION).warning("своё")
    output_capture_opts("другая")["logger"].warning("чужое")

    assert download_report.output_tail(SESSION) == ["своё"]
    assert download_report.output_tail("другая") == ["чужое"]


@pytest.mark.skipif(
    getattr(yt_dlp, "YoutubeDL", None) is None,
    reason="в среде стоит заглушка yt_dlp",
)
def test_ytdlp_itself_routes_warnings_to_the_adapter():
    """Проверка сквозная: важен не флаг в словаре, а поведение самой yt-dlp."""
    options: dict = {"quiet": True}
    apply_network_opts(options, session_id=SESSION)

    with yt_dlp.YoutubeDL(options) as ydl:
        ydl.report_warning("Some formats require a GVS PO Token")
        ydl.to_screen("[download]  25% of 10.00MiB")

    assert download_report.output_tail(SESSION) == [
        "Some formats require a GVS PO Token"
    ]


@pytest.mark.skipif(
    getattr(yt_dlp, "YoutubeDL", None) is None,
    reason="в среде стоит заглушка yt_dlp",
)
def test_ytdlp_progress_printer_accepts_the_adapter():
    """Прогресс yt-dlp печатает через логгер, приняв его за поток вывода."""
    from yt_dlp.minicurses import MultilineLogger

    printer = MultilineLogger(YtdlpOutputLogger(SESSION), 1)
    printer.print_at_line("[download]  25% of 10.00MiB at 1.00MiB/s", 0)

    assert download_report.output_tail(SESSION) == []
