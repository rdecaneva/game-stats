import os
from contextlib import asynccontextmanager
from datetime import date
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from . import bgg
from .db import (
    delete_match,
    get_setting,
    init_db,
    insert_match,
    load_matches,
    recently_played_games,
    save_added_game,
    set_setting,
)
from .games import all_games, get_game
from .games.base import Game

BASE_DIR = Path(__file__).parent


@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_db()
    yield


app = FastAPI(title="Game Stats", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
templates = Jinja2Templates(directory=BASE_DIR / "templates")


def _get_game(key: str) -> Game:
    game = get_game(key)
    if game is None:
        raise HTTPException(status_code=404, detail="Unknown game")
    return game


def _describe(game: Game, match: dict) -> dict:
    """Flatten a match into what the match list needs."""
    winners = [p["player"] for p in match["participants"] if p["won"]]
    return {
        "id": match["id"],
        "game_key": game.key,
        "game_name": game.name,
        "played_on": match["played_on"],
        "played_at": match["played_at"],
        "players": ", ".join(p["player"] for p in match["participants"]),
        "winner": ", ".join(winners) if winners else None,
        "details": match["details"],
    }


@app.get("/", response_class=HTMLResponse)
def home(request: Request):
    games = all_games()
    recent = [
        _describe(games[m["game"]], m) for m in load_matches(limit=10) if m["game"] in games
    ]
    return templates.TemplateResponse(
        request,
        "index.html",
        {"games": games.values(), "recent": recent},
    )


@app.get("/games", response_class=HTMLResponse)
def games_index(request: Request, q: str = ""):
    q = q.strip()
    games = all_games()
    if q:
        matches = [g for g in games.values() if q.lower() in g.name.lower()]
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
def game_page(request: Request, key: str):
    game = _get_game(key)
    matches = load_matches(game.key)
    return templates.TemplateResponse(
        request,
        game.template,
        {
            "game": game,
            "bgg": game.bgg_info(),
            "summary": game.summary(matches),
            "tables": game.stats(matches),
            "matches": [_describe(game, m) for m in matches],
        },
    )


def _render_form(request: Request, game: Game, values: dict, errors: list[str], status: int = 200):
    matches = load_matches(game.key)
    return templates.TemplateResponse(
        request,
        "new_match.html",
        {
            "game": game,
            "values": values,
            "errors": errors,
            "today": date.today().isoformat(),
            "suggestions": game.suggestions(matches),
        },
        status_code=status,
    )


@app.get("/games/{key}/new", response_class=HTMLResponse)
def new_match_form(request: Request, key: str):
    return _render_form(request, _get_game(key), {}, [])


@app.post("/games/{key}/new")
async def create_match(request: Request, key: str):
    game = _get_game(key)
    form = await request.form()
    values = {k: form.get(k) for k in form.keys()}
    values["winner"] = form.getlist("winner")

    errors: list[str] = []
    played_on = (form.get("played_on") or "").strip() or date.today().isoformat()
    try:
        date.fromisoformat(played_on)
    except ValueError:
        errors.append("Date is not valid.")
    played_at = (form.get("played_at") or "").strip() or None

    details, participants, parse_errors = game.parse_form(form)
    errors += parse_errors
    if errors:
        return _render_form(request, game, values, errors, status=422)

    insert_match(game.key, played_on, played_at, details, participants)
    return RedirectResponse(f"/games/{game.key}", status_code=303)


@app.post("/games/{key}/matches/{match_id}/delete")
def delete_match_route(key: str, match_id: int):
    _get_game(key)
    delete_match(key, match_id)
    return RedirectResponse(f"/games/{key}", status_code=303)


@app.get("/admin", response_class=HTMLResponse)
def admin(request: Request, saved: bool = False):
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
    form = await request.form()
    token = (form.get("token") or "").strip()
    if token:
        set_setting("bgg_api_token", token)
    return RedirectResponse("/admin?saved=true", status_code=303)
