from .base import Field, Game, Table, group_results

FORMATS = (
    "Commander",
    "Standard",
    "Pioneer",
    "Modern",
    "Legacy",
    "Pauper",
    "Limited",
    "Other",
)


class MagicTheGathering(Game):
    key = "mtg"
    name = "Magic: The Gathering"
    max_players = 6
    match_fields = (
        Field("format", "Format", "select", FORMATS, required=True),
        Field("turns", "Turns", "number"),
        Field("notes", "Notes", "textarea"),
    )
    player_fields = (Field("deck", "Deck"),)

    def summary(self, matches):
        turns = [m["details"]["turns"] for m in matches if "turns" in m["details"]]
        avg_turns = f"{sum(turns) / len(turns):.1f}" if turns else "—"
        return [
            ("Matches played", str(len(matches))),
            ("Average turns", avg_turns),
        ]

    def stats(self, matches):
        player_results = [(p["player"], p["won"]) for m in matches for p in m["participants"]]
        deck_results = [
            (p["details"].get("deck"), p["won"]) for m in matches for p in m["participants"]
        ]
        format_results = [
            (m["details"].get("format"), p["won"]) for m in matches for p in m["participants"]
        ]
        headers = ["Games", "Wins", "Win rate"]
        return [
            Table("Players", ["Player", *headers], group_results(player_results)),
            Table("Decks", ["Deck", *headers], group_results(deck_results)),
            Table("By format", ["Format", *headers], group_results(format_results)),
        ]
