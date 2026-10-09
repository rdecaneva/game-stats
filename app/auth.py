import hashlib
import hmac
import os
import secrets
from pathlib import Path

from . import db

COOKIE = "game_stats_session"
MIN_PASSWORD = 8
_SCRYPT = {"n": 2**14, "r": 8, "p": 1}


def _secret() -> bytes:
    """Signing key: SECRET_KEY if set, otherwise a random key kept next to the database."""
    env = os.environ.get("SECRET_KEY")
    if env:
        return env.encode()
    path = Path(db.db_path()).parent / "secret.key"
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(secrets.token_hex(32))
    return path.read_text().strip().encode()


def _sign(player_id: int) -> str:
    return hmac.new(_secret(), str(player_id).encode(), hashlib.sha256).hexdigest()


def session_value(player_id: int) -> str:
    return f"{player_id}.{_sign(player_id)}"


def read_session(value: str | None) -> int | None:
    if not value or "." not in value:
        return None
    raw, sig = value.split(".", 1)
    if not raw.isdigit() or not hmac.compare_digest(sig, _sign(int(raw))):
        return None
    return int(raw)


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, **_SCRYPT)
    return f"{salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    salt_hex, digest_hex = stored.split("$", 1)
    digest = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt_hex), **_SCRYPT)
    return hmac.compare_digest(digest.hex(), digest_hex)
