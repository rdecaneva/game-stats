import sqlite3

import pytest
from fastapi.testclient import TestClient

from app import db, stats
from app.charts import duration_chart, score_chart, win_pie
from app.main import app
from app.ranking import assign_ranks


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DB_PATH", str(tmp_path / "test.db"))
    with TestClient(app) as c:
        yield c


def add_players(client, *names):
    for i, name in enumerate(names):
        first, last = name.split(" ", 1)
        client.post("/players/new", data={"first_name": first, "last_name": last, "email": f"p{i}@example.com"})


def login(client):
    """Create a throwaway player and log in as them, so the client can reach gated routes."""
    r = client.post(
        "/players/new",
        data={"first_name": "Auth", "last_name": "User", "email": "auth@example.com"},
        follow_redirects=False,
    )
    player_id = r.headers["location"].rsplit("/", 1)[1]
    r = client.post(f"/players/{player_id}/login-link", follow_redirects=False)
    token = r.headers["location"].rsplit("/", 1)[1]
    client.post(f"/confirm/{token}", data={"password": "test-password", "confirm": "test-password"})


def play(client, date="2026-10-01", location="", duration="", **extra):
    data = {
        "played_on": date,
        "format": "Commander",
        "location": location,
        "duration_seconds": duration,
        "player_0_name": "Ann Ax",
        "player_1_name": "Ben Bee",
        "player_0_winner": "1",
    }
    data.update(extra)
    return client.post("/games/mtg/new", data=data, follow_redirects=False)


def participant(name, **over):
    p = {"player": name, "label": name, "player_id": 1, "is_player": True, "won": False,
         "score": None, "rank": None, "details": {}}
    p.update(over)
    return p


# ---- ranks -----------------------------------------------------------------

def test_ranks_come_from_scores_with_ties_sharing_a_rank():
    ps = [participant("A", score=10), participant("B", score=12), participant("C", score=10)]
    assign_ranks(ps)
    assert [p["rank"] for p in ps] == [2, 1, 2]


def test_ranks_fall_back_to_winner_when_scores_are_missing():
    ps = [participant("A", won=True), participant("B", score=5), participant("C")]
    assign_ranks(ps)
    assert [p["rank"] for p in ps] == [1, 2, 2]


def test_non_players_are_never_ranked():
    ps = [participant("A", won=True), participant("Bot", is_player=False)]
    assign_ranks(ps)
    assert ps[1]["rank"] is None


# ---- stats scope -----------------------------------------------------------

def test_scope_drops_non_players_and_filters_location():
    matches = [
        {"location_id": 1, "participants": [participant("A"), participant("Bot", is_player=False)]},
        {"location_id": 2, "participants": [participant("A")]},
    ]
    scoped = stats.scope(matches, location_id=1)
    assert len(scoped) == 1
    assert [p["player"] for p in scoped[0]["participants"]] == ["A"]


def test_duration_summary_formats_total_average_and_longest():
    matches = [{"duration_seconds": 3600}, {"duration_seconds": 1800}, {"duration_seconds": None}]
    assert stats.duration_summary(matches) == [
        ("Total play time", "1h 30m"),
        ("Average play", "45m"),
        ("Longest play", "1h 00m"),
    ]


# ---- routes ----------------------------------------------------------------

def test_guest_and_non_player_are_saved_and_excluded_from_stats(client):
    login(client)
    add_players(client, "Ann Ax")
    play(
        client,
        player_1_name="Auto Bot",
        player_1_guest="1",
        player_1_nonplayer="1",
        player_2_name="Guest Gus",
        player_2_guest="1",
        player_0_score="7",
        player_2_score="4",
    )
    match = db.load_matches("mtg")[0]
    by_name = {p["player"]: p for p in match["participants"]}
    assert by_name["Auto Bot"]["is_player"] is False
    assert by_name["Guest Gus"]["player_id"] is None
    assert by_name["Ann Ax"]["rank"] == 1

    page = client.get("/games/mtg")
    assert "Auto Bot" not in page.text.split("<h2>Matches</h2>")[0]
    assert "Guest Gus" in page.text


def test_unknown_name_without_guest_tick_is_still_rejected(client):
    login(client)
    add_players(client, "Ann Ax")
    resp = play(client, player_1_name="Nobody Here")
    assert resp.status_code == 422
    assert "Add them under Players first" in resp.text


def test_non_player_cannot_win(client):
    login(client)
    add_players(client, "Ann Ax")
    resp = play(client, player_1_name="Auto Bot", player_1_guest="1", player_1_nonplayer="1", player_1_winner="1")
    assert resp.status_code == 422
    assert "is not competing" in resp.text


def test_location_is_created_once_and_filters_the_game_page(client):
    login(client)
    add_players(client, "Ann Ax", "Ben Bee")
    play(client, location="Kitchen table")
    play(client, date="2026-10-02", location="kitchen  TABLE")
    play(client, date="2026-10-03", location="")
    assert len(db.list_locations()) == 1

    loc = db.list_locations()[0]["id"]
    page = client.get(f"/games/mtg?location={loc}")
    assert page.text.count('class="match-date"') == 2


def test_duration_from_timer_is_saved_and_charted(client):
    login(client)
    add_players(client, "Ann Ax", "Ben Bee")
    play(client, duration="5400")
    match = db.load_matches("mtg")[0]
    assert match["duration_seconds"] == 5400
    page = client.get("/games/mtg")
    assert "1h 30m" in page.text
    assert 'aria-label="Play length in minutes, by play"' in page.text


def test_minutes_typed_by_hand_are_converted(client):
    login(client)
    add_players(client, "Ann Ax", "Ben Bee")
    data = {"duration_minutes": "12.5"}
    resp = play(client, **data)
    assert resp.status_code == 303
    assert db.load_matches("mtg")[0]["duration_seconds"] == 750


def test_match_page_shows_ranks_and_scores(client):
    login(client)
    add_players(client, "Ann Ax", "Ben Bee")
    play(client, player_0_score="9", player_1_score="11")
    match_id = db.load_matches("mtg")[0]["id"]
    page = client.get(f"/games/mtg/matches/{match_id}")
    assert page.status_code == 200
    assert "<td>1</td>" in page.text and "<td>11</td>" in page.text
    assert client.get("/games/mtg/matches/9999").status_code == 404


def test_score_rows_and_pie_render(client):
    login(client)
    add_players(client, "Ann Ax", "Ben Bee")
    play(client, player_0_score="9", player_1_score="11")
    rows = stats.score_rows(db.load_matches("mtg"))
    assert rows[0][0] == "Ben Bee" and rows[0][3] == "11.0"
    svg = win_pie(stats.win_counts(db.load_matches("mtg")), stats.colour_slots(db.load_matches("mtg")))
    assert "Ann Ax" in svg and "var(--series-1)" in svg


def test_win_pie_folds_extra_players_into_other():
    wins = [(f"P{i}", 10 - i) for i in range(10)]
    slots = {name: i for i, (name, _) in enumerate(wins)}
    svg = win_pie(wins, slots)
    assert "Other" in svg
    assert "var(--series-9)" not in svg


def test_empty_charts_show_a_message():
    assert "No timed plays yet." in duration_chart([])
    assert "No scores recorded yet." in score_chart([])


def test_player_page_location_filter(client):
    login(client)
    add_players(client, "Ann Ax", "Ben Bee")
    play(client, location="Cafe")
    play(client, date="2026-10-02", location="Home")
    loc = [l for l in db.list_locations() if l["name"] == "Cafe"][0]["id"]
    page = client.get(f"/players/1?location={loc}")
    assert "Matches played" in page.text
    assert client.get("/players/1").status_code == 200


# ---- migration -------------------------------------------------------------

def test_old_participants_table_is_rebuilt_and_keeps_rows(tmp_path, monkeypatch):
    path = tmp_path / "old.db"
    monkeypatch.setenv("DB_PATH", str(path))
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE players (id INTEGER PRIMARY KEY AUTOINCREMENT, first_name TEXT NOT NULL,
          last_name TEXT NOT NULL, nickname TEXT NOT NULL DEFAULT '', email TEXT NOT NULL,
          phone TEXT NOT NULL DEFAULT '', notes TEXT NOT NULL DEFAULT '', first_key TEXT NOT NULL,
          last_key TEXT NOT NULL, nick_key TEXT NOT NULL DEFAULT '', email_key TEXT NOT NULL UNIQUE,
          show_email INTEGER NOT NULL DEFAULT 0, show_phone INTEGER NOT NULL DEFAULT 0,
          password_hash TEXT, confirm_token TEXT, created_at TEXT NOT NULL DEFAULT (datetime('now')));
        CREATE TABLE matches (id INTEGER PRIMARY KEY AUTOINCREMENT, game TEXT NOT NULL,
          played_on TEXT NOT NULL, details TEXT NOT NULL DEFAULT '{}',
          created_at TEXT NOT NULL DEFAULT (datetime('now')));
        CREATE TABLE participants (id INTEGER PRIMARY KEY AUTOINCREMENT,
          match_id INTEGER NOT NULL REFERENCES matches(id) ON DELETE CASCADE,
          player_id INTEGER NOT NULL REFERENCES players(id), won INTEGER NOT NULL DEFAULT 0,
          details TEXT NOT NULL DEFAULT '{}');
        CREATE INDEX idx_participants_match ON participants(match_id);
        CREATE INDEX idx_participants_player ON participants(player_id);
        INSERT INTO players (id, first_name, last_name, email, first_key, last_key, email_key)
          VALUES (1, 'Ann', 'Ax', 'a@x.com', 'ann', 'ax', 'a@x.com');
        INSERT INTO matches (id, game, played_on) VALUES (1, 'mtg', '2026-01-01');
        INSERT INTO participants (match_id, player_id, won) VALUES (1, 1, 1);
        """
    )
    conn.commit()
    conn.close()

    db.init_db()
    matches = db.load_matches("mtg")
    assert matches[0]["participants"][0]["player"] == "Ann Ax"
    assert matches[0]["participants"][0]["won"] is True
    db.init_db()  # running again is harmless
