from ..db import load_added_games
from .base import Game
from .generic import GenericGame
from .mtg import MagicTheGathering
from .ti4 import TwilightImperium4

# Games defined in code. The key is used in URLs and stored with each match.
# Games added from BoardGameGeek search live in the database and are merged in by all_games().
CODE_GAMES: dict[str, Game] = {g.key: g for g in (MagicTheGathering(), TwilightImperium4())}


def all_games() -> dict[str, Game]:
    games: dict[str, Game] = {
        key: g for key, g in CODE_GAMES.items()
    }
    for row in load_added_games():
        games.setdefault(row["key"], GenericGame(row))
    return dict(sorted(games.items(), key=lambda kv: kv[1].name.lower()))


def get_game(key: str) -> Game | None:
    if key in CODE_GAMES:
        return CODE_GAMES[key]
    for row in load_added_games():
        if row["key"] == key:
            return GenericGame(row)
    return None
