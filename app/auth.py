"""Session cookie auth and role checks."""
from __future__ import annotations

import secrets
from datetime import timedelta
from typing import Optional

import bcrypt
from fastapi import HTTPException, Request, Response

from app.config import SESSION_COOKIE, SESSION_DAYS
from app.db import create_session, delete_session, get_session_user, utcnow


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))
    except ValueError:
        return False


def start_session(response: Response, user_id: int) -> str:
    token = secrets.token_urlsafe(32)
    expires = utcnow() + timedelta(days=SESSION_DAYS)
    create_session(token, user_id, expires)
    response.set_cookie(
        SESSION_COOKIE,
        token,
        httponly=True,
        samesite="lax",
        max_age=SESSION_DAYS * 86400,
        path="/",
    )
    return token


def clear_session(response: Response, token: Optional[str]):
    if token:
        delete_session(token)
    response.delete_cookie(SESSION_COOKIE, path="/")


def current_user(request: Request):
    token = request.cookies.get(SESSION_COOKIE)
    user = get_session_user(token or "")
    if not user or not user.get("is_active"):
        return None
    return user


def require_user(request: Request) -> dict:
    user = current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Sign in required.")
    return user


def require_staff(request: Request) -> dict:
    user = require_user(request)
    if user["role"] not in ("supervisor", "educator"):
        raise HTTPException(status_code=403, detail="Educator or supervisor access required.")
    return user


def require_supervisor(request: Request) -> dict:
    user = require_user(request)
    if user["role"] != "supervisor":
        raise HTTPException(status_code=403, detail="Supervisor access required.")
    return user


def require_student(request: Request) -> dict:
    user = require_user(request)
    if user["role"] != "student":
        raise HTTPException(status_code=403, detail="Student access required.")
    return user
