"""Numbers shared by the game, player and play pages. Pure functions over load_matches() output."""

from .db import norm


def scope(matches: list[dict], location_id: int | None = None) -> list[dict]:
    """Narrow matches for stats: only competing players count, and optionally one location only."""
    scoped = []
    for m in matches:
        if location_id is not None and m["location_id"] != location_id:
            continue
        scoped.append({**m, "participants": [p for p in m["participants"] if p["is_player"]]})
    return scoped


def format_duration(seconds: int) -> str:
    minutes = round(seconds / 60)
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h {minutes:02d}m" if hours else f"{minutes}m"


def duration_summary(matches: list[dict]) -> list[tuple[str, str]]:
    times = [m["duration_seconds"] for m in matches if m["duration_seconds"] is not None]
    if not times:
        return [("Total play time", "—"), ("Average play", "—"), ("Longest play", "—")]
    return [
        ("Total play time", format_duration(sum(times))),
        ("Average play", format_duration(sum(times) / len(times))),
        ("Longest play", format_duration(max(times))),
    ]


def score_rows(matches: list[dict]) -> list[list]:
    """Per player: [name, scored plays, low, average, high], highest average first."""
    scores: dict[str, list[int]] = {}
    for m in matches:
        for p in m["participants"]:
            if p["score"] is not None:
                scores.setdefault(p["player"], []).append(p["score"])
    rows = [
        [name, len(s), min(s), f"{sum(s) / len(s):.1f}", max(s)]
        for name, s in scores.items()
    ]
    return sorted(rows, key=lambda r: -float(r[3]))


def win_counts(matches: list[dict]) -> list[tuple[str, int]]:
    """Wins per player, most first. Players with no wins are left out."""
    wins: dict[str, int] = {}
    for m in matches:
        for p in m["participants"]:
            if p["won"]:
                wins[p["player"]] = wins.get(p["player"], 0) + 1
    return sorted(wins.items(), key=lambda kv: (-kv[1], norm(kv[0])))


def colour_slots(all_matches: list[dict]) -> dict[str, int]:
    """Fixed colour slot per player for a game: alphabetical over the full history.

    Computed from unfiltered matches so a player keeps their colour when a filter hides others.
    """
    names = {p["player"] for m in all_matches for p in m["participants"] if p["is_player"]}
    return {name: i for i, name in enumerate(sorted(names, key=norm))}
