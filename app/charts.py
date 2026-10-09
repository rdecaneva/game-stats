"""Server-rendered SVG charts. No JavaScript; colours come from CSS variables so dark mode works.

Marks follow the dataviz spec: 4px rounded data ends, a 2px surface gap between touching marks,
hairline gridlines, labels only where they add something, and a table twin on the page.
"""

import math

from markupsafe import escape

from .stats import format_duration

OTHER = "var(--series-other)"
MAX_SLOTS = 8


def _series(slot: int) -> str:
    return f"var(--series-{slot + 1})"


def _bar_path(x: float, top: float, width: float, base: float) -> str:
    """A column with rounded top corners (4px) and a square base."""
    r = min(4, width / 2, (base - top) / 2)
    return (
        f"M{x:.1f},{base:.1f} V{top + r:.1f} Q{x:.1f},{top:.1f} {x + r:.1f},{top:.1f} "
        f"H{x + width - r:.1f} Q{x + width:.1f},{top:.1f} {x + width:.1f},{top + r:.1f} "
        f"V{base:.1f} Z"
    )


def _nice_max(value: float) -> float:
    if value <= 0:
        return 1
    magnitude = 10 ** math.floor(math.log10(value))
    for step in (1, 2, 2.5, 5, 10):
        if step * magnitude >= value:
            return step * magnitude
    return 10 * magnitude


def duration_chart(points: list[tuple[str, int]]) -> str:
    """Columns of play length (minutes), oldest first. points: [(date, seconds)]."""
    if not points:
        return '<p class="muted">No timed plays yet.</p>'
    width, height, left, top, bottom = 360, 200, 36, 18, 26
    plot_h = height - top - bottom
    minutes = [round(s / 60, 1) for _, s in points]
    y_max = _nice_max(max(minutes))
    slot = (width - left) / len(points)
    bar_w = min(24, slot * 0.7)
    longest = minutes.index(max(minutes))

    parts = [f'<svg viewBox="0 0 {width} {height}" class="chart" role="img" '
             f'aria-label="Play length in minutes, by play">']
    for frac in (0, 0.5, 1):
        y = top + plot_h * (1 - frac)
        value = y_max * frac
        cls = "baseline" if frac == 0 else "grid"
        parts.append(f'<line x1="{left}" x2="{width}" y1="{y:.1f}" y2="{y:.1f}" class="{cls}"/>')
        parts.append(f'<text x="{left - 6}" y="{y + 4:.1f}" class="tick" text-anchor="end">{value:g}</text>')
    for i, (minute, (date, seconds)) in enumerate(zip(minutes, points)):
        x = left + slot * i + (slot - bar_w) / 2
        h = plot_h * minute / y_max
        base = top + plot_h
        parts.append(
            f'<path d="{_bar_path(x, base - h, bar_w, base)}" fill="{_series(0)}">'
            f"<title>{escape(date)}: {format_duration(seconds)}</title></path>"
        )
        if i == longest:
            parts.append(
                f'<text x="{x + bar_w / 2:.1f}" y="{base - h - 4:.1f}" class="value" '
                f'text-anchor="middle">{format_duration(seconds)}</text>'
            )
    parts.append(f'<text x="{left}" y="{height - 6}" class="tick">{escape(points[0][0])}</text>')
    parts.append(
        f'<text x="{width}" y="{height - 6}" class="tick" text-anchor="end">{escape(points[-1][0])}</text>'
    )
    parts.append("</svg>")
    return "".join(parts)


def score_chart(rows: list[list]) -> str:
    """Horizontal bars of each player's average score, with a low-to-high range.

    rows: [name, plays, low, average, high] as produced by stats.score_rows.
    """
    if not rows:
        return '<p class="muted">No scores recorded yet.</p>'
    row_h, label_w, width = 34, 120, 360
    height = row_h * len(rows) + 8
    avg_max = _nice_max(max(float(r[3]) for r in rows) * 1.15)
    plot_w = width - label_w - 50
    parts = [f'<svg viewBox="0 0 {width} {height}" class="chart" role="img" '
             f'aria-label="Average score per player">']
    for i, (name, plays, low, avg, high) in enumerate(rows):
        y = 4 + i * row_h
        avg_f = float(avg)
        w = plot_w * avg_f / avg_max
        parts.append(
            f'<text x="0" y="{y + 19}" class="label">{escape(name)}</text>'
            f'<path d="{_bar_path(label_w, y + 6, max(w, 2), y + 24)}" fill="{_series(0)}">'
            f"<title>{escape(name)}: average {avg}, range {low}–{high} over {plays} plays</title></path>"
            f'<text x="{label_w + w + 6:.1f}" y="{y + 19}" class="value">{avg}</text>'
        )
    parts.append("</svg>")
    return "".join(parts)


def _pie_slice(cx, cy, r, start, end, colour) -> str:
    """One wedge. A 2px surface-coloured edge keeps touching wedges apart."""
    style = f'fill:{colour};stroke:var(--surface-1);stroke-width:2'
    if end - start >= 2 * math.pi - 1e-9:
        return f'<circle cx="{cx}" cy="{cy}" r="{r}" style="{style}"/>'
    x1, y1 = cx + r * math.sin(start), cy - r * math.cos(start)
    x2, y2 = cx + r * math.sin(end), cy - r * math.cos(end)
    large = 1 if end - start > math.pi else 0
    return (
        f'<path d="M{cx},{cy} L{x1:.2f},{y1:.2f} A{r},{r} 0 {large} 1 {x2:.2f},{y2:.2f} Z" '
        f'style="{style}"/>'
    )


def win_pie(wins: list[tuple[str, int]], slots: dict[str, int]) -> str:
    """Share of wins per player. Colours are fixed per player via `slots`; beyond 8, the rest is 'Other'.

    wins: [(name, wins)] most first. slots: player name -> colour index.
    """
    if not wins:
        return '<p class="muted">No wins recorded yet.</p>'
    shown = wins[:MAX_SLOTS]
    rest = sum(n for _, n in wins[MAX_SLOTS:])
    entries = [(name, n, _series(slots.get(name, 0))) for name, n in shown]
    if rest:
        entries.append(("Other", rest, OTHER))
    total = sum(n for _, n, _ in entries)
    size, cx, cy, r = 180, 90, 90, 84

    slices, legend = [], []
    angle = 0.0
    for name, n, colour in entries:
        span = 2 * math.pi * n / total
        slices.append(_pie_slice(cx, cy, r, angle, angle + span, colour))
        angle += span
    for name, n, colour in entries:
        legend.append(
            f'<li><span class="swatch" style="background:{colour}"></span>'
            f'<span class="legend-name">{escape(name)}</span>'
            f'<span class="legend-value">{n} · {n / total:.0%}</span></li>'
        )
    return (
        '<div class="pie">'
        f'<svg viewBox="0 0 {size} {size}" class="chart pie-svg" role="img" '
        f'aria-label="Wins by player">'
        f'<g class="pie-slices">{"".join(slices)}</g></svg>'
        f'<ul class="legend">{"".join(legend)}</ul>'
        "</div>"
    )
