import json
import os
import sqlite3
from contextlib import contextmanager

SCHEMA = """
CREATE TABLE IF NOT EXISTS matches (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    game       TEXT NOT NULL,
    played_on  TEXT NOT NULL,
    played_at  TEXT,
    details    TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS participants (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    match_id INTEGER NOT NULL REFERENCES matches(id) ON DELETE CASCADE,
    player   TEXT NOT NULL,
    won      INTEGER NOT NULL DEFAULT 0,
    details  TEXT NOT NULL DEFAULT '{}'
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
"""


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


def init_db() -> None:
    with connect() as conn:
        conn.executescript(SCHEMA)
        try:
            conn.execute("ALTER TABLE matches ADD COLUMN played_at TEXT")
        except sqlite3.OperationalError:
            pass  # already added by a previous run


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


def insert_match(game: str, played_on: str, played_at: str | None, details: dict, participants: list[dict]) -> int:
    with connect() as conn:
        cur = conn.execute(
            "INSERT INTO matches (game, played_on, played_at, details) VALUES (?, ?, ?, ?)",
            (game, played_on, played_at, json.dumps(details)),
        )
        match_id = cur.lastrowid
        conn.executemany(
            "INSERT INTO participants (match_id, player, won, details) VALUES (?, ?, ?, ?)",
            [
                (match_id, p["player"], int(p["won"]), json.dumps(p["details"]))
                for p in participants
            ],
        )
    return match_id


def delete_match(game: str, match_id: int) -> None:
    with connect() as conn:
        conn.execute("DELETE FROM matches WHERE id = ? AND game = ?", (match_id, game))


def load_matches(game: str | None = None, limit: int | None = None) -> list[dict]:
    """Return matches (newest first) with their participants attached."""
    sql = "SELECT id, game, played_on, played_at, details FROM matches"
    args: list = []
    if game is not None:
        sql += " WHERE game = ?"
        args.append(game)
    sql += " ORDER BY played_on DESC, played_at DESC, id DESC"
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
                f"SELECT match_id, player, won, details FROM participants "
                f"WHERE match_id IN ({placeholders}) ORDER BY id",
                ids,
            ).fetchall()
            for p in part_rows:
                participants[p["match_id"]].append(
                    {
                        "player": p["player"],
                        "won": bool(p["won"]),
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
            "participants": participants[r["id"]],
        }
        for r in rows
    ]
