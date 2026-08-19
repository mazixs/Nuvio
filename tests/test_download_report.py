"""Побочный канал диагностики: хвост вывода yt-dlp и фактический формат."""

import threading

import pytest

from utils import download_report


@pytest.fixture(autouse=True)
def clean_registry():
    download_report._OUTPUT.clear()
    download_report._DELIVERED.clear()
    yield
    download_report._OUTPUT.clear()
    download_report._DELIVERED.clear()


@pytest.mark.unit
def test_output_is_kept_per_session():
    download_report.record_output("a", "первая")
    download_report.record_output("b", "чужая")
    download_report.record_output("a", "вторая")

    assert download_report.output_tail("a") == ["первая", "вторая"]
    assert download_report.output_tail("b") == ["чужая"]


@pytest.mark.unit
def test_lines_without_session_precede_session_lines():
    """Разбор ссылки идёт до появления сессии, но объясняет выбор клиента."""
    download_report.record_output(None, "visionos player API")
    download_report.record_output("a", "403 на скачивании")

    assert download_report.output_tail("a") == [
        "visionos player API",
        "403 на скачивании",
    ]


@pytest.mark.unit
def test_tail_is_bounded():
    for i in range(download_report.OUTPUT_TAIL * 2):
        download_report.record_output("a", f"строка {i}")

    tail = download_report.output_tail("a")
    assert len(tail) == download_report.OUTPUT_TAIL
    assert tail[-1] == f"строка {download_report.OUTPUT_TAIL * 2 - 1}"


@pytest.mark.unit
def test_blank_lines_are_ignored():
    download_report.record_output("a", "   \n")

    assert download_report.output_tail("a") == []


@pytest.mark.unit
def test_session_count_is_bounded():
    for i in range(download_report.TRACKED_SESSIONS + 10):
        download_report.record_output(f"s{i}", "строка")

    assert len(download_report._OUTPUT) <= download_report.TRACKED_SESSIONS
    assert download_report.output_tail("s0") == []


@pytest.mark.unit
def test_delivered_format_round_trip():
    download_report.record_delivered_format("a", "399+251")

    assert download_report.delivered_format("a") == "399+251"
    assert download_report.delivered_format("b") is None


@pytest.mark.unit
def test_empty_delivered_format_is_not_recorded():
    download_report.record_delivered_format("a", None)
    download_report.record_delivered_format("a", "")

    assert download_report.delivered_format("a") is None


@pytest.mark.unit
def test_forget_clears_both_registries():
    download_report.record_output("a", "строка")
    download_report.record_delivered_format("a", "137+140")

    download_report.forget("a")

    assert download_report.output_tail("a") == []
    assert download_report.delivered_format("a") is None


@pytest.mark.unit
def test_concurrent_writers_do_not_lose_the_registry():
    """Загрузки идут в пуле потоков, поэтому регистр обязан быть потокобезопасным."""

    def worker(index: int) -> None:
        for i in range(50):
            download_report.record_output(f"s{index}", f"{index}:{i}")
            download_report.record_delivered_format(f"s{index}", f"fmt{index}")

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    for index in range(8):
        assert download_report.delivered_format(f"s{index}") == f"fmt{index}"
        assert download_report.output_tail(f"s{index}")
