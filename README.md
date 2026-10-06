# Game Stats

A small, mobile-friendly web app for tracking results of board and card games. Each game is a
small Python class, so new games can be added without touching the rest of the app.

Currently supported: **Magic: The Gathering** (1v1 and multiplayer, with decks, formats, and turn counts).

## Stack

- FastAPI + Jinja2 server-rendered pages (no JavaScript build step)
- SQLite (a single file in `data/`)
- Plain CSS, mobile-first, with light and dark themes
- Docker / docker compose

## Run with Docker

```bash
docker compose up -d --build
```

Then open http://localhost:8080 (or http://<your-host>:8080 from a phone on the same network). To use a different host port, set `PORT=9000 docker compose up -d`.

Data is stored in `./data/stats.db` on the host, so it survives rebuilds.

The container runs as uid 1000. If your user id differs, build with yours so it can write to `./data`:

```bash
docker compose build --build-arg UID=$(id -u)
docker compose up -d
```

## Run locally (without Docker)

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/uvicorn app.main:app --reload
```

## Tests

```bash
.venv/bin/pytest
```

## Project layout

```
app/
  main.py            FastAPI routes
  db.py              SQLite schema and queries
  games/
    base.py          Game base class: form fields, parsing, stats
    mtg.py           Magic: The Gathering definition
    __init__.py      Registry of games
  templates/         Jinja2 pages (mobile-first)
  static/style.css   Styles
tests/
```

## Adding a new game

1. Create `app/games/<name>.py` with a subclass of `Game`:
   - `key` (used in URLs, e.g. `"chess"`) and `name` (display name)
   - `max_players` (number of player rows on the form)
   - `match_fields`: per-match inputs (`Field(name, label, kind, options, required)`)
   - `player_fields`: per-player inputs (e.g. faction, character)
   - Optionally override `summary()` and `stats()` to add game-specific numbers and tables.
2. Register it in `app/games/__init__.py`.

Results are stored generically (a match row, participant rows, and JSON details), so no
database migration is needed for a new game.

## Roadmap

- Edit existing matches
- Per-player filters and head-to-head records
- Authentication if the app is exposed beyond your network
- iOS app that talks to the same JSON API (a `/api` layer is the next step)
