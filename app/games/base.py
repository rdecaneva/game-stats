from collections import defaultdict
from dataclasses import dataclass

from .. import bgg


@dataclass(frozen=True)
class Field:
    """One input on the match form.

    kind: text | number | select | textarea
    """

    name: str
    label: str
    kind: str = "text"
    options: tuple[str, ...] = ()
    required: bool = False


@dataclass
class Table:
    title: str
    headers: list[str]
    rows: list[list]


def group_results(results: list[tuple[str | None, bool]]) -> list[list]:
    """Turn (key, won) pairs into rows of [key, games, wins, win rate].

    Rows with no key are skipped. Sorted by win rate, then games played.
    """
    counts: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    for key, won in results:
        if not key:
            continue
        counts[key][0] += 1
        counts[key][1] += int(won)

    ordered = sorted(
        counts.items(),
        key=lambda kv: (-kv[1][1] / kv[1][0], -kv[1][0], kv[0].lower()),
    )
    return [
        [key, games, wins, f"{wins / games:.0%}"]
        for key, (games, wins) in ordered
    ]


class Game:
    """Base class for a tracked game. Subclass, set the attributes, and register it in games/__init__.py."""

    key: str = ""
    name: str = ""
    # BoardGameGeek thing id, used for the thumbnail, release year and designer.
    bgg_id: int | None = None
    max_players: int = 4
    match_fields: tuple[Field, ...] = ()
    # Fields recorded once per player (e.g. deck, character, faction).
    player_fields: tuple[Field, ...] = ()
    # Page template for this game's stats page. Games with a custom page set their own.
    template: str = "game.html"

    def bgg_info(self) -> "bgg.BggInfo | None":
        return bgg.fetch(self.bgg_id) if self.bgg_id else None

    # ---- form handling -------------------------------------------------

    def parse_form(self, form) -> tuple[dict, list[dict], list[str]]:
        """Turn submitted form data into (match_details, participants, errors)."""
        errors: list[str] = []

        details: dict = {}
        for f in self.match_fields:
            value = _read(form.get(f.name), f, errors, prefix="")
            if value is not None:
                details[f.name] = value

        winner = form.get("winner", "")
        participants: list[dict] = []
        for i in range(self.max_players):
            name = (form.get(f"player_{i}_name") or "").strip()
            if not name:
                continue
            player_details: dict = {}
            for f in self.player_fields:
                value = _read(form.get(f"player_{i}_{f.name}"), f, errors, prefix=f"{name}: ")
                if value is not None:
                    player_details[f.name] = value
            participants.append(
                {"player": name, "won": str(i) == winner, "details": player_details}
            )

        if len(participants) < 2:
            errors.append("Enter at least two players.")
        return details, participants, errors

    # ---- stats ---------------------------------------------------------

    def summary(self, matches: list[dict]) -> list[tuple[str, str]]:
        """Headline numbers shown at the top of the game page."""
        return [("Matches played", str(len(matches)))]

    def stats(self, matches: list[dict]) -> list[Table]:
        """Tables shown on the game page. Override to add game-specific breakdowns."""
        player_rows = group_results(
            [(p["player"], p["won"]) for m in matches for p in m["participants"]]
        )
        return [Table("Players", ["Player", "Games", "Wins", "Win rate"], player_rows)]

    def suggestions(self, matches: list[dict]) -> dict[str, list[str]]:
        """Previously used values, keyed by field name, for autocomplete on the form."""
        result: dict[str, set[str]] = {"player": set()}
        for f in self.player_fields:
            result[f.name] = set()
        for m in matches:
            for p in m["participants"]:
                result["player"].add(p["player"])
                for f in self.player_fields:
                    value = p["details"].get(f.name)
                    if value:
                        result[f.name].add(str(value))
        return {k: sorted(v, key=str.lower) for k, v in result.items()}


def _read(raw, field: Field, errors: list[str], prefix: str):
    """Coerce one raw form value to the field's type. Returns None when empty."""
    value = (raw or "").strip() if isinstance(raw, str) else raw
    if value in (None, ""):
        if field.required:
            errors.append(f"{prefix}{field.label} is required.")
        return None
    if field.kind == "number":
        try:
            return int(value)
        except ValueError:
            errors.append(f"{prefix}{field.label} must be a whole number.")
            return None
    if field.kind == "select" and value not in field.options:
        errors.append(f"{prefix}{field.label} must be one of the listed options.")
        return None
    return value
