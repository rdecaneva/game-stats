from .. import bgg
from .base import Game


class GenericGame(Game):
    """A game added from BoardGameGeek search. Uses the default match form and stats."""

    def __init__(self, row: dict):
        self.key = row["key"]
        self.name = row["name"]
        self.bgg_id = row["bgg_id"]
        self._info = bgg.BggInfo(
            thumbnail=row["thumbnail"],
            year=row["year"],
            designers=tuple(row["designers"]),
        )

    def bgg_info(self) -> bgg.BggInfo:
        # Stored when the game was added, so details show even without a BGG token.
        return self._info
