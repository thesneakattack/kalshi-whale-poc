"""
Encrypted local store for connected-account credentials — the alternative to
hand-editing .env once you're signed in. This is what "Connect" on the
Accounts page actually does: saves a small JSON blob (URL, API key, field
names — whatever that provider's __init__ asks for), encrypted at rest.

Inert (raises on save) until APP_SECRET_KEY is set, since that value is also
what derives the encryption key — see services/auth.py.fernet_key(). Reading
returns None rather than raising, so callers can fall back to .env values
without a config check first.

SQLite file lives at data/accounts.db — already covered by .gitignore's
`data/*.db`, never commit it.
"""
import json
import sqlite3
import time
from pathlib import Path

from cryptography.fernet import Fernet

from services import auth

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "accounts.db"


def _connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS connected_accounts (
            provider TEXT PRIMARY KEY,
            credentials_encrypted BLOB NOT NULL,
            updated_at REAL NOT NULL
        )
        """
    )
    return conn


def enabled() -> bool:
    return bool(auth.session_secret_key())


def save(provider: str, credentials: dict):
    if not enabled():
        raise RuntimeError("Set APP_SECRET_KEY in .env before connecting accounts")
    blob = Fernet(auth.fernet_key()).encrypt(json.dumps(credentials).encode())
    with _connect() as conn:
        conn.execute(
            "INSERT INTO connected_accounts (provider, credentials_encrypted, updated_at) VALUES (?, ?, ?) "
            "ON CONFLICT(provider) DO UPDATE SET credentials_encrypted = excluded.credentials_encrypted, "
            "updated_at = excluded.updated_at",
            (provider, blob, time.time()),
        )


def load(provider: str) -> dict | None:
    if not enabled():
        return None
    with _connect() as conn:
        row = conn.execute(
            "SELECT credentials_encrypted FROM connected_accounts WHERE provider = ?", (provider,)
        ).fetchone()
    if not row:
        return None
    try:
        return json.loads(Fernet(auth.fernet_key()).decrypt(row[0]).decode())
    except Exception:
        return None  # APP_SECRET_KEY changed since this was saved, most likely


def delete(provider: str):
    with _connect() as conn:
        conn.execute("DELETE FROM connected_accounts WHERE provider = ?", (provider,))


def status() -> dict:
    """{provider: updated_at} for every connected provider — no secrets in this."""
    with _connect() as conn:
        rows = conn.execute("SELECT provider, updated_at FROM connected_accounts").fetchall()
    return {provider: updated_at for provider, updated_at in rows}
