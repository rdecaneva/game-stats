from .base import Field, Game, Table, group_results

EDITIONS = (
    "Base game",
    "Prophecy of Kings",
    "Other",
)


class TwilightImperium4(Game):
    key = "ti4"
    name = "Twilight Imperium (4th Edition)"
    bgg_id = 233078
    max_players = 8
    template = "games/ti4.html"
    match_fields = (
        Field("edition", "Edition", "select", EDITIONS, required=True),
        Field("notes", "Notes", "textarea"),
    )
    player_fields = (Field("faction", "Faction"),)

    def summary(self, matches):
        return [
            ("Matches played", str(len(matches))),
            ("Factions used", str(len({p["details"].get("faction") for m in matches
                                       for p in m["participants"]} - {None}))),
        ]

    def stats(self, matches):
        player_results = [(p["player"], p["won"]) for m in matches for p in m["participants"]]
        faction_results = [
            (p["details"].get("faction"), p["won"]) for m in matches for p in m["participants"]
        ]
        edition_results = [
            (m["details"].get("edition"), p["won"]) for m in matches for p in m["participants"]
        ]
        return [
            Table("Players", ["Player", "Games", "Wins", "Win rate"], group_results(player_results)),
            Table("Factions", ["Faction", "Games", "Wins", "Win rate"], group_results(faction_results)),
            Table("By edition", ["Edition", "Games", "Wins", "Win rate"], group_results(edition_results)),
        ]
