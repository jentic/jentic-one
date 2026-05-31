"""Database connection helper and Alembic migration runner."""

import os
import secrets
from contextlib import asynccontextmanager
from typing import AsyncIterator

import aiosqlite
from alembic import command
from alembic.config import Config

from src.config import DB_PATH, DEFAULT_TOOLKIT_ID  # noqa: F401 — re-exported for consumers


def run_migrations() -> None:
    """Run Alembic migrations (upgrade head) synchronously.

    Intended for use at application startup (e.g. from FastAPI lifespan hooks).
    Safe to call multiple times — Alembic skips already-applied migrations.
    """
    # Locate alembic.ini relative to the project root
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    alembic_cfg = Config(os.path.join(project_root, "alembic.ini"))
    # Ensure script_location is absolute so it works regardless of cwd
    alembic_cfg.set_main_option("script_location", os.path.join(project_root, "alembic"))
    command.upgrade(alembic_cfg, "head")


@asynccontextmanager
async def get_db() -> AsyncIterator[aiosqlite.Connection]:
    """Return an async context-manager for a DB connection with foreign keys enabled."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("PRAGMA foreign_keys = ON")
        yield db


async def get_setting(key: str) -> str | None:
    """Read a single settings value."""
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT value FROM settings WHERE key=?", (key,)) as cur:
            row = await cur.fetchone()
    return row[0] if row else None


async def set_setting(key: str, value: str) -> None:
    """Write a single settings value."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (key, value))
        await db.commit()


async def setup_state() -> dict:
    """Return current setup flags as a dict.

    Keys:
      default_key_claimed — bool
      account_created     — bool
      jwt_secret          — str (generated on first call)
    """
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT key, value FROM settings") as cur:
            rows = await cur.fetchall()
    s = {r[0]: r[1] for r in rows}

    # Generate jwt_secret on first call
    if "jwt_secret" not in s:
        secret = secrets.token_hex(32)
        await set_setting("jwt_secret", secret)
        s["jwt_secret"] = secret

    return {
        "default_key_claimed": s.get("default_key_claimed") == "1",
        "account_created": s.get("account_created") == "1",
        "jwt_secret": s["jwt_secret"],
    }
