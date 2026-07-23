"""Тесты безопасной классификации пользовательских ошибок."""

from utils.public_errors import (
    build_public_error_message,
    classify_internal_error_category,
)


def test_platform_access_error_does_not_expose_cookies():
    message = build_public_error_message(
        "instagram",
        "IG-ACCESS-ABC123",
        "cookies are expired; login required",
    )

    assert "cookies" not in message.lower()
    assert "IG-ACCESS-ABC123" in message
    assert "авторизац" in message.lower()


def test_network_error_uses_generic_network_message():
    assert classify_internal_error_category("tiktok", "connection timed out") == "NETWORK"
    message = build_public_error_message(
        "tiktok",
        "TT-NETWORK-ABC123",
        "connection timed out",
    )

    assert "TT-NETWORK-ABC123" in message
    assert "соединен" in message.lower()
