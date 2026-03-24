#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Тесты классификации ошибок polling-цикла в main.py."""

import logging

import telegram

import main


def test_polling_error_callback_logs_network_error_as_warning(caplog):
    error = telegram.error.NetworkError("httpx.RemoteProtocolError: peer disconnected")

    with caplog.at_level(logging.WARNING):
        main._polling_error_callback(error)

    assert "REMOTE_DISCONNECT" in caplog.text
    assert "перехватил polling" in caplog.text


def test_polling_error_callback_logs_other_errors_as_warning(caplog):
    error = telegram.error.TelegramError("generic telegram error")

    with caplog.at_level(logging.WARNING):
        main._polling_error_callback(error)

    assert "UNKNOWN" in caplog.text
    assert "Неожиданная ошибка при long polling Bot API" in caplog.text


def test_classify_polling_error_detects_conflict():
    category, summary = main._classify_polling_error(
        telegram.error.Conflict("terminated by other getUpdates request")
    )

    assert category == "POLLING_CONFLICT"
    assert "Параллельный polling" in summary
