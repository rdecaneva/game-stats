import pytest
from fastapi.testclient import TestClient

from app import db
from app.db import load_matches
from app.games import GAMES
from app.games.base import group_results
from app.main import app


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DB_PATH", str(tmp_path / "test.db"))
    with TestClient(app) as c:
        yield c


class FakeForm(dict):
    """Stands in for Starlette's FormData, which only needs .get()."""


def add_players(client, *names):
    """Create a player record for each 'First Last' name, so the match form can find them."""
    for i, name in enumerate(names):
        first, last = name.split(" ", 1)
        client.post(
            "/players/new",
            data={"first_name": first, "last_name": last, "email": f"p{i}@example.com"},
        )


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


def login_admin(client):
    """Like login(), but the account is promoted to admin first."""
    login(client)
    with db.connect() as conn:
        conn.execute("UPDATE players SET is_admin = 1 WHERE email_key = 'auth@example.com'")


def mtg_form(**extra):
    base = {
        "format": "Commander",
        "turns": "9",
        "player_0_name": "Ryan",
        "player_0_deck": "Atraxa",
        "player_1_name": "Sam",
        "player_1_deck": "Krenko",
        "player_0_winner": "1",
    }
    base.update(extra)
    return FakeForm(base)


def test_parse_marks_single_winner_and_types_values():
    game = GAMES["mtg"]
    details, participants, errors = game.parse_form(mtg_form())

    assert errors == []
    assert details == {"format": "Commander", "turns": 9}
    assert [p["player"] for p in participants] == ["Ryan", "Sam"]
    assert [p["won"] for p in participants] == [True, False]
    assert participants[0]["details"] == {"deck": "Atraxa"}


def test_parse_reports_missing_format_and_too_few_players():
    game = GAMES["mtg"]
    form = mtg_form(format="", player_1_name="")
    _, participants, errors = game.parse_form(form)

    assert "Format is required." in errors
    assert "Enter at least two players." in errors
    assert len(participants) == 1


def test_parse_rejects_non_numeric_turns():
    game = GAMES["mtg"]
    _, _, errors = game.parse_form(mtg_form(turns="lots"))
    assert "Turns must be a whole number." in errors


def test_group_results_sorts_by_win_rate_and_skips_missing_keys():
    rows = group_results(
        [("A", True), ("A", False), ("B", True), ("B", True), ("C", True), (None, True)]
    )
    assert rows == [
        ["B", 2, 2, "100%"],
        ["C", 1, 1, "100%"],
        ["A", 2, 1, "50%"],
    ]


def test_create_match_then_show_on_game_page(client):
    login(client)
    add_players(client, "Ryan Smith", "Sam Jones", "Lee Park")
    resp = client.post(
        "/games/mtg/new",
        data={
            "played_on": "2026-10-01",
            "format": "Commander",
            "turns": "11",
            "player_0_name": "Ryan Smith",
            "player_0_deck": "Atraxa",
            "player_1_name": "Sam Jones",
            "player_1_deck": "Krenko",
            "player_2_name": "Lee Park",
            "player_2_deck": "Edgar",
            "player_2_winner": "1",
        },
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert resp.headers["location"] == "/games/mtg"

    page = client.get("/games/mtg")
    assert page.status_code == 200
    assert "Krenko" in page.text
    assert "Average turns" in page.text

    matches = load_matches("mtg")
    assert len(matches) == 1
    winners = [p["player"] for p in matches[0]["participants"] if p["won"]]
    assert winners == ["Lee Park"]


def test_invalid_submission_rerenders_form_with_errors(client):
    login(client)
    resp = client.post("/games/mtg/new", data={"format": "", "player_0_name": "Ryan"})
    assert resp.status_code == 422
    assert "Enter at least two players." in resp.text
    assert load_matches("mtg") == []


def test_delete_match(client):
    login_admin(client)
    add_players(client, "Ann Ax", "Ben Bee")
    client.post(
        "/games/mtg/new",
        data={"format": "Modern", "player_0_name": "Ann Ax", "player_1_name": "Ben Bee", "player_1_winner": "1"},
    )
    match_id = load_matches("mtg")[0]["id"]

    client.post(f"/games/mtg/matches/{match_id}/delete", follow_redirects=False)
    assert load_matches("mtg") == []


def test_non_admin_cannot_delete_a_match(client):
    login(client)
    add_players(client, "Ann Ax", "Ben Bee")
    client.post(
        "/games/mtg/new",
        data={"format": "Modern", "player_0_name": "Ann Ax", "player_1_name": "Ben Bee", "player_1_winner": "1"},
    )
    match_id = load_matches("mtg")[0]["id"]

    assert client.post(f"/games/mtg/matches/{match_id}/delete").status_code == 403
    assert len(load_matches("mtg")) == 1


def test_unknown_game_is_404(client):
    login(client)
    assert client.get("/games/chess").status_code == 404


def test_non_admin_cannot_edit_a_match(client):
    login(client)
    add_players(client, "Ann Ax", "Ben Bee")
    client.post(
        "/games/mtg/new",
        data={"format": "Modern", "player_0_name": "Ann Ax", "player_1_name": "Ben Bee", "player_1_winner": "1"},
    )
    match_id = load_matches("mtg")[0]["id"]
    assert client.get(f"/games/mtg/matches/{match_id}/edit").status_code == 403
    assert client.post(f"/games/mtg/matches/{match_id}/edit", data={}).status_code == 403


def test_admin_can_edit_a_match(client):
    login_admin(client)
    add_players(client, "Ann Ax", "Ben Bee", "Cam Cee")
    client.post(
        "/games/mtg/new",
        data={
            "format": "Modern",
            "player_0_name": "Ann Ax",
            "player_0_deck": "Atraxa",
            "player_1_name": "Ben Bee",
            "player_1_winner": "1",
        },
    )
    match_id = load_matches("mtg")[0]["id"]

    edit_page = client.get(f"/games/mtg/matches/{match_id}/edit")
    assert edit_page.status_code == 200
    assert 'value="Atraxa"' in edit_page.text
    assert 'name="player_1_winner" value="1" checked' in edit_page.text

    resp = client.post(
        f"/games/mtg/matches/{match_id}/edit",
        data={
            "format": "Commander",
            "player_0_name": "Ann Ax",
            "player_0_deck": "Atraxa",
            "player_1_name": "Ben Bee",
            "player_2_name": "Cam Cee",
            "player_2_winner": "1",
        },
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert resp.headers["location"] == f"/games/mtg/matches/{match_id}"

    matches = load_matches("mtg")
    assert len(matches) == 1
    match = matches[0]
    assert match["details"]["format"] == "Commander"
    assert [p["player"] for p in match["participants"]] == ["Ann Ax", "Ben Bee", "Cam Cee"]
    winners = [p["player"] for p in match["participants"] if p["won"]]
    assert winners == ["Cam Cee"]


def test_editing_an_unknown_match_is_404(client):
    login_admin(client)
    assert client.get("/games/mtg/matches/9999/edit").status_code == 404


def test_non_admin_cannot_manage_locations(client):
    login(client)
    assert client.get("/locations").status_code == 403


def test_admin_can_create_a_location_with_details(client):
    login_admin(client)
    resp = client.post(
        "/locations/new",
        data={"name": "Kitchen table", "type": "House", "city": "Springfield"},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    loc = db.list_locations()[0]
    assert loc["name"] == "Kitchen table"
    assert loc["type"] == "House"
    assert loc["city"] == "Springfield"


def test_admin_can_rename_a_location(client):
    login_admin(client)
    add_players(client, "Ann Ax", "Ben Bee")
    client.post(
        "/games/mtg/new",
        data={
            "format": "Commander",
            "location": "Kitchen table",
            "player_0_name": "Ann Ax",
            "player_1_name": "Ben Bee",
        },
    )
    loc = db.list_locations()[0]
    resp = client.post(f"/locations/{loc['id']}/edit", data={"name": "Living room"}, follow_redirects=False)
    assert resp.status_code == 303
    assert db.list_locations()[0]["name"] == "Living room"
    assert load_matches("mtg")[0]["location"] == "Living room"


def test_renaming_to_a_duplicate_name_is_rejected(client):
    login_admin(client)
    db.location_id_for("Kitchen table")
    db.location_id_for("Living room")
    other = next(l for l in db.list_locations() if l["name"] == "Living room")
    resp = client.post(f"/locations/{other['id']}/edit", data={"name": "kitchen  TABLE"})
    assert resp.status_code == 422
    assert "already called" in resp.text


def test_creating_a_location_without_a_name_is_rejected(client):
    login_admin(client)
    resp = client.post("/locations/new", data={"name": ""})
    assert resp.status_code == 422
    assert "Name is required" in resp.text
    assert db.list_locations() == []


def test_non_admin_cannot_create_a_location(client):
    login(client)
    assert client.get("/locations/new").status_code == 403
    assert client.post("/locations/new", data={"name": "Kitchen table"}).status_code == 403


def test_editing_an_unknown_location_is_404(client):
    login_admin(client)
    assert client.get("/locations/9999/edit").status_code == 404


def test_map_preview_shown_only_with_an_address(client):
    login_admin(client)
    client.post("/locations/new", data={"name": "No Address Yet"})
    loc = db.list_locations()[0]
    no_address_page = client.get(f"/locations/{loc['id']}/edit")
    assert "map-preview" not in no_address_page.text

    client.post(f"/locations/{loc['id']}/edit", data={"name": "No Address Yet", "city": "Springfield"})
    with_address_page = client.get(f"/locations/{loc['id']}/edit")
    assert "map-preview" in with_address_page.text
    assert "maps.google.com/maps?q=" in with_address_page.text
    assert "Springfield" in with_address_page.text


def test_admin_can_delete_a_location_and_matches_keep_history(client):
    login_admin(client)
    add_players(client, "Ann Ax", "Ben Bee")
    client.post(
        "/games/mtg/new",
        data={
            "format": "Commander",
            "location": "Kitchen table",
            "player_0_name": "Ann Ax",
            "player_1_name": "Ben Bee",
        },
    )
    loc = db.list_locations()[0]
    resp = client.post(f"/locations/{loc['id']}/delete", follow_redirects=False)
    assert resp.status_code == 303
    assert db.list_locations() == []
    assert load_matches("mtg")[0]["location"] is None
