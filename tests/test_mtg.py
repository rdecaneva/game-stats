import pytest
from fastapi.testclient import TestClient

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


def mtg_form(**extra):
    base = {
        "format": "Commander",
        "turns": "9",
        "player_0_name": "Ryan",
        "player_0_deck": "Atraxa",
        "player_1_name": "Sam",
        "player_1_deck": "Krenko",
        "winner": "0",
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
    resp = client.post(
        "/games/mtg/new",
        data={
            "played_on": "2026-10-01",
            "format": "Commander",
            "turns": "11",
            "player_0_name": "Ryan",
            "player_0_deck": "Atraxa",
            "player_1_name": "Sam",
            "player_1_deck": "Krenko",
            "player_2_name": "Lee",
            "player_2_deck": "Edgar",
            "winner": "2",
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
    assert winners == ["Lee"]


def test_invalid_submission_rerenders_form_with_errors(client):
    resp = client.post("/games/mtg/new", data={"format": "", "player_0_name": "Ryan"})
    assert resp.status_code == 422
    assert "Enter at least two players." in resp.text
    assert load_matches("mtg") == []


def test_delete_match(client):
    client.post(
        "/games/mtg/new",
        data={"format": "Modern", "player_0_name": "A", "player_1_name": "B", "winner": "1"},
    )
    match_id = load_matches("mtg")[0]["id"]

    client.post(f"/games/mtg/matches/{match_id}/delete", follow_redirects=False)
    assert load_matches("mtg") == []


def test_unknown_game_is_404(client):
    assert client.get("/games/chess").status_code == 404
