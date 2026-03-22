"""
WebUI дашборд аналитики бота.
"""
import os
import hashlib
import secrets
from pathlib import Path

from fastapi import FastAPI, Request, Form, Depends, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from utils.analytics_db import (
    init_db, dashboard_summary, get_all_users, get_user_detail,
)

WEB_DIR = Path(__file__).resolve().parent

app = FastAPI(title="Nuvio Analytics")
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


@app.on_event("startup")
async def startup():
    init_db()


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
    password_hash = hashlib.sha256(password.encode()).hexdigest()
    if username == WEB_USERNAME and password_hash == WEB_PASSWORD_HASH:
        request.session["authenticated"] = True
        return RedirectResponse("/", status_code=303)
    return templates.TemplateResponse("login.html", {"request": request, "error": "Неверный логин или пароль"})


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
