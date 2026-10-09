import pytest
from fastapi.testclient import TestClient

from app import db
from app.db import display_name, norm
from app.main import app


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DB_PATH", str(tmp_path / "test.db"))
    monkeypatch.setenv("SECRET_KEY", "test-secret")
    with TestClient(app) as c:
        yield c


def player_data(**over):
    data = {
        "first_name": "Ryan",
        "last_name": "de'Caneva",
        "nickname": "",
        "email": "ryan@example.com",
        "phone": "",
        "notes": "",
    }
    data.update(over)
    return data


def create(client, **over):
    return client.post("/players/new", data=player_data(**over), follow_redirects=False)


def test_norm_ignores_case_and_composed_vs_decomposed_accents():
    assert norm("José") == norm("JOSÉ") == norm("José")


def test_display_name_puts_nickname_in_brackets():
    assert display_name("Ryan", "de'Caneva", "Rye") == "Ryan de'Caneva (Rye)"
    assert display_name("Ryan", "de'Caneva", "") == "Ryan de'Caneva"


def test_same_name_is_allowed_when_emails_differ(client):
    assert create(client).status_code == 303
    assert create(client, email="other@example.com").status_code == 303
    assert create(client, email="third@example.com", nickname="Rye").status_code == 303


def test_nickname_is_optional_for_unique_names(client):
    assert create(client, nickname="").status_code == 303


def test_duplicate_email_is_rejected(client):
    create(client)
    r = create(client, first_name="Sam", last_name="Lee", email="RYAN@example.com")
    assert r.status_code == 422
    assert "already uses this email" in r.text


def test_contact_details_hidden_from_visitors_by_default(client):
    create(client)
    create(client, first_name="Sam", last_name="Lee", email="sam@example.com")
    login_as(client, 2, "sam@example.com")
    assert "ryan@example.com" not in client.get("/players/1").text


def test_contact_details_shown_when_player_opts_in(client):
    create(client, show_email="on")
    create(client, first_name="Sam", last_name="Lee", email="sam@example.com")
    login_as(client, 2, "sam@example.com")
    assert "ryan@example.com" in client.get("/players/1").text


def test_login_flow_reveals_own_details(client):
    create(client)
    r = client.post("/players/1/login-link", follow_redirects=False)
    token = r.headers["location"].rsplit("/", 1)[1]

    r = client.post(
        f"/confirm/{token}",
        data={"password": "correct horse", "confirm": "correct horse"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert "ryan@example.com" in client.get("/players/1").text

    client.post("/logout")
    assert "ryan@example.com" not in client.get("/players/1").text


def test_login_rejects_wrong_password(client):
    create(client)
    token = client.post("/players/1/login-link", follow_redirects=False).headers["location"].rsplit("/", 1)[1]
    client.post(f"/confirm/{token}", data={"password": "correct horse", "confirm": "correct horse"})
    client.post("/logout")

    r = client.post("/login", data={"email": "ryan@example.com", "password": "nope"})
    assert r.status_code == 401


def test_match_is_recorded_against_player_record(client):
    create(client)
    create(client, first_name="Sam", last_name="Lee", email="sam@example.com")
    login_as(client, 1, "ryan@example.com")
    r = client.post(
        "/games/mtg/new",
        data={
            "format": "Commander",
            "player_0_name": "Ryan de'Caneva",
            "player_1_name": "Sam Lee",
            "player_0_winner": "1",
        },
        follow_redirects=False,
    )
    assert r.status_code == 303

    match = db.load_matches("mtg")[0]
    assert [p["player_id"] for p in match["participants"]] == [1, 2]
    assert [p["player"] for p in match["participants"]] == ["Ryan de'Caneva", "Sam Lee"]


def test_match_rejects_ambiguous_player_name(client):
    create(client)
    create(client, email="other@example.com")
    login_as(client, 1, "ryan@example.com")
    r = client.post(
        "/games/mtg/new",
        data={"format": "Commander", "player_0_name": "Ryan de'Caneva", "player_1_name": "Ryan de'Caneva"},
    )
    assert r.status_code == 422
    assert "More than one player" in r.text


def test_match_rejects_unknown_player(client):
    create(client)
    login_as(client, 1, "ryan@example.com")
    r = client.post(
        "/games/mtg/new",
        data={"format": "Commander", "player_0_name": "Nobody", "player_1_name": "Also Nobody"},
    )
    assert r.status_code == 422
    assert "No player called" in r.text


def test_player_page_shows_record_across_games(client):
    create(client)
    create(client, first_name="Sam", last_name="Lee", email="sam@example.com")
    login_as(client, 1, "ryan@example.com")
    client.post(
        "/games/mtg/new",
        data={"format": "Commander", "player_0_name": "Ryan de'Caneva", "player_1_name": "Sam Lee", "player_0_winner": "1"},
    )
    r = client.get("/players/1")
    assert r.status_code == 200
    assert "Matches played" in r.text
    assert "Magic: The Gathering" in r.text


def login_as(client, player_id, email, password="correct horse"):
    token = client.post(f"/players/{player_id}/login-link", follow_redirects=False).headers["location"].rsplit("/", 1)[1]
    client.post(f"/confirm/{token}", data={"password": password, "confirm": password})
    client.post("/logout")
    client.post("/login", data={"email": email, "password": password})


def test_edit_form_requires_login(client):
    create(client)
    assert client.get("/players/1/edit", follow_redirects=False).status_code == 401


def test_player_can_edit_own_details(client):
    create(client)
    login_as(client, 1, "ryan@example.com")
    assert client.get("/players/1/edit").status_code == 200
    r = client.post("/players/1/edit", data=player_data(phone="0400 000 000"), follow_redirects=False)
    assert r.status_code == 303
    assert db.get_player(1)["phone"] == "0400 000 000"


def test_player_cannot_edit_someone_else(client):
    create(client)
    create(client, first_name="Sam", last_name="Lee", email="sam@example.com")
    login_as(client, 2, "sam@example.com")
    assert client.get("/players/1/edit").status_code == 403
    r = client.post("/players/1/edit", data=player_data(phone="999"))
    assert r.status_code == 403
    assert db.get_player(1)["phone"] == ""


def test_admin_can_edit_anyone_and_sees_private_details(client):
    create(client)
    create(client, first_name="Sam", last_name="Lee", email="sam@example.com")
    login_as(client, 1, "ryan@example.com")
    with db.connect() as conn:
        conn.execute("UPDATE players SET is_admin = 1 WHERE id = 1")
    client.post("/logout")
    client.post("/login", data={"email": "ryan@example.com", "password": "correct horse"})

    assert "sam@example.com" in client.get("/players/2").text
    assert client.get("/players/2/edit").status_code == 200
    r = client.post("/players/2/edit", data=player_data(first_name="Sam", last_name="Lee", email="sam@example.com", phone="123"), follow_redirects=False)
    assert r.status_code == 303
    assert db.get_player(2)["phone"] == "123"


def make_admin_session(client):
    create(client)
    create(client, first_name="Sam", last_name="Lee", email="sam@example.com")
    login_as(client, 1, "ryan@example.com")
    with db.connect() as conn:
        conn.execute("UPDATE players SET is_admin = 1 WHERE id = 1")
    client.post("/logout")
    client.post("/login", data={"email": "ryan@example.com", "password": "correct horse"})


def test_admin_can_make_another_player_admin(client):
    make_admin_session(client)
    r = client.post("/players/2/admin", data={"make_admin": "1", "next": "/players"}, follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/players"
    assert db.get_player(2)["is_admin"] is True
    assert "Remove admin" in client.get("/players").text


def test_admin_can_remove_another_admin(client):
    make_admin_session(client)
    client.post("/players/2/admin", data={"make_admin": "1"})
    client.post("/players/2/admin", data={"make_admin": "0"})
    assert db.get_player(2)["is_admin"] is False


def test_admin_cannot_remove_own_rights(client):
    make_admin_session(client)
    r = client.post("/players/1/admin", data={"make_admin": "0"})
    assert r.status_code == 422
    assert db.get_player(1)["is_admin"] is True


def test_non_admin_cannot_change_admin_rights(client):
    create(client)
    create(client, first_name="Sam", last_name="Lee", email="sam@example.com")
    login_as(client, 2, "sam@example.com")
    assert client.post("/players/1/admin", data={"make_admin": "1"}).status_code == 403
    assert db.get_player(1)["is_admin"] is False


def test_admin_toggle_ignores_offsite_redirect(client):
    make_admin_session(client)
    r = client.post("/players/2/admin", data={"make_admin": "1", "next": "https://evil.example"}, follow_redirects=False)
    assert r.headers["location"] == "/players/2"


def test_non_admin_cannot_delete_a_player(client):
    create(client)
    create(client, first_name="Sam", last_name="Lee", email="sam@example.com")
    login_as(client, 2, "sam@example.com")
    assert client.post("/players/1/delete").status_code == 403
    assert db.get_player(1) is not None


def test_admin_can_delete_a_player_and_their_matches_keep_the_name(client):
    make_admin_session(client)
    client.post(
        "/games/mtg/new",
        data={"format": "Commander", "player_0_name": "Ryan de'Caneva", "player_1_name": "Sam Lee", "player_0_winner": "1"},
    )
    r = client.post("/players/2/delete", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/players"
    assert db.get_player(2) is None
    assert "Sam Lee" not in client.get("/players").text

    match = db.load_matches("mtg")[0]
    sam = next(p for p in match["participants"] if p["player"] == "Sam Lee")
    assert sam["player_id"] is None


def test_deleting_your_own_account_logs_you_out(client):
    make_admin_session(client)
    client.post("/players/2/admin", data={"make_admin": "1"})  # a second admin, so player 1 isn't the last one
    client.post("/players/1/delete")
    assert client.get("/players/1/edit", follow_redirects=False).status_code == 401


def test_cannot_delete_the_last_admin(client):
    make_admin_session(client)
    r = client.post("/players/1/delete")
    assert r.status_code == 422
    assert db.get_player(1) is not None




def test_edit_page_shows_admin_button_to_admins(client):
    make_admin_session(client)
    assert "Make admin" in client.get("/players/2/edit").text
