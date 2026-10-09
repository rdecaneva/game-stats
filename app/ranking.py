def assign_ranks(participants: list[dict]) -> None:
    """Set `rank` on each participant. 1 is best; tied players share a rank; non-players get None.

    Higher scores rank better. When every competing player has a score, ranks come from
    scores. Otherwise the winner is 1st and everyone else shares 2nd.
    """
    for p in participants:
        p["rank"] = None
    competing = [p for p in participants if p["is_player"]]
    if not competing:
        return

    if all(p.get("score") is not None for p in competing):
        rank, previous = 0, None
        for position, p in enumerate(sorted(competing, key=lambda p: -p["score"]), start=1):
            if p["score"] != previous:
                rank, previous = position, p["score"]
            p["rank"] = rank
    elif any(p["won"] for p in competing):
        for p in competing:
            p["rank"] = 1 if p["won"] else 2
