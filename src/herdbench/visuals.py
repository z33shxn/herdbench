"""README visuals: animated, theme-aware SVG images drawn from the published results.

``herdbench visuals`` writes a light and a dark file for each image into ``docs/visuals``. Every number an image
shows is read from ``results/`` when the images are built, and the hero image is drawn from the bundled truth
panel, so the pictures cannot drift from the tables. Each file is plain SVG with CSS animation: no script, no
external font or image, a ``prefers-reduced-motion`` rule that shows the final frame at once, and no underscore
or long dash anywhere in the file (the tests check all of this).

Design system: the system sans, ink and surface tokens and the status palette of the data-visualisation rules
used across the project; long and short positions use a blue and red diverging scale with a grey midpoint,
stepped separately for the light and the dark surface.
"""

from __future__ import annotations

import json
import math
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from xml.sax.saxutils import escape

import numpy as np
import pandas as pd

FONT = 'system-ui,-apple-system,"Segoe UI",Roboto,"Helvetica Neue",Arial,sans-serif'

#: Images written by :func:`build`, in the order the README shows them.
NAMES = ("hero", "how", "scorecard", "insights", "timeline")

# --------------------------------------------------------------------------------------------------------------
# Text measurement: Helvetica metrics (units per 1000 em) stand in for the viewer's system sans, with a margin.
# --------------------------------------------------------------------------------------------------------------

_CHARS = " !\"#$%&'()*+,-./0123456789:;<=>?@ABCDEFGHIJKLMNOPQRSTUVWXYZ[\\]^_`abcdefghijklmnopqrstuvwxyz{|}~"
_REGULAR = (
    [278, 278, 355, 556, 556, 889, 667, 191, 333, 333, 389, 584, 278, 333, 278, 278]
    + [556] * 10
    + [278, 278, 584, 584, 584, 556, 1015]
    + [667, 667, 722, 722, 667, 611, 778, 722, 278, 500, 667, 556, 833]
    + [722, 778, 667, 778, 722, 667, 611, 722, 667, 944, 667, 667, 611]
    + [278, 278, 278, 469, 556, 333]
    + [556, 556, 500, 556, 556, 278, 556, 556, 222, 222, 500, 222, 833]
    + [556, 556, 556, 556, 333, 500, 278, 556, 500, 722, 500, 500, 500]
    + [334, 260, 334, 584]
)
_BOLD = (
    [278, 333, 474, 556, 556, 889, 722, 238, 333, 333, 389, 584, 278, 333, 278, 278]
    + [556] * 10
    + [333, 333, 584, 584, 584, 611, 975]
    + [722, 722, 722, 722, 667, 611, 778, 722, 278, 556, 722, 611, 833]
    + [722, 778, 667, 778, 722, 667, 611, 722, 667, 944, 667, 667, 611]
    + [333, 278, 333, 584, 556, 333]
    + [556, 611, 556, 611, 556, 333, 611, 611, 278, 278, 556, 278, 889]
    + [611, 611, 611, 611, 389, 556, 333, 611, 556, 778, 556, 556, 500]
    + [389, 280, 389, 584]
)
_WIDTHS = {False: dict(zip(_CHARS, _REGULAR, strict=True)), True: dict(zip(_CHARS, _BOLD, strict=True))}
#: A no-break space binds the last two words of a line so that no word is left alone on the last line.
NBSP = chr(0xA0)
_OTHER = {"·": 278, "±": 584, "’": 222, "×": 584, "→": 1000, "←": 1000, NBSP: 278}


def text_width(s: str, size: float, bold: bool = False, spacing: float = 0.0) -> float:
    """Approximate rendered width of ``s`` in px, 6% wider than Helvetica to cover other system faces."""
    table = _WIDTHS[bold]
    units = sum(table.get(c, _OTHER.get(c, 600)) for c in s)
    return units / 1000 * size * 1.06 + spacing * max(len(s) - 1, 0)


def wrap(s: str, size: float, width: float, bold: bool = False) -> list[str]:
    """Greedy word wrap of ``s`` into lines no wider than ``width`` px."""
    lines: list[str] = []
    line = ""
    for word in s.split(" "):
        if not word:
            continue
        trial = f"{line} {word}".strip()
        if line and text_width(trial, size, bold) > width:
            lines.append(line)
            line = word
        else:
            line = trial
    if line:
        lines.append(line)
    return lines


def fit(s: str, size: float, width: float, bold: bool = False) -> float:
    """The largest font size up to ``size`` (in half-pixel steps) at which ``s`` fits in ``width`` px."""
    while size > 8 and text_width(s, size, bold) > width:
        size -= 0.5
    return size


# --------------------------------------------------------------------------------------------------------------
# Colour: OKLab interpolation for the diverging ramps.
# --------------------------------------------------------------------------------------------------------------


def _lin(c: float) -> float:
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def _gam(c: float) -> float:
    return 12.92 * c if c <= 0.0031308 else 1.055 * c ** (1 / 2.4) - 0.055


def oklab(hex_color: str) -> tuple[float, float, float]:
    """OKLab coordinates of a ``#rrggbb`` colour."""
    r, g, b = (_lin(int(hex_color[i : i + 2], 16) / 255) for i in (1, 3, 5))
    lm = np.cbrt(
        [
            0.4122214708 * r + 0.5363325363 * g + 0.0514459929 * b,
            0.2119034982 * r + 0.6806995451 * g + 0.1073969566 * b,
            0.0883024619 * r + 0.2817188376 * g + 0.6299787005 * b,
        ]
    )
    l_, m_, s_ = (float(v) for v in lm)
    return (
        0.2104542553 * l_ + 0.7936177850 * m_ - 0.0040720468 * s_,
        1.9779984951 * l_ - 2.4285922050 * m_ + 0.4505937099 * s_,
        0.0259040371 * l_ + 0.7827717662 * m_ - 0.8086757660 * s_,
    )


def _linear_rgb(lab: Sequence[float]) -> tuple[float, float, float]:
    L, a, b = lab
    l_ = (L + 0.3963377774 * a + 0.2158037573 * b) ** 3
    m_ = (L - 0.1055613458 * a - 0.0638541728 * b) ** 3
    s_ = (L - 0.0894841775 * a - 1.2914855480 * b) ** 3
    return (
        4.0767416621 * l_ - 3.3077115913 * m_ + 0.2309699292 * s_,
        -1.2684380046 * l_ + 2.6097574011 * m_ - 0.3413193965 * s_,
        -0.0041960863 * l_ - 0.7034186147 * m_ + 1.7076147010 * s_,
    )


def from_oklab(lab: Sequence[float]) -> str:
    """``#rrggbb`` for OKLab coordinates, clipped into sRGB."""
    rgb = [min(max(_gam(min(max(c, 0.0), 1.0)), 0.0), 1.0) for c in _linear_rgb(lab)]
    return "#" + "".join(f"{round(c * 255):02x}" for c in rgb)


def with_lightness(hex_color: str, like: str) -> str:
    """``hex_color``'s hue at the OKLab lightness of ``like``, with the most chroma that stays in sRGB."""
    _, a, b = oklab(hex_color)
    target = oklab(like)[0]
    chroma, hue = math.hypot(a, b), math.atan2(b, a)
    while chroma > 0:
        lab = (target, chroma * math.cos(hue), chroma * math.sin(hue))
        if all(-1e-4 <= c <= 1 + 1e-4 for c in _linear_rgb(lab)):
            return from_oklab(lab)
        chroma -= 0.002
    return from_oklab((target, 0.0, 0.0))


def ramp(mid: str, pole: str, steps: Sequence[float] = (0.22, 0.42, 0.62, 0.81, 1.0)) -> tuple[str, ...]:
    """Colours from just off ``mid`` to ``pole``, evenly spaced in OKLab."""
    a, b = oklab(mid), oklab(pole)
    return tuple(from_oklab([a[i] + (b[i] - a[i]) * t for i in range(3)]) for t in steps)


@dataclass(frozen=True)
class Theme:
    name: str
    surface: str
    raised: str
    ink: str
    ink2: str
    muted: str
    grid: str
    axis: str
    border: str
    blue: str
    red: str
    good: str
    warning: str
    critical: str
    on_warning: str
    mid: str
    pos: tuple[str, ...]
    neg: tuple[str, ...]


def _theme(
    name: str,
    *,
    surface: str,
    raised: str,
    ink: str,
    ink2: str,
    grid: str,
    axis: str,
    border: str,
    blue: str,
    red: str,
    mid: str,
    pole: str,
) -> Theme:
    return Theme(
        name=name, surface=surface, raised=raised, ink=ink, ink2=ink2, muted="#898781", grid=grid, axis=axis,
        border=border, blue=blue, red=red, good="#0ca30c", warning="#fab219", critical="#d03b3b",
        on_warning="#0b0b0b", mid=mid, pos=ramp(mid, pole), neg=ramp(mid, with_lightness(red, pole)),
    )  # fmt: skip


LIGHT = _theme(
    "light", surface="#fcfcfb", raised="#f4f3ef", ink="#0b0b0b", ink2="#52514e", grid="#e1e0d9", axis="#c3c2b7",
    border="rgba(11,11,11,0.10)", blue="#2a78d6", red="#e34948", mid="#f0efec", pole="#1c5cab",
)  # fmt: skip
DARK = _theme(
    "dark", surface="#1a1a19", raised="#222221", ink="#f0efec", ink2="#c3c2b7", grid="#2c2c2a", axis="#383835",
    border="rgba(255,255,255,0.10)", blue="#3987e5", red="#e66767", mid="#383835", pole="#6da7ec",
)  # fmt: skip
THEMES = (LIGHT, DARK)

# --------------------------------------------------------------------------------------------------------------
# Facts: every number the images show, read from the results folder.
# --------------------------------------------------------------------------------------------------------------

#: Plain-language names for the seven confirmatory proxies: what the signal is, then how it is measured.
PLAIN = {
    "premium_24h": ("Price premium", "How far the futures price sat above spot, last 24 hours"),
    "momentum_7d": ("Price momentum", "The price change over the last 7 days"),
    "oi_flow_7d": ("Open interest flow", "7-day change in open positions, signed by the price move"),
    "bn_global_ls": ("Binance, all accounts", "Long/short ratio of all accounts"),
    "bn_top_account_ls": ("Binance, top traders' accounts", "Long/short ratio of top traders' accounts"),
    "bn_top_position_ls": ("Binance, top traders' positions", "Long/short ratio of top traders' positions"),
    "bn_taker_ls": ("Binance, taker flow", "Aggressive buying over aggressive selling"),
}

#: Plain-language verdicts, with the status tone and glyph that carry them next to the words.
VERDICTS = {
    "confirmed": ("Tracks the crowd", "good", "check"),
    "inconclusive": ("Too close to call", "warning", "question"),
    "uninformative": ("No useful link", "critical", "cross"),
    "reversed": ("Points the wrong way", "critical", "cross"),
}

#: When the study was fixed (docs/timestamps/README.md): the pre-registration's first Bitcoin anchor and the
#: start of the first proxy-versus-truth computation.
SEALED_BLOCK = 976_330
SEALED_AT = datetime(2026, 11, 17, 22, 7, tzinfo=timezone.utc)
SCORED_AT = datetime(2026, 11, 23, 14, 51, 57, tzinfo=timezone.utc)
#: The results commit (42f6fa8).
PUBLISHED_AT = datetime(2026, 11, 23, 15, 38, tzinfo=timezone.utc)

#: Scorecard title and the captions under the funding and liquidation figures.
TITLE = "Which signals really know what the crowd holds?"
CAPTION1 = "of hours stuck at the floor rate"
CAPTION3 = "correlation with next week's liquidations"


@dataclass(frozen=True)
class Facts:
    family: tuple[dict, ...]
    markets: tuple[str, ...]
    days: int
    market_days: int
    pinned_share: float
    funding_rho: float
    premium_rho: float
    heads_rho: float
    money_rho: float
    fragile_rho: float
    past_liq_rho: float

    @property
    def confirmed(self) -> int:
        return sum(r["verdict"] == "confirmed" for r in self.family)


def _row(df: pd.DataFrame, **match: str) -> pd.Series:
    mask = np.ones(len(df), dtype=bool)
    for k, v in match.items():
        mask &= (df[k] == v).to_numpy()
    rows = df[mask]
    if len(rows) != 1:
        raise ValueError(f"expected one row for {match}, found {len(rows)}")
    return rows.iloc[0]


def load_facts(results: Path) -> Facts:
    """Read every number the images show from a results folder."""
    summary = json.loads((results / "score_summary.json").read_text(encoding="utf-8"))
    family = pd.read_csv(results / "binance" / "primary_family.csv")
    directional = pd.read_csv(results / "directional_primary.csv")
    cross = pd.read_csv(results / "binance" / "cross_venue.csv")
    liq = pd.read_csv(results / "liquidation_target.csv")
    rows = tuple(
        {
            "proxy": str(r.proxy),
            "rho": float(r.rho),
            "ci_low": float(r.ci_low),
            "ci_high": float(r.ci_high),
            "verdict": str(r.verdict),
        }
        for r in family.sort_values("rho", ascending=False).itertuples()
    )
    funding = _row(directional, proxy="funding_24h", truth="ni", cohort="non_mm")
    premium = _row(family, proxy="premium_24h")
    return Facts(
        family=rows,
        markets=tuple(summary["primary_markets"]),
        days=int(summary["n_common_days"]),
        market_days=int(summary["primary_market_days"]),
        pinned_share=float(funding["pinned_share"]),
        funding_rho=float(funding["rho"]),
        premium_rho=float(premium["rho"]),
        heads_rho=float(_row(cross, proxy="bn_global_ls", truth="ai", cohort="all")["rho"]),
        money_rho=float(_row(family, proxy="bn_global_ls")["rho"]),
        fragile_rho=float(_row(liq, predictor="fragile_share", variant="registered")["rho"]),
        past_liq_rho=float(_row(liq, predictor="liq_intensity_7d", variant="registered")["rho"]),
    )


def tapestry_grid(panel: pd.DataFrame, markets: Sequence[str]) -> tuple[np.ndarray, list[str], list[date]]:
    """Net positioning ``ni`` of the crowd without market makers: markets (most net long first) by dates."""
    q = panel[(panel["cohort"] == "non_mm") & panel["market"].isin(list(markets))]
    wide = q.pivot(index="market", columns="date", values="ni").sort_index(axis=1)
    order = wide.mean(axis=1).sort_values(ascending=False, kind="mergesort").index
    wide = wide.loc[order]
    days = [pd.Timestamp(d).date() for d in wide.columns]
    return wide.to_numpy(dtype=float), [str(m) for m in wide.index], days


# --------------------------------------------------------------------------------------------------------------
# SVG scaffolding
# --------------------------------------------------------------------------------------------------------------

_BASE_CSS = (
    "text{font-family:" + FONT + "}"
    ".fu{animation:fu .9s cubic-bezier(.22,1,.36,1) both}"
    ".fi{animation:fi .9s ease-out both}"
    "@keyframes fu{from{opacity:0;transform:translateY(12px)}to{opacity:1;transform:none}}"
    "@keyframes fi{from{opacity:0}to{opacity:1}}"
    + "".join(f".d{i}{{animation-delay:{i * 0.12:.2f}s}}" for i in range(1, 25))
)
_REDUCED = "@media (prefers-reduced-motion:reduce){*{animation:none!important}}"


def _num(v: float, digits: int = 2) -> str:
    """A number with an ASCII hyphen-minus for negatives and no negative zero."""
    s = f"{v:.{digits}f}"
    return s[1:] if s.startswith("-") and float(s) == 0 else s


def _t(
    x: float,
    y: float,
    s: str,
    size: float,
    fill: str,
    *,
    weight: int = 400,
    anchor: str = "start",
    spacing: float = 0.0,
    cls: str = "",
    extra: str = "",
) -> str:
    attrs = [f'x="{x:g}"', f'y="{y:g}"', f'font-size="{size:g}"', f'fill="{fill}"']
    if weight != 400:
        attrs.append(f'font-weight="{weight}"')
    if anchor != "start":
        attrs.append(f'text-anchor="{anchor}"')
    if spacing:
        attrs.append(f'letter-spacing="{spacing:g}"')
    if cls:
        attrs.append(f'class="{cls}"')
    if extra:
        attrs.append(extra)
    return f"<text {' '.join(attrs)}>{escape(s)}</text>"  # fmt: skip


def _svg(w: int, h: int, title: str, desc: str, theme: Theme, css: str, body: str) -> str:
    card = (
        f'<rect x=".5" y=".5" width="{w - 1}" height="{h - 1}" rx="24" fill="{theme.surface}" stroke="{theme.border}"/>'
    )
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" viewBox="0 0 {w} {h}" role="img" '
        f'aria-labelledby="title desc">'
        f'<title id="title">{escape(title)}</title><desc id="desc">{escape(desc)}</desc>'
        f"<style>{_BASE_CSS}{css}{_REDUCED}</style>{card}{body}</svg>\n"
    )


def _glyph(kind: str, cx: float, cy: float, color: str, r: float = 10.0) -> str:
    """A filled status disc with a check, cross or question mark in white (near-black on warning yellow)."""
    s = r * 0.42
    if kind == "check":
        mark = (
            f'<path d="M{cx - s:.1f} {cy + 0.1:.1f}l{s * 0.75:.1f} {s * 0.8:.1f} {s * 1.3:.1f} {-s * 1.55:.1f}" '
            f'fill="none" stroke="#fff" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"/>'
        )
        fill = color
    elif kind == "cross":
        mark = (
            f'<path d="M{cx - s:.1f} {cy - s:.1f}l{2 * s:.1f} {2 * s:.1f}m0 {-2 * s:.1f}l{-2 * s:.1f} {2 * s:.1f}" '
            f'fill="none" stroke="#fff" stroke-width="2.2" stroke-linecap="round"/>'
        )
        fill = color
    else:
        mark = (
            f'<text x="{cx:g}" y="{cy + r * 0.42:.1f}" font-size="{r * 1.3:.1f}" font-weight="800" '
            f'text-anchor="middle" fill="#0b0b0b">?</text>'
        )
        fill = color
    return f'<circle cx="{cx:g}" cy="{cy:g}" r="{r:g}" fill="{fill}"/>{mark}'


def _mark(x: float, y: float, theme: Theme, cell: float = 12.0, gap: float = 3.0) -> str:
    """The HerdBench mark: a three-by-three patch of the positioning tapestry."""
    pattern = ("p3", "p2", "m", "p2", "n2", "p4", "m", "p3", "p1")
    colors = {"p1": theme.pos[0], "p2": theme.pos[1], "p3": theme.pos[2], "p4": theme.pos[4],
              "n2": theme.neg[3], "m": theme.mid}  # fmt: skip
    out = []
    for i, key in enumerate(pattern):
        cx, cy = x + (i % 3) * (cell + gap), y + (i // 3) * (cell + gap)
        out.append(f'<rect x="{cx:g}" y="{cy:g}" width="{cell:g}" height="{cell:g}" rx="3" fill="{colors[key]}"/>')
    return "".join(out)


# --------------------------------------------------------------------------------------------------------------
# 1. Hero: the tapestry of real crowd positioning, revealed by a scanning line
# --------------------------------------------------------------------------------------------------------------

#: Bin edges on |ni| for the diverging scale (five steps each side of a grey band around zero).
EDGES = (0.015, 0.05, 0.09, 0.13, 0.17)


def _level(v: float) -> int:
    k = sum(abs(v) > e for e in EDGES)
    return k if v > 0 else -k


def _levels(theme: Theme) -> dict[int, str]:
    """Colour of each level of the diverging scale: -5 (most short) to +5 (most long)."""
    return {0: theme.mid, **{k: theme.pos[k - 1] for k in range(1, 6)}, **{-k: theme.neg[k - 1] for k in range(1, 6)}}


def _cells(grid: np.ndarray, x0: float, y0: float, cw: float, pitch: float, ch: float, theme: Theme) -> str:
    """The tapestry: one path per colour level, each subpath a run of equal-level days in one market's row."""
    rows, cols = grid.shape
    paths: dict[int, list[str]] = {}
    for r in range(rows):
        y = y0 + r * pitch
        c = 0
        while c < cols:
            v = grid[r, c]
            if np.isnan(v):
                c += 1
                continue
            lvl, start = _level(float(v)), c
            while c < cols and not np.isnan(grid[r, c]) and _level(float(grid[r, c])) == lvl:
                c += 1
            x, run = x0 + start * cw, (c - start) * cw
            paths.setdefault(lvl, []).append(f"M{x:.1f} {y:g}h{run:.1f}v{ch:g}h{-run:.1f}z")
    color = _levels(theme)
    return "".join(f'<path fill="{color[k]}" d="{"".join(paths[k])}"/>' for k in sorted(paths))


def hero(theme: Theme, facts: Facts, grid: np.ndarray, days: Sequence[date]) -> str:
    w, h = 1200, 600
    rows, cols = grid.shape
    cw, pitch, ch = 1.9, 13, 11
    x0, y0 = 1144 - cols * cw, 112
    tw, th = cols * cw, rows * pitch - (pitch - ch)
    color = _levels(theme)
    cells = _cells(grid, x0, y0, cw, pitch, ch, theme)

    # Month ticks along the bottom.
    ticks = []
    seen = set()
    for i, d in enumerate(days):
        if d.month in (9, 12, 3, 6) and (d.year, d.month) not in seen:
            seen.add((d.year, d.month))
            x = x0 + i * cw
            ticks.append(f'<rect x="{x:.1f}" y="{y0 + th + 8}" width="1" height="6" fill="{theme.axis}"/>')
            ticks.append(_t(x, y0 + th + 30, d.strftime("%b %Y"), 14, theme.muted))
    last = days[-1]
    ticks.append(_t(x0 + tw, y0 + th + 30, last.strftime("%b %Y"), 14, theme.muted, anchor="end"))

    # Legend: more short ... more long.
    sw, sg = 13, 2
    legend_w = 11 * sw + 10 * sg
    lx = x0 + tw - legend_w - text_width("More long", 14) - 10
    legend = [_t(lx - 10, y0 - 22, "More short", 14, theme.ink2, anchor="end")]
    for i, k in enumerate(range(-5, 6)):
        legend.append(
            f'<rect x="{lx + i * (sw + sg):g}" y="{y0 - 32}" width="{sw}" height="11" rx="2" fill="{color[k]}"/>'
        )
    legend.append(_t(lx + legend_w + 10, y0 - 22, "More long", 14, theme.ink2))
    label = _t(x0, y0 - 22, "What the crowd really held", 14, theme.ink, weight=650)

    # Left column.
    lx0 = 72
    sub = wrap("Funding rates, long/short ratios and more, tested against the real positions of every trader "
               "on Hyperliquid.", 20, 400)  # fmt: skip
    left = [
        f'<g class="fu d1">{_mark(lx0, 112, theme, cell=9, gap=2.5)}'
        + _t(lx0 + 44, 132, "OPEN BENCHMARK · PRE-REGISTERED", 14, theme.ink2, weight=650, spacing=1.8)
        + "</g>",
        _t(lx0 - 4, 238, "HerdBench", 86, theme.ink, weight=800, spacing=-3, cls="fu d2"),
        _t(lx0, 300, "Crowd signals, fact-checked.", 32, theme.ink, weight=650, spacing=-0.6, cls="fu d3"),
    ]
    for i, line in enumerate(sub):
        left.append(_t(lx0, 346 + i * 29, line, 20, theme.ink2, cls="fu d4"))
    stats = [
        (str(len(facts.markets)), "markets"),
        (str(facts.days), "days scored"),
        (f"{facts.confirmed} of {len(facts.family)}", "signals passed"),
    ]
    sx: float = lx0
    for i, (big, small) in enumerate(stats):
        left.append(
            f'<g class="fu d{6 + i}">'
            + _t(sx, 500, big, 30, theme.ink, weight=750, spacing=-0.5)
            + _t(sx, 526, small, 14, theme.muted)
            + "</g>"
        )
        sx += max(text_width(big, 30, True), text_width(small, 14)) + 40
    left.append(f'<rect x="{lx0}" y="452" width="{sx - lx0 - 40:.0f}" height="1" fill="{theme.grid}" class="fi d5"/>')

    span = tw + 12
    css = (
        f".wipe{{transform:translateX({span:.0f}px);animation:wipe 2.6s cubic-bezier(.65,0,.35,1) .45s both}}"
        f"@keyframes wipe{{from{{transform:translateX(0)}}to{{transform:translateX({span:.0f}px)}}}}"
        f".scan{{opacity:0;animation:scan 2.6s cubic-bezier(.65,0,.35,1) .45s both}}"
        f"@keyframes scan{{0%{{transform:translateX(0);opacity:0}}8%{{opacity:1}}86%{{opacity:1}}"
        f"100%{{transform:translateX({span:.0f}px);opacity:0}}}}"
    )
    defs = (
        f'<defs><clipPath id="tap"><rect x="{x0 - 6:.1f}" y="{y0 - 4}" width="{tw + 12:.1f}" height="{th + 8}"/>'
        f'</clipPath><linearGradient id="glow" x1="0" x2="1" y1="0" y2="0">'
        f'<stop offset="0" stop-color="{theme.blue}" stop-opacity="0"/>'
        f'<stop offset="1" stop-color="{theme.blue}" stop-opacity="0.32"/></linearGradient></defs>'
    )
    reveal = (
        f'<g clip-path="url(#tap)">'
        f'<rect class="wipe" x="{x0 - 6:.1f}" y="{y0 - 4}" width="{tw + 12:.1f}" height="{th + 8}" '
        f'fill="{theme.surface}"/>'
        f'<g class="scan"><rect x="{x0 - 46:.1f}" y="{y0 - 4}" width="40" height="{th + 8}" fill="url(#glow)"/>'
        f'<rect x="{x0 - 7:.1f}" y="{y0 - 4}" width="2" height="{th + 8}" fill="{theme.blue}"/></g></g>'
    )
    body = (
        defs
        + "".join(left)
        + f'<g class="fi d3">{label}{"".join(legend)}</g>'
        + cells
        + reveal
        + f'<g class="fi d12">{"".join(ticks)}</g>'
    )
    title = "HerdBench: crowd signals, fact-checked"
    desc = (
        f"A tapestry of the real net positioning of the crowd, without market makers, in {rows} markets over "
        f"{cols} days: blue where traders held more longs, red where they held more shorts. Beside it: "
        f"HerdBench, crowd signals fact-checked; {facts.confirmed} of {len(facts.family)} signals passed."
    )
    return _svg(w, h, title, desc, theme, css, body)


# --------------------------------------------------------------------------------------------------------------
# 2. How it works: three steps joined by moving dots
# --------------------------------------------------------------------------------------------------------------


def _ledger(cx: float, cy: float, theme: Theme) -> str:
    x, y = cx - 56, cy - 62
    out = [f'<rect x="{x:g}" y="{y:g}" width="112" height="124" rx="14" fill="{theme.raised}" stroke="{theme.grid}"/>']
    bars = ((52, theme.blue), (30, theme.red), (44, theme.blue), (22, theme.blue), (38, theme.red))
    for i, (length, color) in enumerate(bars):
        ry = y + 22 + i * 20
        out.append(f'<circle cx="{x + 22:g}" cy="{ry:g}" r="5" fill="{theme.muted}"/>')
        out.append(f'<rect x="{x + 36:g}" y="{ry - 4:g}" width="{length}" height="8" rx="4" fill="{color}"/>')
    return "".join(out)


def _crowd(cx: float, cy: float, theme: Theme) -> str:
    out = []
    golden = math.pi * (3 - math.sqrt(5))
    for i in range(26):
        rad = 8.2 * math.sqrt(i + 0.6)
        a = i * golden
        x, y = cx - 14 + rad * math.cos(a), cy + rad * math.sin(a) * 0.92
        color = theme.blue if (i * 7) % 10 < 6 else theme.red
        out.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="5" fill="{color}"/>')
    out.append(
        f'<rect x="{cx + 48:g}" y="{cy - 40:g}" width="32" height="84" rx="16" fill="none" '
        f'stroke="{theme.grid}" stroke-width="1.5" class="fi d8"/>'
    )
    for j, dy in enumerate((-24, -8, 8, 24)):
        out.append(f'<circle cx="{cx + 64:g}" cy="{cy + dy:g}" r="5" fill="{theme.muted}" class="fi d{8 + j}"/>')
    return "".join(out)


def _grades(cx: float, cy: float, theme: Theme) -> str:
    x, y = cx - 62, cy - 44
    out = []
    for i, (length, kind) in enumerate(((78, "check"), (40, "question"), (14, "cross"))):
        ry = y + i * 40
        tone = {"check": theme.good, "question": theme.warning, "cross": theme.critical}[kind]
        out.append(f'<rect x="{x:g}" y="{ry - 5:g}" width="92" height="10" rx="5" fill="{theme.grid}"/>')
        out.append(f'<rect x="{x:g}" y="{ry - 5:g}" width="{length}" height="10" rx="5" fill="{theme.blue}"/>')
        out.append(_glyph(kind, x + 116, ry, tone, r=11))
    return "".join(out)


def how(theme: Theme, facts: Facts) -> str:
    w, h = 1200, 450
    centers = (200, 600, 1000)
    icy = 142
    steps = (
        ("Everything is public", "Hyperliquid shows every account's open positions and every trade, on a public "
         "ledger."),
        ("Rebuild the real crowd", f"What traders really held in {len(facts.markets)} markets, every day for a year, "
         "with market makers set aside."),
        ("Grade the signals", "Each popular signal is scored against that truth, under rules sealed in Bitcoin "
         "before any result existed."),
    )  # fmt: skip
    icons = (_ledger, _crowd, _grades)
    out = []
    for i, ((title, text), cx, icon) in enumerate(zip(steps, centers, icons, strict=True)):
        d = 1 + i * 3
        out.append(f'<g class="fu d{d}">{icon(cx, icy, theme)}</g>')
        out.append(_t(cx, 262, f"STEP {i + 1}", 13, theme.muted, weight=700, anchor="middle", spacing=2,
                      cls=f"fu d{d + 1}"))  # fmt: skip
        out.append(_t(cx, 294, title, 25, theme.ink, weight=700, anchor="middle", spacing=-0.3, cls=f"fu d{d + 1}"))
        for j, line in enumerate(wrap(text, 18, 316)):
            out.append(_t(cx, 330 + j * 26, line, 18, theme.ink2, anchor="middle", cls=f"fu d{d + 2}"))
    # Connectors with dots travelling from step to step.
    links = []
    for a, b in ((centers[0], centers[1]), (centers[1], centers[2])):
        xa, xb = a + 92, b - 92
        links.append(f'<rect x="{xa}" y="{icy}" width="{xb - xa}" height="1.5" fill="{theme.axis}"/>')
        for k in range(3):
            links.append(
                f'<circle cx="{xa}" cy="{icy + 0.75}" r="4" fill="{theme.blue}" class="flow" '
                f'style="animation-delay:{k * 0.35:.2f}s"/>'
            )
        links.append(
            f'<path d="M{xb - 7} {icy - 5.25}l7 6-7 6" fill="none" stroke="{theme.axis}" stroke-width="1.5" '
            f'stroke-linecap="round" stroke-linejoin="round"/>'
        )
    dist = 400 - 184 - 10
    css = (
        f".flow{{opacity:0;animation:flow 2.1s cubic-bezier(.45,0,.55,1) 2 both}}"
        f"@keyframes flow{{0%{{transform:translateX(0);opacity:0}}12%{{opacity:1}}80%{{opacity:1}}"
        f"100%{{transform:translateX({dist}px);opacity:0}}}}"
    )
    body = f'<g class="fi d1">{"".join(links)}</g>' + "".join(out)
    title = "How HerdBench works"
    desc = (
        "Three steps. Everything is public: Hyperliquid shows every account's positions and trades. Rebuild the "
        f"real crowd: what traders held in {len(facts.markets)} markets every day, market makers set aside. "
        "Grade the signals: each popular signal is scored against that truth under rules fixed in advance."
    )
    return _svg(w, h, title, desc, theme, css, body)


# --------------------------------------------------------------------------------------------------------------
# 3. Scorecard: the seven confirmatory signals against the truth
# --------------------------------------------------------------------------------------------------------------


def _chip(x: float, cy: float, verdict: str, theme: Theme, cls: str) -> str:
    label, tone, kind = VERDICTS[verdict]
    color = {"good": theme.good, "warning": theme.warning, "critical": theme.critical}[tone]
    width = 44 + text_width(label, 15, True)
    return (
        f'<g class="{cls}"><rect x="{x:g}" y="{cy - 17:g}" width="{width:.0f}" height="34" rx="17" '
        f'fill="{color}" fill-opacity="0.13"/>{_glyph(kind, x + 19, cy, color, r=10)}'
        + _t(x + 36, cy + 5.5, label, 15, theme.ink, weight=650)
        + "</g>"
    )


def scorecard(theme: Theme, facts: Facts) -> str:
    w = 1200
    top, pitch = 222, 74
    rows = facts.family
    h = top + pitch * len(rows) + 118
    lo, hi = -0.15, 0.45
    px0, px1 = 508, 920

    def sx(v: float) -> float:
        return px0 + (v - lo) / (hi - lo) * (px1 - px0)

    plot_top, plot_bottom = top - 40, top + pitch * (len(rows) - 1) + 40
    head = [
        _t(56, 76, TITLE, 32, theme.ink, weight=750, spacing=-0.6, cls="fu d1"),
    ]
    sub = (
        "How closely each signal follows what traders really held, measured inside each market over "
        f"{facts.days} days and averaged across {len(facts.markets)} markets. A thin line marks the 95% range."
    )
    for i, line in enumerate(wrap(sub, 18, 1088)):
        head.append(_t(56, 112 + i * 26, line, 18, theme.ink2, cls="fu d2"))
    grid = [
        f'<rect x="{sx(-0.10):.1f}" y="{plot_top}" width="{sx(0.10) - sx(-0.10):.1f}" '
        f'height="{plot_bottom - plot_top}" rx="8" fill="{theme.raised}"/>',
    ]
    for v in (-0.1, 0.0, 0.1, 0.2, 0.3, 0.4):
        x = sx(v)
        if v != 0.0:
            grid.append(f'<rect x="{x:.1f}" y="{plot_top}" width="1" height="{plot_bottom - plot_top}" '
                        f'fill="{theme.grid}" fill-opacity="0.7"/>')  # fmt: skip
        grid.append(_t(x, plot_bottom + 26, _num(v, 1), 14, theme.muted, anchor="middle"))
    grid.append(f'<rect x="{sx(0) - 0.75:.1f}" y="{plot_top}" width="1.5" height="{plot_bottom - plot_top}" '
                f'fill="{theme.axis}"/>')  # fmt: skip
    grid.append(_t((sx(-0.1) + sx(0.1)) / 2, plot_top - 12, "Too small to matter", 14, theme.muted,
                   anchor="middle"))  # fmt: skip
    grid.append(_t(sx(0.30), plot_top - 12, "Tracks the crowd →", 14, theme.muted, anchor="middle"))
    marks = []
    for i, r in enumerate(rows):
        cy = top + i * pitch
        name, what = PLAIN[r["proxy"]]
        d = 3 + i
        marks.append(_t(56, cy - 3, name, 19, theme.ink, weight=650, cls=f"fu d{d}"))
        marks.append(_t(56, cy + 21, what, 15, theme.muted, cls=f"fu d{d}"))
        x_zero, x_val = sx(0.0), sx(r["rho"])
        left, width = min(x_zero, x_val), abs(x_val - x_zero)
        origin = "left" if r["rho"] >= 0 else "right"
        marks.append(
            f'<path class="grow d{d + 2}" style="transform-origin:{origin} center" fill="{theme.blue}" '
            f'd="{_bar(left, cy - 8, width, 16, rounded="right" if r["rho"] >= 0 else "left")}"/>'
        )
        xl, xh = sx(r["ci_low"]), sx(r["ci_high"])
        marks.append(
            f'<g class="fi d{d + 6}" fill="{theme.ink2}"><rect x="{xl:.1f}" y="{cy - 1:g}" width="{xh - xl:.1f}" '
            f'height="2" rx="1"/><rect x="{xl - 1:.1f}" y="{cy - 7:g}" width="2" height="14" rx="1"/>'
            f'<rect x="{xh - 1:.1f}" y="{cy - 7:g}" width="2" height="14" rx="1"/></g>'
        )
        marks.append(_t(max(xh, x_val) + 12, cy + 5, _num(r["rho"]), 16, theme.ink, weight=650,
                        cls=f"fi d{d + 6}", extra='font-variant-numeric="tabular-nums"'))  # fmt: skip
        marks.append(_chip(956, cy, r["verdict"], theme, f"fu d{d + 7}"))
        if i < len(rows) - 1:
            marks.append(f'<rect x="56" y="{cy + pitch / 2:.0f}" width="{w - 112}" height="1" fill="{theme.grid}" '
                         f'fill-opacity="0.6"/>')  # fmt: skip
    foot = (
        f"Within-market Spearman correlation with the net position of traders other than market makers. "
        f"{facts.confirmed} of {len(rows)} pass after correcting for testing {len(rows)} signals at once (Holm). "
        "Binance statistics: CC BY-NC-SA 4.0."
    )
    feet = [_t(56, h - 66 + i * 22, line, 14, theme.muted, cls="fi d14") for i, line in enumerate(wrap(foot, 14, 1088))]
    css = (
        ".grow{transform-box:fill-box;animation:grow 1s cubic-bezier(.22,1,.36,1) both}"
        "@keyframes grow{from{transform:scaleX(0)}to{transform:scaleX(1)}}"
    )
    body = "".join(head) + f'<g class="fi d2">{"".join(grid)}</g>' + "".join(marks) + "".join(feet)
    title = TITLE
    desc = "Scorecard. " + " ".join(
        f"{PLAIN[r['proxy']][0]}: correlation {_num(r['rho'])} (95% range {_num(r['ci_low'])} to "
        f"{_num(r['ci_high'])}), {VERDICTS[r['verdict']][0].lower()}."
        for r in rows
    )
    return _svg(w, h, title, desc, theme, css, body)


def _bar(x: float, y: float, w: float, h: float, rounded: str, r: float = 4.0) -> str:
    """A bar path with a rounded data end and a square end at the baseline."""
    r = min(r, w / 2, h / 2) if w > 0 else 0.0
    if w <= 0:
        return f"M{x:.1f} {y:.1f}h0v{h:g}h0z"
    if rounded == "right":
        return (
            f"M{x:.1f} {y:.1f}h{w - r:.1f}a{r:g} {r:g} 0 0 1 {r:g} {r:g}v{h - 2 * r:g}"
            f"a{r:g} {r:g} 0 0 1 {-r:g} {r:g}h{-(w - r):.1f}z"
        )
    return (
        f"M{x + w:.1f} {y:.1f}h{-(w - r):.1f}a{r:g} {r:g} 0 0 0 {-r:g} {r:g}v{h - 2 * r:g}"
        f"a{r:g} {r:g} 0 0 0 {r:g} {r:g}h{w - r:.1f}z"
    )


# --------------------------------------------------------------------------------------------------------------
# 4. Insights: three findings in plain words
# --------------------------------------------------------------------------------------------------------------


def insights(theme: Theme, facts: Facts) -> str:
    w, h = 1200, 500
    cw, gap, x0, y0 = 352, 24, 36, 36
    ch = h - 2 * y0
    xs = [x0 + i * (cw + gap) for i in range(3)]
    cards = (
        _card1(theme, xs[0], y0, cw, ch, facts),
        _card2(theme, xs[1], y0, cw, ch, facts),
        _card3(theme, xs[2], y0, cw, ch, facts),
    )
    css = (
        ".grow{transform-box:fill-box;animation:grow 1s cubic-bezier(.22,1,.36,1) both}"
        "@keyframes grow{from{transform:scaleX(0)}to{transform:scaleX(1)}}"
    )
    title = "Three things the data showed"
    desc = (
        f"Funding sat at its floor rate in {round(facts.pinned_share * 100)}% of hours and scored the same as the "
        f"price premium. Binance's all-accounts ratio tracks how many traders are long ({_num(facts.heads_rho)}) "
        f"but not where the money is ({_num(facts.money_rho)}). Positions near liquidation forecast next week's "
        f"liquidations ({_num(facts.fragile_rho)}), but last week's liquidations do as well "
        f"({_num(facts.past_liq_rho)})."
    )
    return _svg(w, h, title, desc, theme, css, "".join(cards))


def _caption_size(cw: float) -> float:
    """One size for every card caption: the largest at which the longest caption fits the card."""
    return min(fit(c, 16, cw - 60, True) for c in (CAPTION1, CAPTION3))


def _card_frame(theme: Theme, x: float, y: float, cw: float, ch: float, kicker: str, text: str) -> list[str]:
    parts = [
        f'<rect x="{x:g}" y="{y:g}" width="{cw:g}" height="{ch:g}" rx="18" fill="{theme.raised}"/>',
        _t(x + 30, y + 46, kicker, 13, theme.muted, weight=700, spacing=1.8),
    ]
    for j, line in enumerate(wrap(text, 16, cw - 60)):
        parts.append(_t(x + 30, y + 312 + j * 24, line, 16, theme.ink2))
    return parts


def _hbar(x: float, y: float, label: str, value: float, scale: float, color: str, theme: Theme, cls: str) -> str:
    width = max(value, 0.0) * scale
    return (
        _t(x, y, label, 15, theme.ink2)
        + f'<rect x="{x:g}" y="{y + 9:g}" width="{scale:.0f}" height="10" rx="5" fill="{theme.grid}"/>'
        + f'<path class="{cls}" style="transform-origin:left center" fill="{color}" '
        f'd="{_bar(x, y + 9, max(width, 2.0), 10, "right")}"/>'
        + _t(x + scale + 12, y + 18.5, _num(value), 16, theme.ink, weight=700)
    )


def _card1(theme: Theme, x: float, y: float, cw: float, ch: float, facts: Facts) -> str:
    """Funding sat at its floor: one square per percent of hours, grey when pinned."""
    pinned = round(facts.pinned_share * 100)
    parts = _card_frame(
        theme, x, y, cw, ch, "FUNDING RATES",
        f"When funding does move, it follows the price premium, and it scores the same "
        f"({_num(facts.funding_rho)}). The premium is the cleaner{NBSP}read.",
    )  # fmt: skip
    parts.append(_t(x + 28, y + 112, f"{pinned}%", 56, theme.ink, weight=750, spacing=-1.5))
    parts.append(_t(x + 30, y + 142, CAPTION1, _caption_size(cw), theme.ink2, weight=600))
    cells = []
    for k in range(100):
        cx, cy = x + 30 + 13.2 * (k % 20), y + 176 + 13.2 * (k // 20)
        fill = theme.muted if k < pinned else theme.blue
        cells.append(f'<rect x="{cx:.1f}" y="{cy:.1f}" width="10.2" height="10.2" rx="2.5" fill="{fill}"/>')
    parts.append(f'<g class="fi d6">{"".join(cells)}</g>')
    parts.append(
        f'<rect x="{x + 30:g}" y="{y + 252}" width="10" height="10" rx="2.5" fill="{theme.muted}"/>'
        + _t(x + 46, y + 261, "at the floor", 14, theme.muted)
        + f'<rect x="{x + 140:g}" y="{y + 252}" width="10" height="10" rx="2.5" fill="{theme.blue}"/>'
        + _t(x + 156, y + 261, "moving", 14, theme.muted)
    )
    return f'<g class="fu d1">{"".join(parts)}</g>'


def _card2(theme: Theme, x: float, y: float, cw: float, ch: float, facts: Facts) -> str:
    """Binance's ratio counts heads, not money."""
    parts = _card_frame(
        theme, x, y, cw, ch, "LONG/SHORT RATIOS",
        "Binance's all-accounts ratio follows how many traders are long, not where the money is. On its own "
        "it cannot tell you which way the crowd is betting.",
    )  # fmt: skip
    parts.append(_t(x + 28, y + 100, "Heads, not", 42, theme.ink, weight=750, spacing=-1))
    parts.append(_t(x + 28, y + 146, "money", 42, theme.ink, weight=750, spacing=-1))
    scale = cw - 60 - 52
    parts.append(_hbar(x + 30, y + 190, "How many traders are long", facts.heads_rho, scale, theme.blue, theme,
                       "grow d8"))  # fmt: skip
    parts.append(_hbar(x + 30, y + 240, "Where the money is", facts.money_rho, scale, theme.blue, theme, "grow d9"))
    return f'<g class="fu d4">{"".join(parts)}</g>'


def _card3(theme: Theme, x: float, y: float, cw: float, ch: float, facts: Facts) -> str:
    """Fragility forecasts liquidations, but so do last week's liquidations."""
    parts = _card_frame(
        theme, x, y, cw, ch, "LIQUIDATIONS",
        "Positions close to their liquidation price forecast next week's liquidations, but last week's "
        "liquidations forecast them just as well.",
    )  # fmt: skip
    parts.append(_t(x + 28, y + 112, _num(facts.fragile_rho), 56, theme.ink, weight=750, spacing=-1.5))
    parts.append(_t(x + 30, y + 142, CAPTION3, _caption_size(cw), theme.ink2, weight=600))
    scale = cw - 60 - 52
    parts.append(_hbar(x + 30, y + 190, "Positions near liquidation now", facts.fragile_rho, scale, theme.blue,
                       theme, "grow d11"))  # fmt: skip
    parts.append(_hbar(x + 30, y + 240, "Last week's liquidations", facts.past_liq_rho, scale, theme.muted, theme,
                       "grow d12"))  # fmt: skip
    return f'<g class="fu d7">{"".join(parts)}</g>'


# --------------------------------------------------------------------------------------------------------------
# 5. Timeline: the plan was sealed before any result existed
# --------------------------------------------------------------------------------------------------------------


def _lock(cx: float, cy: float, color: str, bg: str) -> str:
    return (
        f'<path d="M{cx - 6} {cy - 2}v-4a6 6 0 0 1 12 0v4" fill="none" stroke="{bg}" stroke-width="2.2"/>'
        f'<rect x="{cx - 9}" y="{cy - 2}" width="18" height="13" rx="3" fill="{bg}"/>'
        f'<circle cx="{cx}" cy="{cy + 4.5}" r="2" fill="{color}"/>'
    )


def timeline(theme: Theme, facts: Facts) -> str:
    w, h = 1200, 360
    y = 132
    xs = (210, 600, 990)
    gapd = SCORED_AT - SEALED_AT
    hours = gapd.seconds // 3600
    span_label = f"{gapd.days} days {hours} hours later"
    items = (
        ("17 NOV 2026, 22:07 UTC", "Plan sealed in Bitcoin",
         f"Every test, threshold and verdict rule written down, then anchored in block{NBSP}{SEALED_BLOCK:,}."),
        ("23 NOV 2026, 14:51 UTC", "First look at the truth",
         "The scoring code is frozen, then compared with what traders really held for the first time."),
        (PUBLISHED_AT.strftime("%d %b %Y, %H:%M UTC").upper(), "Results published",
         f"Every table and figure released, each file hashed and rebuildable from public{NBSP}data."),
    )  # fmt: skip
    out = [
        f'<rect x="{xs[0]}" y="{y - 1}" width="{xs[2] - xs[0]}" height="2" fill="{theme.grid}"/>',
        f'<rect class="draw" x="{xs[0]}" y="{y - 1}" width="{xs[2] - xs[0]}" height="2" fill="{theme.blue}"/>',
    ]
    for i, ((when, title, text), x) in enumerate(zip(items, xs, strict=True)):
        d = 2 + i * 4
        disc = theme.blue if i < 2 else theme.good
        out.append(f'<g class="pop d{d}" style="transform-origin:{x}px {y}px">'
                   f'<circle cx="{x}" cy="{y}" r="22" fill="{theme.surface}" stroke="{disc}" stroke-width="2"/>'
                   f'<circle cx="{x}" cy="{y}" r="15" fill="{disc}"/>' + _icon(i, x, y, disc) + "</g>")  # fmt: skip
        out.append(_t(x, y - 46, when, 13, theme.muted, weight=700, anchor="middle", spacing=1.4, cls=f"fu d{d}"))
        out.append(_t(x, y + 62, title, 21, theme.ink, weight=700, anchor="middle", spacing=-0.2,
                      cls=f"fu d{d + 1}"))  # fmt: skip
        for j, line in enumerate(wrap(text, 16, 300)):
            out.append(_t(x, y + 94 + j * 24, line, 16, theme.ink2, anchor="middle", cls=f"fu d{d + 2}"))
    mid = (xs[0] + xs[1]) / 2
    pill = text_width(span_label, 14, True) + 36
    out.append(
        f'<g class="fi d4"><rect x="{mid - pill / 2:.1f}" y="{y - 16}" width="{pill:.1f}" height="32" rx="16" '
        f'fill="{theme.surface}" stroke="{theme.grid}"/>'
        + _t(mid, y + 5, span_label, 14, theme.ink2, weight=650, anchor="middle")
        + "</g>"
    )
    css = (
        ".draw{transform-box:fill-box;transform-origin:left center;animation:grow 2.2s cubic-bezier(.65,0,.35,1) "
        ".2s both}@keyframes grow{from{transform:scaleX(0)}to{transform:scaleX(1)}}"
        ".pop{animation:pop .7s cubic-bezier(.34,1.56,.64,1) both}"
        "@keyframes pop{from{opacity:0;transform:scale(.4)}to{opacity:1;transform:none}}"
    )
    title = "Sealed before any result existed"
    desc = (
        f"Timeline. 17 November 2026, 22:07 UTC: the plan is anchored in Bitcoin block {SEALED_BLOCK:,}. "
        f"{span_label.capitalize()}, on 23 November 2026 at 14:51 UTC, the frozen scoring code is run against the "
        "truth for the first time. The results are then published, every file hashed."
    )
    return _svg(w, h, title, desc, theme, css, "".join(out))


def _icon(i: int, cx: float, cy: float, color: str) -> str:
    if i == 0:
        return _lock(cx, cy - 1, color, "#fff")
    if i == 1:
        return (
            f'<circle cx="{cx - 2}" cy="{cy - 2}" r="6" fill="none" stroke="#fff" stroke-width="2.2"/>'
            f'<path d="M{cx + 2.5} {cy + 2.5}l4.5 4.5" stroke="#fff" stroke-width="2.6" stroke-linecap="round"/>'
        )
    return (
        f'<path d="M{cx - 6} {cy + 0.5}l4 4 8-9" fill="none" stroke="#fff" stroke-width="2.4" '
        f'stroke-linecap="round" stroke-linejoin="round"/>'
    )


def social(facts: Facts, grid: np.ndarray, days: Sequence[date], theme: Theme = DARK) -> str:
    """The 1280 by 640 card shown when the repository link is shared. Static: a PNG is made from it."""
    w, h = 1280, 640
    rows, cols = grid.shape
    cw, pitch, ch = 1.6, 14, 12
    x0, y0 = w - 76 - cols * cw, 112
    th = rows * pitch - (pitch - ch)
    color = _levels(theme)
    lx = 80
    body = [
        f'<rect width="{w}" height="{h}" fill="{theme.surface}"/>',
        _mark(lx, 128, theme, cell=11, gap=3),
        _t(lx + 54, 153, "OPEN BENCHMARK \u00b7 PRE-REGISTERED", 17, theme.ink2, weight=650, spacing=2),
        _t(lx - 5, 276, "HerdBench", 108, theme.ink, weight=800, spacing=-4),
        _t(lx, 344, "Crowd signals, fact-checked.", fit("Crowd signals, fact-checked.", 42, 560, True), theme.ink,
           weight=650, spacing=-0.8),
    ]  # fmt: skip
    for i, line in enumerate(wrap("Tested against the real positions of every trader on Hyperliquid.", 25, 540)):
        body.append(_t(lx, 398 + i * 35, line, 25, theme.ink2))
    sx: float = lx
    for big, small in (
        (str(len(facts.markets)), "markets"),
        (str(facts.days), "days scored"),
        (f"{facts.confirmed} of {len(facts.family)}", "signals passed"),
    ):
        body.append(_t(sx, 530, big, 38, theme.ink, weight=750, spacing=-0.5) + _t(sx, 560, small, 17, theme.muted))
        sx += max(text_width(big, 38, True), text_width(small, 17)) + 44
    body.append(_t(x0, y0 - 24, "What the crowd really held", 17, theme.ink, weight=650))
    sw, sg, tw = 14, 2, cols * cw
    end_x = x0 + tw - text_width("long", 15) - 8
    lx2 = end_x - (11 * sw + 10 * sg)
    for i, k in enumerate(range(-5, 6)):
        body.append(
            f'<rect x="{lx2 + i * (sw + sg):g}" y="{y0 - 36}" width="{sw}" height="12" rx="2" fill="{color[k]}"/>'
        )
    body.append(_t(lx2 - 8, y0 - 25, "short", 15, theme.ink2, anchor="end"))
    body.append(_t(x0 + tw, y0 - 25, "long", 15, theme.ink2, anchor="end"))
    body.append(_cells(grid, x0, y0, cw, pitch, ch, theme))
    body.append(_t(x0, y0 + th + 34, f"{days[0]:%b %Y}", 16, theme.muted))
    body.append(_t(x0 + cols * cw, y0 + th + 34, f"{days[-1]:%b %Y}", 16, theme.muted, anchor="end"))
    body.append(_t(w - 76, h - 44, "github.com/z33shxn/herdbench", 17, theme.muted, anchor="end"))
    title = "HerdBench: crowd signals, fact-checked"
    desc = (
        f"Social card. HerdBench, crowd signals fact-checked, tested against the real positions of every trader on "
        f"Hyperliquid: {len(facts.markets)} markets, {facts.days} days scored, {facts.confirmed} of "
        f"{len(facts.family)} signals passed, beside a tapestry of what the crowd really held."
    )
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" viewBox="0 0 {w} {h}" role="img" '
        f'aria-labelledby="title desc"><title id="title">{escape(title)}</title><desc id="desc">{escape(desc)}</desc>'
        f"<style>text{{font-family:{FONT}}}</style>{''.join(body)}</svg>\n"
    )


# --------------------------------------------------------------------------------------------------------------
# Build
# --------------------------------------------------------------------------------------------------------------


def render_all(facts: Facts, panel: pd.DataFrame) -> dict[str, str]:
    """Every image as ``{"<name>-<theme>.svg": svg_text}``, plus the static ``social.svg`` card."""
    grid, _, days = tapestry_grid(panel, facts.markets)
    out = {}
    for theme in THEMES:
        out[f"hero-{theme.name}.svg"] = hero(theme, facts, grid, days)
        out[f"how-{theme.name}.svg"] = how(theme, facts)
        out[f"scorecard-{theme.name}.svg"] = scorecard(theme, facts)
        out[f"insights-{theme.name}.svg"] = insights(theme, facts)
        out[f"timeline-{theme.name}.svg"] = timeline(theme, facts)
    out["social.svg"] = social(facts, grid, days)
    return out


def build(results: Path, out: Path, panel: pd.DataFrame | None = None) -> list[Path]:
    """Write every README image into ``out`` from the results folder (and the bundled truth panel)."""
    from herdbench.pipeline import write_text

    if panel is None:
        from herdbench.api import load_truth_panel

        panel = load_truth_panel(cohort="non_mm", as_pandas=True)  # type: ignore[assignment]
    assert panel is not None
    facts = load_facts(results)
    out.mkdir(parents=True, exist_ok=True)
    written = []
    for name, svg in render_all(facts, panel).items():
        path = out / name
        write_text(path, svg)
        written.append(path)
    return written
