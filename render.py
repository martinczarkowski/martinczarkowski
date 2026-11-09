"""Render the light and dark profile SVGs.

The layout follows Andrew6rant's profile README: one 985x530 card, ASCII portrait on the
left, neofetch-style key/value lines on the right, every line padded with dots to the same
width so the values stay right-aligned.

Nothing may overflow the card. Configured text that does not fit raises LayoutError;
statistics that would not fit fall back to compact notation (987.7M) for that line only.
"""
from __future__ import annotations

from datetime import date, datetime
from xml.sax.saxutils import escape

WIDTH = 60          # characters per info line
STATS_SPLIT = 35    # column of the " | " separator in the two-column stats lines
MAX_PORTRAIT_COLUMNS = 38
SVG_WIDTH = 985
MIN_ROWS = 25
ASCII_X, INFO_X = 15, 390
TOP, LINE_HEIGHT = 30, 20

THEMES = {
    "light": {"background": "#f6f8fa", "text": "#24292f", "key": "#953800", "value": "#0a3069",
              "add": "#1a7f37", "del": "#cf222e", "dots": "#c2cfde"},
    "dark": {"background": "#161b22", "text": "#c9d1d9", "key": "#ffa657", "value": "#a5d6ff",
             "add": "#3fb950", "del": "#f85149", "dots": "#616e7f"},
}

Segment = tuple[str, str | None]   # (text, css class or None for plain text)


class LayoutError(ValueError):
    """Configured content that would not fit the card."""


def account_age(created: date, today: date) -> str:
    """'6 years, 10 months, 27 days' between two dates (calendar months, like dateutil)."""
    years = today.year - created.year
    months = today.month - created.month
    days = today.day - created.day
    if days < 0:
        months -= 1
        days += (today.replace(day=1) - date.resolution).day
    if months < 0:
        years -= 1
        months += 12
    return ", ".join(f"{n} {unit}{'' if n == 1 else 's'}" for n, unit in
                     ((years, "year"), (months, "month"), (days, "day")))


def fmt(value, compact: bool = False) -> str:
    """1,234,567, or 1.2M in compact notation; n/a for a missing value."""
    if not isinstance(value, int):
        return "n/a"
    if not compact or abs(value) < 1000:
        return f"{value:,}"
    for divisor, suffix in ((1e9, "B"), (1e6, "M"), (1e3, "K")):
        if abs(value) >= divisor:
            return f"{value / divisor:.1f}".rstrip("0").rstrip(".") + suffix
    return str(value)


def dots(count: int) -> Segment:
    return (" " + "." * count + " " if count > 0 else " ", "cc")


def key_segments(key: str) -> list[Segment]:
    segments: list[Segment] = []
    for index, part in enumerate(key.split(".")):
        if index:
            segments.append((".", None))
        segments.append((part, "key"))
    return segments


def length(segments: list[Segment]) -> int:
    return sum(len(text) for text, _ in segments)


def checked(line: list[Segment], what: str) -> list[Segment]:
    if length(line) > WIDTH:
        raise LayoutError(f"{what} is {length(line)} characters wide, the card fits {WIDTH}: {text(line)!r}")
    return line


def text(line: list[Segment]) -> str:
    return "".join(part for part, _ in line)


def row(key: str, value: str) -> list[Segment]:
    head = [(". ", "cc"), *key_segments(key), (":", None)]
    return checked([*head, dots(WIDTH - length(head) - len(value) - 2), (value, "value")], f"row {key!r}")


def header(title: str) -> list[Segment]:
    return checked([(f"{title} -{'—' * (WIDTH - len(title) - 5)}-—-", None)], f"header {title!r}")


def stats_lines(stats: dict) -> list[list[Segment]]:
    """The three statistics lines; a line too long with full numbers is redone in compact notation."""
    lines = []
    for full, compact in zip(_stats_lines(stats, False), _stats_lines(stats, True)):
        lines.append(full if length(full) <= WIDTH else checked(compact, "statistics line"))
    return lines


def _stats_lines(stats: dict, compact: bool) -> list[list[Segment]]:
    def num(key):
        return fmt(stats.get(key), compact)

    repos, contributed, stars, commits, followers = map(num, ("repos", "contributed", "stars", "commits", "followers"))

    def two_columns(left_key, left_value, left_tail, right_key, right_value):
        left_head = [(". ", "cc"), *key_segments(left_key), (":", None)]
        left = [*left_head, dots(STATS_SPLIT - length(left_head) - len(left_value) - length(left_tail) - 2),
                (left_value, "value"), *left_tail, (" | ", None)]
        right_head = [*key_segments(right_key), (":", None)]
        return [*left, *right_head,
                dots(WIDTH - length(left) - length(right_head) - len(right_value) - 2), (right_value, "value")]

    contributed_tail = [(" {", None), ("Contributed", "key"), (": ", None), (contributed, "value"), ("}", None)]
    lines = [two_columns("Repos", repos, contributed_tail, "Stars", stars),
             two_columns("Commits", commits, [], "Followers", followers)]

    additions, deletions = stats.get("additions"), stats.get("deletions")
    loc_head = [(". ", "cc"), ("Net Lines Committed", "key"), (":", None)]
    if isinstance(additions, int) and isinstance(deletions, int):
        loc_tail = [(fmt(additions - deletions, compact), "value"), (" ( ", None),
                    (fmt(additions, compact) + "++", "addColor"), (", ", None),
                    (fmt(deletions, compact) + "--", "delColor"), (" )", None)]
    else:
        loc_tail = [("n/a", "value")]
    lines.append([*loc_head, dots(WIDTH - length(loc_head) - length(loc_tail) - 2), *loc_tail])
    return lines


def info_lines(profile: dict, stats: dict, today: date) -> list[list[Segment] | None]:
    """All lines of the right column; None is an empty line."""
    created = stats.get("created_at")
    age = account_age(datetime.fromisoformat(created).date(), today) if created else "n/a"

    lines: list[list[Segment] | None] = [header(profile["header"])]
    for index, group in enumerate(profile["info"]):
        if index:
            lines.append([(". ", "cc")])
        lines += [row(key, value.replace("{account_age}", age)) for key, value in group]
    lines += [None, header("- Contact"), *(row(key, value) for key, value in profile["contact"])]
    lines += [None, header("- GitHub Stats"), *stats_lines(stats)]
    return lines


def tspan(text: str, css: str | None, position: str = "") -> str:
    attrs = position + (f' class="{css}"' if css else "")
    return f"<tspan{attrs}>{escape(text)}</tspan>" if attrs else escape(text)


def render_svg(theme: str, portrait: list[str], lines: list[list[Segment] | None]) -> str:
    widest = max(map(len, portrait), default=0)
    if widest > MAX_PORTRAIT_COLUMNS:
        raise LayoutError(f"portrait is {widest} characters wide, the card fits {MAX_PORTRAIT_COLUMNS}")
    colors = THEMES[theme]
    rows = max(MIN_ROWS, len(portrait), len(lines))
    height = TOP + (rows - 1) * LINE_HEIGHT + LINE_HEIGHT

    ascii_spans = [tspan(text, None, f' x="{ASCII_X}" y="{TOP + i * LINE_HEIGHT}"') if text else
                   f'<tspan x="{ASCII_X}" y="{TOP + i * LINE_HEIGHT}"></tspan>' for i, text in enumerate(portrait)]
    info_spans = []
    for i, segments in enumerate(lines):
        if segments is None:
            continue
        (first_text, first_css), rest = segments[0], segments[1:]
        position = f' x="{INFO_X}" y="{TOP + i * LINE_HEIGHT}"'
        info_spans.append(tspan(first_text, first_css, position) + "".join(tspan(t, c) for t, c in rest))

    return "\n".join([
        "<?xml version='1.0' encoding='UTF-8'?>",
        f'<svg xmlns="http://www.w3.org/2000/svg" font-family="ConsolasFallback,Consolas,monospace" '
        f'width="{SVG_WIDTH}px" height="{height}px" font-size="16px">',
        "<style>",
        "@font-face {",
        "src: local('Consolas');",
        "font-family: 'ConsolasFallback';",
        "font-display: swap;",
        "size-adjust: 109%;",
        "}",
        f".key {{fill: {colors['key']};}}",
        f".value {{fill: {colors['value']};}}",
        f".addColor {{fill: {colors['add']};}}",
        f".delColor {{fill: {colors['del']};}}",
        f".cc {{fill: {colors['dots']};}}",
        "text, tspan {white-space: pre;}",
        "</style>",
        f'<rect width="{SVG_WIDTH}px" height="{height}px" fill="{colors["background"]}" rx="15"/>',
        f'<text x="{ASCII_X}" y="{TOP}" fill="{colors["text"]}" class="ascii">',
        *ascii_spans,
        "</text>",
        f'<text x="{INFO_X}" y="{TOP}" fill="{colors["text"]}">',
        *info_spans,
        "</text>",
        "</svg>",
        "",
    ])


def render_all(profile: dict, portrait: list[str], stats: dict, today: date) -> dict[str, str]:
    lines = info_lines(profile, stats, today)
    return {theme: render_svg(theme, portrait, lines) for theme in THEMES}
