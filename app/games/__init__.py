from .base import Game
from .mtg import MagicTheGathering

# Register every tracked game here. The key is used in URLs and stored with each match.
GAMES: dict[str, Game] = {g.key: g for g in (MagicTheGathering(),)}
