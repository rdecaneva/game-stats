import json
import os
import sqlite3
import unicodedata
from contextlib import contextmanager

SCHEMA = """
CREATE TABLE IF NOT EXISTS players (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    first_name    TEXT NOT NULL,
    last_name     TEXT NOT NULL,
    nickname      TEXT NOT NULL DEFAULT '',
    email         TEXT NOT NULL,
    phone         TEXT NOT NULL DEFAULT '',
    notes         TEXT NOT NULL DEFAULT '',
    first_key     TEXT NOT NULL,
    last_key      TEXT NOT NULL,
    nick_key      TEXT NOT NULL DEFAULT '',
    email_key     TEXT NOT NULL UNIQUE,
    show_email    INTEGER NOT NULL DEFAULT 0,
    show_phone    INTEGER NOT NULL DEFAULT 0,
    is_admin      INTEGER NOT NULL DEFAULT 0,
    password_hash TEXT,
    confirm_token TEXT,
    created_at    TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_players_name ON players(first_key, last_key);

CREATE TABLE IF NOT EXISTS locations (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL,
    name_key    TEXT NOT NULL UNIQUE,
    type        TEXT NOT NULL DEFAULT '',
    street      TEXT NOT NULL DEFAULT '',
    city        TEXT NOT NULL DEFAULT '',
    region      TEXT NOT NULL DEFAULT '',
    postal_code TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS matches (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    game             TEXT NOT NULL,
    played_on        TEXT NOT NULL,
    played_at        TEXT,
    details          TEXT NOT NULL DEFAULT '{}',
    location_id      INTEGER REFERENCES locations(id),
    duration_seconds INTEGER,
    created_at       TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS participants (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    match_id  INTEGER NOT NULL REFERENCES matches(id) ON DELETE CASCADE,
    player_id INTEGER REFERENCES players(id),   -- NULL for an anonymous guest
    label     TEXT NOT NULL DEFAULT '',         -- the guest's name, when player_id is NULL
    is_player INTEGER NOT NULL DEFAULT 1,       -- 0 for automas, bots, explainers, etc.
    won       INTEGER NOT NULL DEFAULT 0,
    score     INTEGER,
    rank      INTEGER,
    details   TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS games (
    key        TEXT PRIMARY KEY,
    name       TEXT NOT NULL,
    bgg_id     INTEGER,
    year       TEXT,
    designers  TEXT NOT NULL DEFAULT '[]',
    thumbnail  TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_matches_game ON matches(game, played_on);
CREATE INDEX IF NOT EXISTS idx_participants_match ON participants(match_id);
CREATE INDEX IF NOT EXISTS idx_participants_player ON participants(player_id);
"""

PARTICIPANTS_TABLE = SCHEMA[SCHEMA.index("CREATE TABLE IF NOT EXISTS participants"):SCHEMA.index("CREATE INDEX IF NOT EXISTS idx_matches_game")]


def db_path() -> str:
    return os.environ.get("DB_PATH", "data/stats.db")


@contextmanager
def connect():
    path = db_path()
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _columns(conn, table: str) -> set[str]:
    return {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}


def init_db() -> None:
    with connect() as conn:
        conn.executescript(SCHEMA)
        # Databases created before admins existed need the column added.
        if "is_admin" not in _columns(conn, "players"):
            conn.execute("ALTER TABLE players ADD COLUMN is_admin INTEGER NOT NULL DEFAULT 0")
        # Databases created before location details existed need the columns added.
        loc_cols = _columns(conn, "locations")
        for col in ("type", "street", "city", "region", "postal_code"):
            if col not in loc_cols:
                conn.execute(f"ALTER TABLE locations ADD COLUMN {col} TEXT NOT NULL DEFAULT ''")
        match_cols = _columns(conn, "matches")
        if "location_id" not in match_cols:
            conn.execute("ALTER TABLE matches ADD COLUMN location_id INTEGER REFERENCES locations(id)")
        if "duration_seconds" not in match_cols:
            conn.execute("ALTER TABLE matches ADD COLUMN duration_seconds INTEGER")
        if "played_at" not in match_cols:
            conn.execute("ALTER TABLE matches ADD COLUMN played_at TEXT")
        # Older participants required a player and had no guest, non-player, score or rank.
        # SQLite can't drop NOT NULL in place, so rebuild the table and keep the rows.
        if "label" not in _columns(conn, "participants"):
            conn.execute("DROP INDEX IF EXISTS idx_participants_match")
            conn.execute("DROP INDEX IF EXISTS idx_participants_player")
            conn.execute("ALTER TABLE participants RENAME TO participants_old")
            conn.executescript(PARTICIPANTS_TABLE)
            conn.execute(
                """INSERT INTO participants (id, match_id, player_id, won, details)
                   SELECT id, match_id, player_id, won, details FROM participants_old"""
            )
            conn.execute("DROP TABLE participants_old")
            conn.executescript(SCHEMA)


# ---- names and display ---------------------------------------------------

def norm(text: str) -> str:
    """Comparison key for names and emails: NFC, single spaces, case-folded."""
    return " ".join(unicodedata.normalize("NFC", text).split()).casefold()


def display_name(first: str, last: str, nickname: str) -> str:
    name = f"{first} {last}"
    return f"{name} ({nickname})" if nickname else name


def _player(row) -> dict:
    return {
        "id": row["id"],
        "first_name": row["first_name"],
        "last_name": row["last_name"],
        "nickname": row["nickname"],
        "email": row["email"],
        "phone": row["phone"],
        "notes": row["notes"],
        "show_email": bool(row["show_email"]),
        "show_phone": bool(row["show_phone"]),
        "is_admin": bool(row["is_admin"]),
        "display": display_name(row["first_name"], row["last_name"], row["nickname"]),
        "has_login": row["password_hash"] is not None,
    }


# ---- players -------------------------------------------------------------

def list_players() -> list[dict]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM players ORDER BY first_key, last_key, nick_key"
        ).fetchall()
    return [_player(r) for r in rows]


def get_player(player_id: int) -> dict | None:
    with connect() as conn:
        row = conn.execute("SELECT * FROM players WHERE id = ?", (player_id,)).fetchone()
    return _player(row) if row else None


def email_in_use(email_key: str, exclude_id: int | None) -> bool:
    with connect() as conn:
        row = conn.execute(
            "SELECT 1 FROM players WHERE email_key = ? AND id != ?",
            (email_key, exclude_id or 0),
        ).fetchone()
    return row is not None


def save_player(fields: dict, player_id: int | None = None) -> int:
    columns = list(fields)
    values = [fields[c] for c in columns]
    with connect() as conn:
        if player_id is None:
            placeholders = ", ".join("?" for _ in columns)
            cur = conn.execute(
                f"INSERT INTO players ({', '.join(columns)}) VALUES ({placeholders})", values
            )
            return cur.lastrowid
        assignments = ", ".join(f"{c} = ?" for c in columns)
        conn.execute(f"UPDATE players SET {assignments} WHERE id = ?", [*values, player_id])
    return player_id


def find_player_ids_by_label(label: str) -> list[int]:
    """Player ids whose display name matches a label. More than one means the label is ambiguous."""
    wanted = norm(label)
    return [p["id"] for p in list_players() if norm(p["display"]) == wanted]


def get_login(email_key: str) -> dict | None:
    with connect() as conn:
        row = conn.execute(
            "SELECT id, password_hash FROM players WHERE email_key = ?", (email_key,)
        ).fetchone()
    return dict(row) if row else None


def set_login_token(player_id: int, token: str) -> None:
    with connect() as conn:
        conn.execute("UPDATE players SET confirm_token = ? WHERE id = ?", (token, player_id))


def player_id_for_token(token: str) -> int | None:
    with connect() as conn:
        row = conn.execute(
            "SELECT id FROM players WHERE confirm_token = ?", (token,)
        ).fetchone()
    return row["id"] if row else None


def set_admin(player_id: int, is_admin: bool) -> None:
    with connect() as conn:
        conn.execute("UPDATE players SET is_admin = ? WHERE id = ?", (int(is_admin), player_id))


def admin_count() -> int:
    with connect() as conn:
        row = conn.execute("SELECT COUNT(*) AS n FROM players WHERE is_admin = 1").fetchone()
    return row["n"]


def set_password(player_id: int, password_hash: str) -> None:
    with connect() as conn:
        conn.execute(
            "UPDATE players SET password_hash = ?, confirm_token = NULL WHERE id = ?",
            (password_hash, player_id),
        )


def delete_player(player_id: int) -> None:
    """Remove the player record. Past matches keep their history: the player's name is
    frozen onto each participant row as a label, same as a guest with no profile."""
    with connect() as conn:
        row = conn.execute(
            "SELECT first_name, last_name, nickname FROM players WHERE id = ?", (player_id,)
        ).fetchone()
        if row is None:
            return
        label = display_name(row["first_name"], row["last_name"], row["nickname"])
        conn.execute(
            "UPDATE participants SET player_id = NULL, label = ? WHERE player_id = ?",
            (label, player_id),
        )
        conn.execute("DELETE FROM players WHERE id = ?", (player_id,))


# ---- locations -----------------------------------------------------------

def _location(row) -> dict:
    return {
        "id": row["id"],
        "name": row["name"],
        "type": row["type"],
        "street": row["street"],
        "city": row["city"],
        "region": row["region"],
        "postal_code": row["postal_code"],
    }


def list_locations() -> list[dict]:
    with connect() as conn:
        rows = conn.execute("SELECT * FROM locations ORDER BY name_key").fetchall()
    return [_location(r) for r in rows]


def get_location(location_id: int) -> dict | None:
    with connect() as conn:
        row = conn.execute("SELECT * FROM locations WHERE id = ?", (location_id,)).fetchone()
    return _location(row) if row else None


def location_id_for(name: str) -> int | None:
    """Find or create the location with this name. Blank names mean no location."""
    name = " ".join(name.split())
    if not name:
        return None
    with connect() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO locations (name, name_key) VALUES (?, ?)", (name, norm(name))
        )
        row = conn.execute("SELECT id FROM locations WHERE name_key = ?", (norm(name),)).fetchone()
    return row["id"]


def location_name(location_id: int) -> str | None:
    with connect() as conn:
        row = conn.execute("SELECT name FROM locations WHERE id = ?", (location_id,)).fetchone()
    return row["name"] if row else None


def save_location(fields: dict, location_id: int | None = None) -> tuple[int | None, str | None]:
    """Create or update a location. Returns (id, error); id is None on error."""
    name = " ".join((fields.get("name") or "").split())
    if not name:
        return None, "Name is required."
    with connect() as conn:
        clash = conn.execute(
            "SELECT id FROM locations WHERE name_key = ? AND id != ?",
            (norm(name), location_id or 0),
        ).fetchone()
        if clash:
            return None, f"Another location is already called “{name}”."
        values = (
            name,
            norm(name),
            fields.get("type") or "",
            fields.get("street") or "",
            fields.get("city") or "",
            fields.get("region") or "",
            fields.get("postal_code") or "",
        )
        if location_id is None:
            cur = conn.execute(
                """INSERT INTO locations (name, name_key, type, street, city, region, postal_code)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                values,
            )
            location_id = cur.lastrowid
        else:
            conn.execute(
                """UPDATE locations SET name = ?, name_key = ?, type = ?, street = ?, city = ?,
                   region = ?, postal_code = ? WHERE id = ?""",
                values + (location_id,),
            )
    return location_id, None


def delete_location(location_id: int) -> None:
    """Remove the location. Matches that used it keep their history, just without a location."""
    with connect() as conn:
        conn.execute("UPDATE matches SET location_id = NULL WHERE location_id = ?", (location_id,))
        conn.execute("DELETE FROM locations WHERE id = ?", (location_id,))


# ---- matches -------------------------------------------------------------

def _insert_participants(conn, match_id: int, participants: list[dict]) -> None:
    conn.executemany(
        """INSERT INTO participants
           (match_id, player_id, label, is_player, won, score, rank, details)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        [
            (
                match_id,
                p["player_id"],
                p.get("label", ""),
                int(p.get("is_player", True)),
                int(p["won"]),
                p.get("score"),
                p.get("rank"),
                json.dumps(p["details"]),
            )
            for p in participants
        ],
    )


def insert_match(
    game: str,
    played_on: str,
    details: dict,
    participants: list[dict],
    played_at: str | None = None,
    location_id: int | None = None,
    duration_seconds: int | None = None,
) -> int:
    with connect() as conn:
        cur = conn.execute(
            """INSERT INTO matches (game, played_on, played_at, details, location_id, duration_seconds)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (game, played_on, played_at, json.dumps(details), location_id, duration_seconds),
        )
        match_id = cur.lastrowid
        _insert_participants(conn, match_id, participants)
    return match_id


def load_added_games() -> list[dict]:
    """Games added from BoardGameGeek search (not defined in code)."""
    with connect() as conn:
        rows = conn.execute(
            "SELECT key, name, bgg_id, year, designers, thumbnail FROM games ORDER BY name"
        ).fetchall()
    return [
        {**dict(r), "designers": json.loads(r["designers"])} for r in rows
    ]


def save_added_game(key: str, name: str, bgg_id: int, year: str | None,
                    designers: list[str], thumbnail: str | None) -> None:
    with connect() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO games (key, name, bgg_id, year, designers, thumbnail) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (key, name, bgg_id, year, json.dumps(designers), thumbnail),
        )


def get_setting(key: str) -> str | None:
    with connect() as conn:
        row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else None


def set_setting(key: str, value: str) -> None:
    with connect() as conn:
        conn.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )


def recently_played_games(limit: int = 10) -> list[str]:
    """Game keys with at least one match, most recently played first."""
    with connect() as conn:
        rows = conn.execute(
            "SELECT game FROM matches "
            "GROUP BY game ORDER BY MAX(played_on || ' ' || COALESCE(played_at, '')) DESC "
            "LIMIT ?",
            (limit,),
        ).fetchall()
    return [r["game"] for r in rows]


def update_match(
    match_id: int,
    played_on: str,
    details: dict,
    participants: list[dict],
    played_at: str | None = None,
    location_id: int | None = None,
    duration_seconds: int | None = None,
) -> None:
    with connect() as conn:
        conn.execute(
            """UPDATE matches SET played_on = ?, played_at = ?, details = ?, location_id = ?,
                   duration_seconds = ? WHERE id = ?""",
            (played_on, played_at, json.dumps(details), location_id, duration_seconds, match_id),
        )
        conn.execute("DELETE FROM participants WHERE match_id = ?", (match_id,))
        _insert_participants(conn, match_id, participants)


def delete_match(game: str, match_id: int) -> None:
    with connect() as conn:
        conn.execute("DELETE FROM matches WHERE id = ? AND game = ?", (match_id, game))


def load_matches(
    game: str | None = None, limit: int | None = None, match_id: int | None = None
) -> list[dict]:
    """Return matches (newest first) with their participants attached."""
    sql = """SELECT m.id, m.game, m.played_on, m.played_at, m.details, m.duration_seconds,
                    m.location_id, l.name AS location
             FROM matches m LEFT JOIN locations l ON l.id = m.location_id"""
    clauses: list[str] = []
    args: list = []
    if game is not None:
        clauses.append("m.game = ?")
        args.append(game)
    if match_id is not None:
        clauses.append("m.id = ?")
        args.append(match_id)
    if clauses:
        sql += " WHERE " + " AND ".join(clauses)
    sql += " ORDER BY m.played_on DESC, m.played_at DESC, m.id DESC"
    if limit is not None:
        sql += " LIMIT ?"
        args.append(limit)

    with connect() as conn:
        rows = conn.execute(sql, args).fetchall()
        ids = [r["id"] for r in rows]
        participants: dict[int, list[dict]] = {i: [] for i in ids}
        if ids:
            placeholders = ",".join("?" * len(ids))
            part_rows = conn.execute(
                f"""SELECT p.match_id, p.player_id, p.label, p.is_player, p.won, p.score, p.rank,
                           p.details, pl.first_name, pl.last_name, pl.nickname
                    FROM participants p LEFT JOIN players pl ON pl.id = p.player_id
                    WHERE p.match_id IN ({placeholders}) ORDER BY p.id""",
                ids,
            ).fetchall()
            for p in part_rows:
                if p["player_id"] is not None:
                    name = display_name(p["first_name"], p["last_name"], p["nickname"])
                else:
                    name = p["label"]
                participants[p["match_id"]].append(
                    {
                        "player_id": p["player_id"],
                        "player": name,
                        "label": p["label"],
                        "is_player": bool(p["is_player"]),
                        "won": bool(p["won"]),
                        "score": p["score"],
                        "rank": p["rank"],
                        "details": json.loads(p["details"]),
                    }
                )

    return [
        {
            "id": r["id"],
            "game": r["game"],
            "played_on": r["played_on"],
            "played_at": r["played_at"],
            "details": json.loads(r["details"]),
            "duration_seconds": r["duration_seconds"],
            "location_id": r["location_id"],
            "location": r["location"],
            "participants": participants[r["id"]],
        }
        for r in rows
    ]
