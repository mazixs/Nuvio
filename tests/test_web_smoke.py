"""Smoke-тесты WebUI без запуска внешнего сервера."""

import hashlib

import pytest
from fastapi.testclient import TestClient as FastAPITestClient

from web import app as web_app


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(web_app, "init_db", lambda: None)
    web_app._login_attempts.clear()
    web_app._notified_ips.clear()
    with FastAPITestClient(web_app.app) as test_client:
        yield test_client


def test_health_endpoint_is_public(client):
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_dashboard_redirects_unauthenticated_user(client):
    response = client.get("/", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/login"


def test_login_page_is_available(client):
    response = client.get("/login")

    assert response.status_code == 200
    assert "Nuvio" in response.text


def test_valid_login_opens_authenticated_summary(client, monkeypatch):
    password = "safe-test-password"
    monkeypatch.setattr(web_app, "WEB_USERNAME", "operator")
    monkeypatch.setattr(
        web_app,
        "WEB_PASSWORD_HASH",
        hashlib.pbkdf2_hmac(
            "sha256",
            password.encode(),
            web_app.SALT,
            100000,
        ),
    )
    monkeypatch.setattr(
        web_app,
        "dashboard_summary",
        lambda: {"total_users": 3, "active_today": 2},
    )

    login_response = client.post(
        "/login",
        data={"username": "operator", "password": password},
        follow_redirects=False,
    )
    summary_response = client.get("/api/summary")

    assert login_response.status_code == 303
    assert login_response.headers["location"] == "/"
    assert summary_response.status_code == 200
    assert summary_response.json() == {"total_users": 3, "active_today": 2}


def test_invalid_login_does_not_authenticate(client):
    response = client.post(
        "/login",
        data={"username": "wrong", "password": "wrong"},
    )
    dashboard = client.get("/", follow_redirects=False)

    assert response.status_code == 200
    assert "Неверный логин или пароль" in response.text
    assert dashboard.status_code == 303
