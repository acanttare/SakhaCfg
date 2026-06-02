from __future__ import annotations

import os
import secrets
import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel, Field
from starlette.responses import HTMLResponse, PlainTextResponse

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = Path(os.environ.get("PANEL_DB_PATH", ROOT / "output" / "subscriptions.db"))
WORKING_TXT_PATH = Path(os.environ.get("WORKING_TXT_PATH", ROOT / "output" / "working.txt"))
ADMIN_TOKEN = os.environ.get("ADMIN_TOKEN", "change-me")
SUBSCRIPTION_TITLE = os.environ.get("SUBSCRIPTION_TITLE", "SakhaCfg Subscription")
ADMIN_HTML_PATH = Path(__file__).resolve().parent / "admin.html"

app = FastAPI(title="SakhaCfg Subscription Panel", version="0.1.0")


def _db() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    with _db() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL UNIQUE,
                token TEXT NOT NULL UNIQUE,
                is_active INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS subscriptions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL UNIQUE,
                expires_at TEXT NOT NULL,
                limit_gb INTEGER NOT NULL,
                used_gb REAL NOT NULL DEFAULT 0,
                updated_at TEXT NOT NULL,
                FOREIGN KEY(user_id) REFERENCES users(id)
            )
            """
        )


@app.on_event("startup")
def _startup() -> None:
    init_db()


def _admin_guard(x_admin_token: str = Header(default="")) -> None:
    if not ADMIN_TOKEN or x_admin_token != ADMIN_TOKEN:
        raise HTTPException(status_code=401, detail="Unauthorized")


class CreateUserRequest(BaseModel):
    username: str = Field(min_length=2, max_length=64)
    expires_at: date
    limit_gb: int = Field(ge=1, le=100000)


class UpdateSubscriptionRequest(BaseModel):
    expires_at: date | None = None
    limit_gb: int | None = Field(default=None, ge=1, le=100000)
    is_active: bool | None = None


@dataclass
class UserSubscription:
    id: int
    username: str
    token: str
    is_active: bool
    expires_at: str
    limit_gb: int
    used_gb: float


def _fetch_users() -> list[UserSubscription]:
    with _db() as conn:
        rows = conn.execute(
            """
            SELECT u.id, u.username, u.token, u.is_active, s.expires_at, s.limit_gb, s.used_gb
            FROM users u
            JOIN subscriptions s ON s.user_id = u.id
            ORDER BY u.id DESC
            """
        ).fetchall()
    return [
        UserSubscription(
            id=row["id"],
            username=row["username"],
            token=row["token"],
            is_active=bool(row["is_active"]),
            expires_at=row["expires_at"],
            limit_gb=int(row["limit_gb"]),
            used_gb=float(row["used_gb"]),
        )
        for row in rows
    ]


def _fetch_by_token(token: str) -> UserSubscription | None:
    with _db() as conn:
        row = conn.execute(
            """
            SELECT u.id, u.username, u.token, u.is_active, s.expires_at, s.limit_gb, s.used_gb
            FROM users u
            JOIN subscriptions s ON s.user_id = u.id
            WHERE u.token = ?
            """,
            (token,),
        ).fetchone()
    if not row:
        return None
    return UserSubscription(
        id=row["id"],
        username=row["username"],
        token=row["token"],
        is_active=bool(row["is_active"]),
        expires_at=row["expires_at"],
        limit_gb=int(row["limit_gb"]),
        used_gb=float(row["used_gb"]),
    )


def _load_working_configs(limit: int = 50) -> list[str]:
    if not WORKING_TXT_PATH.exists():
        return []
    lines = WORKING_TXT_PATH.read_text(encoding="utf-8").splitlines()
    configs = [line.strip() for line in lines if line.strip() and not line.startswith("#")]
    return configs[:limit]


def _now_utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@app.get("/health")
def health() -> dict[str, Any]:
    return {"ok": True, "time": _now_utc_iso()}


@app.get("/admin", response_class=HTMLResponse)
def admin_page() -> str:
    if not ADMIN_HTML_PATH.exists():
        return "<h1>Admin UI missing</h1>"
    return ADMIN_HTML_PATH.read_text(encoding="utf-8")


@app.get("/admin/users")
def list_users(_: None = Depends(_admin_guard)) -> list[dict[str, Any]]:
    return [u.__dict__ for u in _fetch_users()]


@app.post("/admin/users")
def create_user(body: CreateUserRequest, _: None = Depends(_admin_guard)) -> dict[str, Any]:
    token = secrets.token_urlsafe(24)
    now = _now_utc_iso()
    with _db() as conn:
        try:
            cur = conn.execute(
                "INSERT INTO users(username, token, is_active, created_at) VALUES(?,?,1,?)",
                (body.username.strip(), token, now),
            )
            user_id = int(cur.lastrowid)
            conn.execute(
                "INSERT INTO subscriptions(user_id, expires_at, limit_gb, used_gb, updated_at) VALUES(?,?,?,?,?)",
                (user_id, body.expires_at.isoformat(), body.limit_gb, 0, now),
            )
        except sqlite3.IntegrityError as exc:
            raise HTTPException(status_code=409, detail=f"User create failed: {exc}") from exc
    return {"ok": True, "username": body.username, "token": token}


@app.patch("/admin/users/{user_id}")
def update_user(user_id: int, body: UpdateSubscriptionRequest, _: None = Depends(_admin_guard)) -> dict[str, Any]:
    with _db() as conn:
        user = conn.execute("SELECT id FROM users WHERE id = ?", (user_id,)).fetchone()
        if not user:
            raise HTTPException(status_code=404, detail="User not found")

        if body.is_active is not None:
            conn.execute("UPDATE users SET is_active = ? WHERE id = ?", (1 if body.is_active else 0, user_id))

        updates: list[str] = []
        values: list[Any] = []
        if body.expires_at is not None:
            updates.append("expires_at = ?")
            values.append(body.expires_at.isoformat())
        if body.limit_gb is not None:
            updates.append("limit_gb = ?")
            values.append(body.limit_gb)
        if updates:
            updates.append("updated_at = ?")
            values.append(_now_utc_iso())
            values.append(user_id)
            conn.execute(f"UPDATE subscriptions SET {', '.join(updates)} WHERE user_id = ?", values)

    return {"ok": True}


@app.get("/sub/{token}", response_class=PlainTextResponse, response_model=None)
def get_subscription(token: str) -> str:
    user = _fetch_by_token(token)
    if not user:
        raise HTTPException(status_code=404, detail="Subscription not found")

    if not user.is_active:
        raise HTTPException(status_code=403, detail="Subscription is disabled")

    today = date.today()
    expires = date.fromisoformat(user.expires_at)
    if today > expires:
        raise HTTPException(status_code=403, detail="Subscription has expired")

    if user.used_gb >= float(user.limit_gb):
        raise HTTPException(status_code=403, detail="Traffic limit exhausted")

    configs = _load_working_configs(limit=50)
    header = [
        f"# title: {SUBSCRIPTION_TITLE} - {user.username}",
        f"# expires_at: {user.expires_at}",
        f"# limit_gb: {user.limit_gb}",
        f"# used_gb: {user.used_gb:.2f}",
        "",
    ]
    return "\n".join(header + configs) + "\n"
