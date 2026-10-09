import re

from . import db

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _raw(form, key: str) -> str:
    value = form.get(key)
    return value.strip() if isinstance(value, str) else ""


def _tidy(form, key: str) -> str:
    return " ".join(_raw(form, key).split())


def validate_player(form, player_id: int | None = None) -> tuple[dict, list[str]]:
    """Check a player form. Returns (clean fields ready for db.save_player, errors)."""
    first = _tidy(form, "first_name")
    last = _tidy(form, "last_name")
    nickname = _tidy(form, "nickname")
    email = _tidy(form, "email")

    errors: list[str] = []
    if not first:
        errors.append("First name is required.")
    if not last:
        errors.append("Last name is required.")
    if not email:
        errors.append("Email is required.")
    elif not EMAIL_RE.match(email):
        errors.append("Email doesn't look valid.")

    if email and EMAIL_RE.match(email) and db.email_in_use(db.norm(email), player_id):
        errors.append("Another player already uses this email.")

    clean = {
        "first_name": first,
        "last_name": last,
        "nickname": nickname,
        "email": email,
        "phone": _tidy(form, "phone"),
        "notes": _raw(form, "notes"),
        "first_key": db.norm(first),
        "last_key": db.norm(last),
        "nick_key": db.norm(nickname),
        "email_key": db.norm(email),
        "show_email": int(form.get("show_email") == "on"),
        "show_phone": int(form.get("show_phone") == "on"),
    }
    return clean, errors
