"""
WebUI дашборд аналитики бота.
"""
import hmac
import logging
import os
import hashlib
import secrets
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import httpx
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Form, Depends, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

logger = logging.getLogger("nuvio.web")

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from utils.analytics_db import (
    init_db, dashboard_summary, get_all_users, get_user_detail,
)

WEB_DIR = Path(__file__).resolve().parent

# ── Безопасность ──────────────────────────────────────────────

MAX_INPUT_LENGTH = 128  # макс. длина логина/пароля


def _parse_duration(value: str) -> int:
    """Парсит строку вида '15m', '1h', '300s', '300' → секунды."""
    value = value.strip().lower()
    if value.endswith("m"):
        return int(value[:-1]) * 60
    if value.endswith("h"):
        return int(value[:-1]) * 3600
    if value.endswith("s"):
        return int(value[:-1])
    return int(value)


LOGIN_RATE_LIMIT = int(os.environ.get("FAIL2BAN_RETRIES", "5"))
LOGIN_LOCKOUT = _parse_duration(os.environ.get("FAIL2BAN_TIME", "10m"))

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
ADMIN_IDS = [
    uid.strip() for uid in os.environ.get("ADMIN_IDS", "").split(",") if uid.strip()
]

# Хранилище неудачных попыток: {ip: [(timestamp, username), ...]}
_login_attempts: dict[str, list[tuple[float, str]]] = defaultdict(list)
# Множество IP, по которым уже отправлено уведомление (чтобы не спамить)
_notified_ips: set[str] = set()
# Лимит отслеживаемых IP (защита от исчерпания памяти при DDoS)
_MAX_TRACKED_IPS = 10_000


def _cleanup_old_ips() -> None:
    """Удаляет записи с истёкшими попытками, ограничивает общий размер."""
    now = time.time()
    expired = [ip for ip, attempts in _login_attempts.items()
               if all(now - t >= LOGIN_LOCKOUT for t, _ in attempts)]
    for ip in expired:
        del _login_attempts[ip]
        _notified_ips.discard(ip)
    # Если всё ещё слишком много — удаляем самые старые
    if len(_login_attempts) > _MAX_TRACKED_IPS:
        by_oldest = sorted(_login_attempts, key=lambda ip: _login_attempts[ip][0][0])
        for ip in by_oldest[:len(_login_attempts) - _MAX_TRACKED_IPS]:
            del _login_attempts[ip]
            _notified_ips.discard(ip)


def _check_rate_limit(ip: str) -> bool:
    """Возвращает True если IP заблокирован из-за превышения лимита."""
    now = time.time()
    _login_attempts[ip] = [
        (t, u) for t, u in _login_attempts[ip] if now - t < LOGIN_LOCKOUT
    ]
    if not _login_attempts[ip]:
        del _login_attempts[ip]
        _notified_ips.discard(ip)
        return False
    # Периодическая чистка
    if len(_login_attempts) > _MAX_TRACKED_IPS:
        _cleanup_old_ips()
    return len(_login_attempts[ip]) >= LOGIN_RATE_LIMIT


def _record_failed_attempt(ip: str, username: str) -> None:
    _login_attempts[ip].append((time.time(), username))


def _clear_attempts(ip: str) -> None:
    _login_attempts.pop(ip, None)
    _notified_ips.discard(ip)


async def _notify_admins_brute_force(ip: str) -> None:
    """Отправляет уведомление админам в Telegram при срабатывании fail2ban."""
    if ip in _notified_ips:
        return
    if not TELEGRAM_TOKEN or not ADMIN_IDS:
        logger.warning("Fail2ban сработал для %s, но TELEGRAM_TOKEN/ADMIN_IDS не заданы", ip)
        return

    _notified_ips.add(ip)

    attempts = _login_attempts.get(ip, [])
    logins_used = list(dict.fromkeys(u for _, u in attempts))  # уникальные, сохраняя порядок
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    lockout_min = LOGIN_LOCKOUT // 60

    text = (
        f"🚨 <b>Fail2ban: IP заблокирован</b>\n\n"
        f"<b>IP:</b> <code>{ip}</code>\n"
        f"<b>Время:</b> {now}\n"
        f"<b>Попыток:</b> {len(attempts)}\n"
        f"<b>Логины:</b> <code>{'</code>, <code>'.join(logins_used)}</code>\n"
        f"<b>Блокировка:</b> {lockout_min} мин.\n\n"
        f"⚠️ Возможен brute-force на WebUI дашборд."
    )

    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    async with httpx.AsyncClient(timeout=10) as client:
        for admin_id in ADMIN_IDS:
            try:
                await client.post(url, json={
                    "chat_id": admin_id,
                    "text": text,
                    "parse_mode": "HTML",
                })
            except Exception:
                logger.error("Не удалось отправить fail2ban уведомление админу %s", admin_id)


def _sanitize_input(value: str) -> str:
    """Обрезает и ограничивает длину ввода."""
    return value.strip()[:MAX_INPUT_LENGTH]


# ── Приложение ────────────────────────────────────────────────


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


app = FastAPI(title="Nuvio Analytics", docs_url=None, redoc_url=None, lifespan=lifespan)
app.mount("/static", StaticFiles(directory=str(WEB_DIR / "static")), name="static")
app.add_middleware(
    SessionMiddleware,
    secret_key=os.environ.get("WEB_SECRET_KEY", secrets.token_hex(32)),
)
templates = Jinja2Templates(directory=str(WEB_DIR / "templates"))

WEB_USERNAME = os.environ.get("WEB_USERNAME", "admin")
WEB_PASSWORD_HASH = hashlib.sha256(
    os.environ.get("WEB_PASSWORD", "changeme").encode()
).hexdigest()


def _check_auth(request: Request) -> bool:
    return request.session.get("authenticated") is True


def require_auth(request: Request):
    if not _check_auth(request):
        raise HTTPException(status_code=303, headers={"Location": "/login"})
    return True


@app.exception_handler(HTTPException)
async def redirect_exception_handler(request: Request, exc: HTTPException):
    if exc.status_code == 303 and "Location" in (exc.headers or {}):
        return RedirectResponse(exc.headers["Location"], status_code=303)
    return HTMLResponse(content=str(exc.detail), status_code=exc.status_code)


# ── Auth ────────────────────────────────────────────────────────


@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    if _check_auth(request):
        return RedirectResponse("/", status_code=303)
    return templates.TemplateResponse("login.html", {"request": request, "error": None})


@app.post("/login", response_class=HTMLResponse)
async def login_submit(request: Request, username: str = Form(...), password: str = Form(...)):
    client_ip = request.client.host if request.client else "unknown"

    # Санитизация ввода
    username = _sanitize_input(username)
    password = _sanitize_input(password)

    # Rate limiting
    if _check_rate_limit(client_ip):
        await _notify_admins_brute_force(client_ip)
        lockout_min = LOGIN_LOCKOUT // 60
        return templates.TemplateResponse("login.html", {
            "request": request,
            "error": f"Слишком много попыток. Попробуйте через {lockout_min} мин.",
        })

    if not username or not password:
        return templates.TemplateResponse("login.html", {
            "request": request, "error": "Заполните все поля",
        })

    # Timing-safe сравнение (защита от timing attack)
    password_hash = hashlib.sha256(password.encode()).hexdigest()
    username_ok = hmac.compare_digest(username, WEB_USERNAME)
    password_ok = hmac.compare_digest(password_hash, WEB_PASSWORD_HASH)

    if username_ok and password_ok:
        _clear_attempts(client_ip)
        request.session["authenticated"] = True
        return RedirectResponse("/", status_code=303)

    _record_failed_attempt(client_ip, username)

    # Проверяем, не превышен ли лимит после этой попытки
    if _check_rate_limit(client_ip):
        await _notify_admins_brute_force(client_ip)

    return templates.TemplateResponse("login.html", {
        "request": request, "error": "Неверный логин или пароль",
    })


@app.get("/logout")
async def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/login", status_code=303)


# ── Dashboard ───────────────────────────────────────────────────


@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request, _=Depends(require_auth)):
    data = dashboard_summary()
    return templates.TemplateResponse("dashboard.html", {"request": request, **data})


@app.get("/users", response_class=HTMLResponse)
async def users_list(request: Request, page: int = 1, _=Depends(require_auth)):
    page = max(1, page)
    per_page = 50
    offset = (page - 1) * per_page
    users = get_all_users(limit=per_page, offset=offset)
    return templates.TemplateResponse("users.html", {
        "request": request,
        "users": users,
        "page": page,
        "has_next": len(users) == per_page,
    })


@app.get("/users/{user_id}", response_class=HTMLResponse)
async def user_detail(request: Request, user_id: int, _=Depends(require_auth)):
    user = get_user_detail(user_id)
    if not user:
        return HTMLResponse("Пользователь не найден", status_code=404)
    return templates.TemplateResponse("user_detail.html", {"request": request, "user": user})


# ── API (JSON) ──────────────────────────────────────────────────


@app.get("/api/summary")
async def api_summary(request: Request, _=Depends(require_auth)):
    return dashboard_summary()


def run():
    import uvicorn
    port = int(os.environ.get("WEB_PORT", "8080"))
    uvicorn.run(app, host="0.0.0.0", port=port)


if __name__ == "__main__":
    run()
