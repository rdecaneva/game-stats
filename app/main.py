from contextlib import asynccontextmanager
from datetime import date
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from . import bgg
from .db import delete_match, init_db, insert_match, load_matches
from .games import GAMES
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
    game = GAMES.get(key)
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
        "players": ", ".join(p["player"] for p in match["participants"]),
        "winner": ", ".join(winners) if winners else None,
        "details": match["details"],
    }


@app.get("/", response_class=HTMLResponse)
def home(request: Request):
    recent = [
        _describe(GAMES[m["game"]], m) for m in load_matches(limit=10) if m["game"] in GAMES
    ]
    return templates.TemplateResponse(
        request,
        "index.html",
        {"games": GAMES.values(), "recent": recent},
    )


@app.get("/games/{key}", response_class=HTMLResponse)
def game_page(request: Request, key: str):
    game = _get_game(key)
    matches = load_matches(game.key)
    return templates.TemplateResponse(
        request,
        "game.html",
        {
            "game": game,
            "bgg": bgg.fetch(game.bgg_id) if game.bgg_id else None,
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

    errors: list[str] = []
    played_on = (form.get("played_on") or "").strip() or date.today().isoformat()
    try:
        date.fromisoformat(played_on)
    except ValueError:
        errors.append("Date is not valid.")

    details, participants, parse_errors = game.parse_form(form)
    errors += parse_errors
    if errors:
        return _render_form(request, game, values, errors, status=422)

    insert_match(game.key, played_on, details, participants)
    return RedirectResponse(f"/games/{game.key}", status_code=303)


@app.post("/games/{key}/matches/{match_id}/delete")
def delete_match_route(key: str, match_id: int):
    _get_game(key)
    delete_match(key, match_id)
    return RedirectResponse(f"/games/{key}", status_code=303)
