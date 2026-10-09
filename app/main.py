import os
import re
import secrets
from contextlib import asynccontextmanager
from datetime import date
from pathlib import Path
from urllib.parse import quote

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from . import auth, bgg, charts, db, stats
from .db import (
    delete_match,
    get_setting,
    init_db,
    insert_match,
    load_matches,
    recently_played_games,
    save_added_game,
    set_setting,
    update_match,
)
from .games import all_games, get_game
from .games.base import Game, Table
from .players import validate_player
from .ranking import assign_ranks

BASE_DIR = Path(__file__).parent


@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_db()
    yield


app = FastAPI(title="Game Stats", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
templates = Jinja2Templates(directory=BASE_DIR / "templates")
# Cache-bust static assets so a deploy's CSS/JS changes show up without a hard refresh.
templates.env.globals["asset_version"] = int((BASE_DIR / "static" / "style.css").stat().st_mtime)


# Paths reachable without a session: logging in, setting a password from a login link,
# and creating a new player record (which is how someone gets into the system at all,
# before they have any login to speak of).
PUBLIC_PREFIXES = ("/static/", "/login", "/confirm/", "/players/new")
PUBLIC_PATTERNS = (re.compile(r"^/players/\d+/login-link$"),)


def _is_public(path: str) -> bool:
    return path.startswith(PUBLIC_PREFIXES) or any(p.match(path) for p in PUBLIC_PATTERNS)


@app.middleware("http")
async def attach_session(request: Request, call_next):
    player_id = auth.read_session(request.cookies.get(auth.COOKIE))
    request.state.player = db.get_player(player_id) if player_id else None
    if request.state.player is None and not _is_public(request.url.path):
        return templates.TemplateResponse(
            request, "login.html", {"errors": [], "email": ""}, status_code=401
        )
    return await call_next(request)


def _get_game(key: str) -> Game:
    game = get_game(key)
    if game is None:
        raise HTTPException(status_code=404, detail="Unknown game")
    return game


def _describe(game: Game, match: dict) -> dict:
    """Flatten a match into what the match list needs."""
    winners = [p["player"] for p in match["participants"] if p["won"]]
    seconds = match["duration_seconds"]
    return {
        "id": match["id"],
        "game_key": game.key,
        "game_name": game.name,
        "played_on": match["played_on"],
        "played_at": match["played_at"],
        "players": ", ".join(p["player"] for p in match["participants"]),
        "winner": ", ".join(winners) if winners else None,
        "details": match["details"],
        "location": match["location"],
        "duration": stats.format_duration(seconds) if seconds is not None else None,
    }


def _at_location(matches: list[dict], location_id: int | None) -> list[dict]:
    return [m for m in matches if location_id is None or m["location_id"] == location_id]


def _location_arg(location: str | None) -> int | None:
    return int(location) if location and location.isdigit() else None


def _session_cookie(response, player_id: int):
    response.set_cookie(
        auth.COOKIE,
        auth.session_value(player_id),
        httponly=True,
        samesite="lax",
        max_age=60 * 60 * 24 * 30,
    )
    return response


# ---- menu and games ------------------------------------------------------

@app.get("/", response_class=HTMLResponse)
def home(request: Request):
    return templates.TemplateResponse(request, "menu.html", {})


@app.get("/games", response_class=HTMLResponse)
def games_index(request: Request, q: str = ""):
    q = q.strip()
    games = all_games()
    if q:
        needle = db.norm(q)
        matches = [g for g in games.values() if needle in db.norm(g.name)]
    else:
        recent_keys = recently_played_games(limit=10)
        matches = [games[key] for key in recent_keys if key in games]
        if not matches:
            matches = list(games.values())
    # Only search BoardGameGeek when the query has no local match, to avoid needless lookups.
    found = bgg.search(q) if q and not matches else []
    recent = [
        _describe(games[m["game"]], m) for m in load_matches(limit=10) if m["game"] in games
    ]
    return templates.TemplateResponse(
        request,
        "games.html",
        {
            "q": q,
            "games": matches,
            "found": found,
            "bgg_configured": bgg.configured(),
            "already_added": {g.bgg_id for g in games.values() if g.bgg_id},
            "recent": recent,
        },
    )


@app.post("/games/add")
async def add_game(request: Request):
    form = await request.form()
    try:
        bgg_id = int(form.get("bgg_id") or 0)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid BoardGameGeek id")
    info = bgg.fetch(bgg_id) if bgg_id else None
    name = (form.get("name") or "").strip()
    if not bgg_id or not name:
        raise HTTPException(status_code=400, detail="Missing game details")
    key = f"bgg-{bgg_id}"
    save_added_game(
        key,
        name,
        bgg_id,
        info.year if info else None,
        list(info.designers) if info else [],
        info.thumbnail if info else None,
    )
    return RedirectResponse(f"/games/{key}", status_code=303)


@app.get("/games/{key}", response_class=HTMLResponse)
def game_page(request: Request, key: str, location: str | None = None):
    game = _get_game(key)
    location_id = _location_arg(location)
    everything = load_matches(game.key)
    here = _at_location(everything, location_id)
    # Stats count competing players only; the match list still shows every play.
    counted = stats.scope(everything, location_id)
    scores = stats.score_rows(counted)
    tables = game.stats(counted) + [
        Table("Scores", ["Player", "Scored plays", "Low", "Average", "High"], scores)
    ]
    timed = [(m["played_on"], m["duration_seconds"]) for m in reversed(here) if m["duration_seconds"] is not None]
    chart_blocks = [
        {"title": "Play length", "svg": charts.duration_chart(timed)},
        {"title": "Average score by player", "svg": charts.score_chart(scores)},
        {"title": "Wins by player", "svg": charts.win_pie(stats.win_counts(counted), stats.colour_slots(everything))},
    ]
    return templates.TemplateResponse(
        request,
        game.template,
        {
            "game": game,
            "bgg": game.bgg_info(),
            "summary": game.summary(counted) + stats.duration_summary(here),
            "tables": tables,
            "charts": chart_blocks,
            "matches": [_describe(game, m) for m in here],
            "locations": db.list_locations(),
            "location_id": location_id,
        },
    )


@app.get("/games/{key}/matches/{match_id}", response_class=HTMLResponse)
def match_page(request: Request, key: str, match_id: int):
    game = _get_game(key)
    found = load_matches(game.key, match_id=match_id)
    if not found:
        raise HTTPException(status_code=404, detail="Unknown play")
    match = found[0]
    # Ranked players first (1st, 2nd, ...), then non-players and anyone without a rank.
    rows = sorted(match["participants"], key=lambda p: (p["rank"] is None, p["rank"] or 0, p["player"]))
    return templates.TemplateResponse(
        request,
        "match.html",
        {"game": game, "match": _describe(game, match), "rows": rows},
    )


def _render_form(
    request: Request,
    game: Game,
    values: dict,
    errors: list[str],
    status: int = 200,
    action: str | None = None,
    heading: str | None = None,
    cancel_url: str | None = None,
):
    matches = load_matches(game.key)
    suggestions = game.suggestions(matches)
    # The player picker offers every player record, not just names seen in matches.
    suggestions["player"] = [p["display"] for p in db.list_players()]
    suggestions["location"] = [loc["name"] for loc in db.list_locations()]
    return templates.TemplateResponse(
        request,
        "new_match.html",
        {
            "game": game,
            "values": values,
            "errors": errors,
            "today": date.today().isoformat(),
            "suggestions": suggestions,
            "action": action,
            "heading": heading,
            "cancel_url": cancel_url,
        },
        status_code=status,
    )


def _match_to_values(match: dict) -> dict:
    """Flatten a loaded match back into the match form's field names, for editing."""
    values: dict = {
        "played_on": match["played_on"],
        "played_at": match["played_at"] or "",
        "location": match["location"] or "",
        **match["details"],
    }
    if match["duration_seconds"] is not None:
        values["duration_minutes"] = f"{match['duration_seconds'] / 60:.1f}"
    for i, p in enumerate(match["participants"]):
        values[f"player_{i}_name"] = p["player"]
        if p["player_id"] is None:
            values[f"player_{i}_guest"] = "1"
        if not p["is_player"]:
            values[f"player_{i}_nonplayer"] = "1"
        if p["won"]:
            values[f"player_{i}_winner"] = "1"
        if p["score"] is not None:
            values[f"player_{i}_score"] = p["score"]
        for k, v in p["details"].items():
            values[f"player_{i}_{k}"] = v
    return values


def _parse_match_form(game: Game, form) -> tuple[str, str | None, dict, list[dict], int | None, list[str]]:
    """Validate a submitted match form. Shared by create and edit."""
    errors: list[str] = []
    played_on = (form.get("played_on") or "").strip() or date.today().isoformat()
    try:
        date.fromisoformat(played_on)
    except ValueError:
        errors.append("Date is not valid.")
    played_at = (form.get("played_at") or "").strip() or None

    details, participants, parse_errors = game.parse_form(form)
    errors += parse_errors
    duration, duration_error = _duration_seconds(form)
    if duration_error:
        errors.append(duration_error)
    for p in participants:
        if p["guest"]:
            # Guests take part in the play but have no profile; the typed name is their label.
            p["player_id"] = None
            continue
        ids = db.find_player_ids_by_label(p["player"])
        if not ids:
            errors.append(f"No player called “{p['player']}”. Add them under Players first, or tick Guest.")
        elif len(ids) > 1:
            errors.append(f"More than one player is called “{p['player']}”. Give them distinct nicknames.")
        else:
            p["player_id"] = ids[0]
    return played_on, played_at, details, participants, duration, errors


def _require_admin(request: Request) -> None:
    viewer = request.state.player
    if viewer is None or not viewer["is_admin"]:
        raise HTTPException(status_code=403, detail="Only admins can do that")


def _duration_seconds(form) -> tuple[int | None, str | None]:
    """Seconds from the timer (duration_seconds), or typed minutes (duration_minutes)."""
    try:
        if (form.get("duration_seconds") or "").strip():
            seconds = int(form.get("duration_seconds"))
        elif (form.get("duration_minutes") or "").strip():
            seconds = round(float(form.get("duration_minutes")) * 60)
        else:
            return None, None
    except ValueError:
        return None, "Duration must be a number of minutes."
    if seconds < 0:
        return None, "Duration can't be negative."
    return seconds, None


@app.get("/games/{key}/new", response_class=HTMLResponse)
def new_match_form(request: Request, key: str):
    return _render_form(request, _get_game(key), {}, [])


@app.post("/games/{key}/new")
async def create_match(request: Request, key: str):
    game = _get_game(key)
    form = await request.form()
    values = {k: form.get(k) for k in form.keys()}

    played_on, played_at, details, participants, duration, errors = _parse_match_form(game, form)
    if errors:
        return _render_form(request, game, values, errors, status=422)

    assign_ranks(participants)
    insert_match(
        game.key,
        played_on,
        details,
        participants,
        played_at=played_at,
        location_id=db.location_id_for(form.get("location") or ""),
        duration_seconds=duration,
    )
    return RedirectResponse(f"/games/{game.key}", status_code=303)


@app.get("/games/{key}/matches/{match_id}/edit", response_class=HTMLResponse)
def edit_match_form(request: Request, key: str, match_id: int):
    _require_admin(request)
    game = _get_game(key)
    found = load_matches(game.key, match_id=match_id)
    if not found:
        raise HTTPException(status_code=404, detail="Unknown play")
    return _render_form(
        request,
        game,
        _match_to_values(found[0]),
        [],
        action=f"/games/{game.key}/matches/{match_id}/edit",
        heading="Edit Log Entry",
        cancel_url=f"/games/{game.key}/matches/{match_id}",
    )


@app.post("/games/{key}/matches/{match_id}/edit")
async def update_match_route(request: Request, key: str, match_id: int):
    _require_admin(request)
    game = _get_game(key)
    if not load_matches(game.key, match_id=match_id):
        raise HTTPException(status_code=404, detail="Unknown play")
    form = await request.form()
    values = {k: form.get(k) for k in form.keys()}

    played_on, played_at, details, participants, duration, errors = _parse_match_form(game, form)
    if errors:
        return _render_form(
            request,
            game,
            values,
            errors,
            status=422,
            action=f"/games/{game.key}/matches/{match_id}/edit",
            heading="Edit Log Entry",
            cancel_url=f"/games/{game.key}/matches/{match_id}",
        )

    assign_ranks(participants)
    update_match(
        match_id,
        played_on,
        details,
        participants,
        played_at=played_at,
        location_id=db.location_id_for(form.get("location") or ""),
        duration_seconds=duration,
    )
    return RedirectResponse(f"/games/{game.key}/matches/{match_id}", status_code=303)


@app.post("/games/{key}/matches/{match_id}/delete")
def delete_match_route(request: Request, key: str, match_id: int):
    _require_admin(request)
    _get_game(key)
    delete_match(key, match_id)
    return RedirectResponse(f"/games/{key}", status_code=303)


# ---- locations -------------------------------------------------------------

LOCATION_TYPES = ("House", "Business", "Other")


@app.get("/locations", response_class=HTMLResponse)
def locations_list(request: Request):
    _require_admin(request)
    return templates.TemplateResponse(request, "locations.html", {"locations": db.list_locations()})


def _map_embed_url(values: dict) -> str | None:
    """A keyless Google Maps embed for the address, or None without enough to search for."""
    parts = [values.get(k) for k in ("name", "street", "city", "region", "postal_code")]
    query = ", ".join(p for p in parts if p)
    if not any(values.get(k) for k in ("street", "city", "postal_code")):
        return None
    return f"https://maps.google.com/maps?q={quote(query)}&output=embed"


def _render_location_form(request, values, errors, title, action, status=200):
    return templates.TemplateResponse(
        request,
        "location_form.html",
        {
            "values": values,
            "errors": errors,
            "title": title,
            "action": action,
            "types": LOCATION_TYPES,
            "map_url": _map_embed_url(values),
        },
        status_code=status,
    )


@app.get("/locations/new", response_class=HTMLResponse)
def new_location_form(request: Request):
    _require_admin(request)
    return _render_location_form(request, {}, [], "New location", "/locations/new")


@app.post("/locations/new")
async def create_location(request: Request):
    _require_admin(request)
    form = await request.form()
    fields = {k: form.get(k) for k in form.keys()}
    location_id, error = db.save_location(fields)
    if error:
        return _render_location_form(request, fields, [error], "New location", "/locations/new", status=422)
    return RedirectResponse("/locations", status_code=303)


@app.get("/locations/{location_id}/edit", response_class=HTMLResponse)
def edit_location_form(request: Request, location_id: int):
    _require_admin(request)
    location = db.get_location(location_id)
    if location is None:
        raise HTTPException(status_code=404, detail="Unknown location")
    return _render_location_form(
        request, location, [], "Edit location", f"/locations/{location_id}/edit"
    )


@app.post("/locations/{location_id}/edit")
async def update_location(request: Request, location_id: int):
    _require_admin(request)
    if db.get_location(location_id) is None:
        raise HTTPException(status_code=404, detail="Unknown location")
    form = await request.form()
    fields = {k: form.get(k) for k in form.keys()}
    _, error = db.save_location(fields, location_id=location_id)
    if error:
        return _render_location_form(
            request, fields, [error], "Edit location", f"/locations/{location_id}/edit", status=422
        )
    return RedirectResponse("/locations", status_code=303)


@app.post("/locations/{location_id}/delete")
def delete_location_route(request: Request, location_id: int):
    _require_admin(request)
    if db.get_location(location_id) is None:
        raise HTTPException(status_code=404, detail="Unknown location")
    db.delete_location(location_id)
    return RedirectResponse("/locations", status_code=303)


# ---- players -------------------------------------------------------------

@app.get("/players", response_class=HTMLResponse)
def players_list(request: Request, q: str = ""):
    needle = db.norm(q)
    players = [p for p in db.list_players() if needle in db.norm(p["display"])]
    return templates.TemplateResponse(
        request, "players.html", {"players": players, "q": q, "admin_count": db.admin_count()}
    )


def _render_player_form(request, values, errors, title, action, status=200):
    return templates.TemplateResponse(
        request,
        "player_form.html",
        {"values": values, "errors": errors, "title": title, "action": action, "admin_count": db.admin_count()},
        status_code=status,
    )


@app.get("/players/new", response_class=HTMLResponse)
def new_player_form(request: Request):
    return _render_player_form(request, {}, [], "New player", "/players/new")


@app.post("/players/new")
async def create_player(request: Request):
    form = await request.form()
    fields, errors = validate_player(form)
    if errors:
        values = {k: form.get(k) for k in form.keys()}
        return _render_player_form(request, values, errors, "New player", "/players/new", 422)
    player_id = db.save_player(fields)
    return RedirectResponse(f"/players/{player_id}", status_code=303)


def _require_editor(request: Request, player_id: int) -> dict:
    """Players edit their own record; admins may edit anyone's."""
    viewer = request.state.player
    if viewer is None:
        raise HTTPException(status_code=401, detail="Log in to edit player details")
    if viewer["id"] != player_id and not viewer["is_admin"]:
        raise HTTPException(status_code=403, detail="You can only edit your own details")
    player = db.get_player(player_id)
    if player is None:
        raise HTTPException(status_code=404, detail="Unknown player")
    return player


@app.get("/players/{player_id}/edit", response_class=HTMLResponse)
def edit_player_form(request: Request, player_id: int):
    player = _require_editor(request, player_id)
    return _render_player_form(
        request, player, [], "Edit player", f"/players/{player_id}/edit"
    )


@app.post("/players/{player_id}/edit")
async def update_player(request: Request, player_id: int):
    _require_editor(request, player_id)
    form = await request.form()
    fields, errors = validate_player(form, player_id=player_id)
    if errors:
        values = {k: form.get(k) for k in form.keys()}
        return _render_player_form(
            request, values, errors, "Edit player", f"/players/{player_id}/edit", 422
        )
    db.save_player(fields, player_id=player_id)
    return RedirectResponse(f"/players/{player_id}", status_code=303)


@app.post("/players/{player_id}/admin")
async def set_admin(request: Request, player_id: int):
    """Admins grant or remove admin rights. There must always be at least one admin."""
    viewer = request.state.player
    if viewer is None or not viewer["is_admin"]:
        raise HTTPException(status_code=403, detail="Only admins can change admin rights")
    target = db.get_player(player_id)
    if target is None:
        raise HTTPException(status_code=404, detail="Unknown player")
    form = await request.form()
    make_admin = form.get("make_admin") == "1"
    if not make_admin and viewer["id"] == player_id:
        raise HTTPException(status_code=422, detail="You can't remove your own admin rights")
    db.set_admin(player_id, make_admin)
    next_url = form.get("next") or f"/players/{player_id}"
    if not next_url.startswith("/") or next_url.startswith("//"):
        next_url = f"/players/{player_id}"
    return RedirectResponse(next_url, status_code=303)


@app.post("/players/{player_id}/delete")
def delete_player_route(request: Request, player_id: int):
    viewer = request.state.player
    if viewer is None or not viewer["is_admin"]:
        raise HTTPException(status_code=403, detail="Only admins can delete players")
    target = db.get_player(player_id)
    if target is None:
        raise HTTPException(status_code=404, detail="Unknown player")
    if target["is_admin"] and db.admin_count() <= 1:
        raise HTTPException(status_code=422, detail="There must always be at least one admin")
    db.delete_player(player_id)
    response = RedirectResponse("/players", status_code=303)
    if viewer["id"] == player_id:
        # Deleting your own account ends your session with it.
        response.delete_cookie(auth.COOKIE)
    return response


def _player_summary(player_id: int, matches: list[dict]) -> tuple[list, list, list]:
    """Overall numbers, per-game rows, and this player's matches (newest first)."""
    games = all_games()
    per_game: dict[str, list[int]] = {}
    history: list[dict] = []
    for m in matches:
        game = games.get(m["game"])
        mine = next((p for p in m["participants"] if p["player_id"] == player_id), None)
        if game is None or mine is None:
            continue
        counts = per_game.setdefault(game.key, [0, 0])
        counts[0] += 1
        counts[1] += int(mine["won"])
        history.append(_describe(game, m))

    def rate(games: int, wins: int) -> str:
        return f"{wins / games:.0%}" if games else "—"

    total = sum(c[0] for c in per_game.values())
    wins = sum(c[1] for c in per_game.values())
    summary = [
        ("Matches played", str(total)),
        ("Wins", str(wins)),
        ("Win rate", rate(total, wins)),
    ]
    game_rows = [
        [games[key].name, count, won, rate(count, won)]
        for key, (count, won) in sorted(per_game.items(), key=lambda kv: -kv[1][0])
    ]
    return summary, game_rows, history


@app.get("/players/{player_id}", response_class=HTMLResponse)
def player_page(request: Request, player_id: int, location: str | None = None):
    player = db.get_player(player_id)
    if player is None:
        raise HTTPException(status_code=404, detail="Unknown player")

    viewer = request.state.player
    is_owner = viewer is not None and viewer["id"] == player_id
    is_admin = viewer is not None and viewer["is_admin"]
    can_see_private = is_owner or is_admin
    # Contact details stay hidden from visitors unless the player has chosen to show them.
    # The player and admins always see them.
    contact = {
        "email": player["email"] if can_see_private or player["show_email"] else None,
        "phone": (player["phone"] if can_see_private or player["show_phone"] else None),
    }
    location_id = _location_arg(location)
    matches = stats.scope(load_matches(), location_id)
    summary, game_rows, history = _player_summary(player_id, matches)
    return templates.TemplateResponse(
        request,
        "player.html",
        {
            "player": player,
            "is_owner": is_owner,
            "can_edit": can_see_private,
            "contact": contact,
            "notes": player["notes"] if can_see_private else None,
            "summary": summary,
            "game_rows": game_rows,
            "history": history,
            "locations": db.list_locations(),
            "location_id": location_id,
        },
    )


@app.post("/players/{player_id}/login-link")
def create_login_link(player_id: int):
    player = db.get_player(player_id)
    if player is None:
        raise HTTPException(status_code=404, detail="Unknown player")
    if player["has_login"]:
        return RedirectResponse(f"/players/{player_id}", status_code=303)
    token = secrets.token_urlsafe(24)
    db.set_login_token(player_id, token)
    return RedirectResponse(f"/confirm/{token}", status_code=303)


# ---- login ---------------------------------------------------------------

@app.get("/confirm/{token}", response_class=HTMLResponse)
def confirm_form(request: Request, token: str):
    player_id = db.player_id_for_token(token)
    if player_id is None:
        raise HTTPException(status_code=404, detail="This link has expired or was already used.")
    return templates.TemplateResponse(
        request,
        "confirm.html",
        {"player": db.get_player(player_id), "token": token, "errors": []},
    )


@app.post("/confirm/{token}")
async def confirm_submit(request: Request, token: str):
    player_id = db.player_id_for_token(token)
    if player_id is None:
        raise HTTPException(status_code=404, detail="This link has expired or was already used.")
    form = await request.form()
    password = form.get("password") or ""
    errors: list[str] = []
    if len(password) < auth.MIN_PASSWORD:
        errors.append(f"Password must be at least {auth.MIN_PASSWORD} characters.")
    if password != (form.get("confirm") or ""):
        errors.append("Passwords don't match.")
    if errors:
        return templates.TemplateResponse(
            request,
            "confirm.html",
            {"player": db.get_player(player_id), "token": token, "errors": errors},
            status_code=422,
        )
    db.set_password(player_id, auth.hash_password(password))
    return _session_cookie(RedirectResponse(f"/players/{player_id}", status_code=303), player_id)


@app.get("/login", response_class=HTMLResponse)
def login_form(request: Request):
    return templates.TemplateResponse(request, "login.html", {"errors": [], "email": ""})


@app.post("/login")
async def login_submit(request: Request):
    form = await request.form()
    email = (form.get("email") or "").strip()
    login = db.get_login(db.norm(email)) if email else None
    if login is None or login["password_hash"] is None or not auth.verify_password(
        form.get("password") or "", login["password_hash"]
    ):
        return templates.TemplateResponse(
            request,
            "login.html",
            {"errors": ["Email or password is wrong."], "email": email},
            status_code=401,
        )
    return _session_cookie(RedirectResponse(f"/players/{login['id']}", status_code=303), login["id"])


@app.post("/logout")
def logout():
    response = RedirectResponse("/", status_code=303)
    response.delete_cookie(auth.COOKIE)
    return response


# ---- admin: BoardGameGeek key ---------------------------------------------

@app.get("/admin", response_class=HTMLResponse)
def admin(request: Request, saved: bool = False):
    _require_admin(request)
    if get_setting("bgg_api_token"):
        token_source = "database"
    elif os.environ.get("BGG_API_TOKEN"):
        token_source = "environment"
    else:
        token_source = None
    return templates.TemplateResponse(
        request, "admin.html", {"token_source": token_source, "saved": saved}
    )


@app.post("/admin/bgg-token")
async def set_bgg_token(request: Request):
    _require_admin(request)
    form = await request.form()
    token = (form.get("token") or "").strip()
    if token:
        set_setting("bgg_api_token", token)
    return RedirectResponse("/admin?saved=true", status_code=303)
