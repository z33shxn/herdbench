"""The README images: drawn from results/, valid and deterministic SVG, accessible, and free of underscores and
long dashes. The committed files in docs/visuals must be exactly what ``herdbench visuals`` draws."""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from herdbench import visuals

ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / "results"
SVG = "{http://www.w3.org/2000/svg}"
LONG_DASHES = "".join(chr(c) for c in (0x2012, 0x2013, 0x2014, 0x2015, 0x2212, 0x2E3A, 0x2E3B, 0xFE58))
needs_results = pytest.mark.skipif(not (RESULTS / "score_summary.json").exists(), reason="no published results")


@pytest.fixture(scope="module")
def drawn():
    from herdbench.api import load_truth_panel

    facts = visuals.load_facts(RESULTS)
    panel = load_truth_panel(cohort="non_mm", as_pandas=True)
    return facts, panel, visuals.render_all(facts, panel)


@needs_results
def test_every_image_comes_in_a_light_and_a_dark_version(drawn):
    _, _, images = drawn
    themed = {f"{n}-{t}.svg" for n in visuals.NAMES for t in ("light", "dark")}
    assert set(images) == themed | {"social.svg"}


@needs_results
def test_images_are_valid_svg_with_a_title_and_a_description(drawn):
    for name, svg in drawn[2].items():
        root = ET.fromstring(svg)
        assert root.tag == f"{SVG}svg", name
        title, desc = root.find(f"{SVG}title"), root.find(f"{SVG}desc")
        assert title is not None, name
        assert title.text, name
        assert desc is not None, name
        assert desc.text, name
        assert len(desc.text) > 40, name


@needs_results
def test_no_underscore_long_dash_script_or_outside_resource(drawn):
    for name, svg in drawn[2].items():
        assert "_" not in svg, name
        assert not any(c in svg for c in LONG_DASHES), name
        for banned in ("<script", "foreignObject", "<image", "@import", "url(http"):
            assert banned not in svg, (name, banned)
        assert not re.search(r"(href|src)=\"(https?:)?//", svg), name


@needs_results
def test_motion_stops_for_readers_who_ask_for_less(drawn):
    for name, svg in drawn[2].items():
        if name == "social.svg":
            assert "animation" not in svg  # the share card is a still image
            continue
        assert "@media (prefers-reduced-motion:reduce){*{animation:none!important}}" in svg, name
        assert "infinite" not in svg, f"{name}: motion must settle (WCAG 2.2.2: stop within five seconds)"


@needs_results
def test_hidden_states_only_live_inside_animations(drawn):
    """With animation switched off, everything must be visible and the reveal cover must sit off the tapestry."""
    hero = drawn[2]["hero-light.svg"]
    assert re.search(r"\.wipe\{transform:translateX\(\d+px\)", hero)
    assert ".scan{opacity:0" in hero  # the scanning line is decoration and is hidden at rest


@needs_results
def test_images_are_deterministic(drawn):
    facts, panel, images = drawn
    assert visuals.render_all(facts, panel) == images


@needs_results
def test_every_number_shown_comes_from_the_results(drawn):
    facts, _, images = drawn
    family = pd.read_csv(RESULTS / "binance" / "primary_family.csv")
    assert facts.confirmed == int((family["verdict"] == "confirmed").sum())
    assert len(facts.family) == len(family)
    card = images["scorecard-light.svg"]
    for r in family.itertuples():
        assert f">{visuals._num(float(r.rho))}</text>" in card, r.proxy
        assert visuals.VERDICTS[str(r.verdict)][0] in card
    hero = images["hero-light.svg"]
    assert f">{facts.confirmed} of {len(family)}</text>" in hero
    assert f">{facts.days}</text>" in hero
    assert f">{len(facts.markets)}</text>" in hero
    insights = images["insights-light.svg"]
    for value in (facts.heads_rho, facts.money_rho, facts.fragile_rho, facts.past_liq_rho, facts.funding_rho):
        assert visuals._num(value) in insights
    assert f">{round(facts.pinned_share * 100)}%</text>" in insights


@needs_results
def test_hero_draws_every_published_cell_of_the_primary_markets(drawn):
    facts, panel, _images = drawn
    grid, markets, days = visuals.tapestry_grid(panel, facts.markets)
    assert sorted(markets) == sorted(facts.markets)
    assert grid.shape == (len(facts.markets), len(days))
    means = pd.DataFrame(grid).mean(axis=1).to_numpy()
    assert (means[:-1] >= means[1:]).all()  # rows run from most net long to most net short


def test_timeline_dates_match_the_timestamps_record():
    text = (ROOT / "docs" / "timestamps" / "README.md").read_text(encoding="utf-8")
    assert f"block {visuals.SEALED_BLOCK:,}, mined {visuals.SEALED_AT:%Y-%m-%d %H:%M} UTC" in text
    assert f"{visuals.SCORED_AT:%H:%M:%S} UTC" in text


def test_text_helpers():
    assert visuals.wrap("a b c", 10, 1000) == ["a b c"]
    assert visuals.month(date(2025, 9, 1)) == "Sep 2025"  # English whatever the locale
    assert visuals.month(date(2026, 11, 23), year=False) == "Nov"
    lines = visuals.wrap("the quick brown fox jumps over the lazy dog again and again", 16, 120)
    assert len(lines) > 1
    assert all(visuals.text_width(line, 16) <= 120 for line in lines)
    assert visuals.fit("x" * 200, 20, 100) < 20
    assert visuals.fit("ok", 20, 100) == 20
    assert visuals._num(-0.0004) == "0.00"
    assert visuals._num(-0.024) == "-0.02"


def test_diverging_scale_is_symmetric_and_steps_away_from_the_midpoint():
    for theme in visuals.THEMES:
        mid = visuals.oklab(theme.mid)[0]
        for arm in (theme.pos, theme.neg):
            gaps = [abs(visuals.oklab(c)[0] - mid) for c in arm]
            assert gaps == sorted(gaps), theme.name
        for p, n in zip(theme.pos, theme.neg, strict=True):
            assert abs(visuals.oklab(p)[0] - visuals.oklab(n)[0]) < 0.03, theme.name


@needs_results
def test_the_committed_images_are_current(drawn):
    """docs/visuals holds exactly what ``herdbench visuals`` draws from this checkout."""
    for name, svg in drawn[2].items():
        path = ROOT / "docs" / "visuals" / name
        assert path.read_bytes() == svg.encode("utf-8"), f"{name} is stale: run `herdbench visuals`"


def test_the_share_card_png_is_1280_by_640():
    """docs/visuals/social.png is the repository's social preview, rendered from social.svg."""
    head = (ROOT / "docs" / "visuals" / "social.png").read_bytes()[:24]
    assert head[:8] == bytes([137, 80, 78, 71, 13, 10, 26, 10])  # the PNG signature
    assert int.from_bytes(head[16:20], "big") == 1280
    assert int.from_bytes(head[20:24], "big") == 640


@needs_results
def test_scorecard_rows_carry_their_own_name_value_and_verdict(drawn):
    """Each row's name, value and verdict sit on one line, and the rows run in the results' order (by rho)."""
    facts, _, images = drawn
    root = ET.fromstring(images["scorecard-light.svg"])
    texts = [(float(t.get("y", "0")), "".join(t.itertext())) for t in root.iter(f"{SVG}text")]
    rows_y = []
    for r in facts.family:
        ys = [y for y, s in texts if s == visuals.PLAIN[r["proxy"]][0]]
        assert len(ys) == 1, r["proxy"]
        rows_y.append(ys[0])
    assert rows_y == sorted(rows_y)
    for r, y in zip(facts.family, rows_y, strict=True):
        line = {s for ty, s in texts if abs(ty - y) <= 9}
        assert visuals._num(r["rho"]) in line, r["proxy"]
        assert visuals.VERDICTS[r["verdict"]][0] in line, r["proxy"]


@needs_results
def test_everything_is_visible_with_motion_off(drawn):
    """Outside @keyframes no rule hides anything except the decorative scan line and travelling dots, and the
    hero's reveal cover rests beyond the tapestry."""
    for name, svg in drawn[2].items():
        css = re.search(r"<style>(.*?)</style>", svg, re.S)
        assert css is not None, name
        rules = re.sub(r"@keyframes [a-z]+\{(?:[^{}]*\{[^{}]*\})*\}", "", css.group(1))
        for selector, declarations in re.findall(r"([^{}]+)\{([^{}]*)\}", rules):
            flat = declarations.replace(" ", "")
            if "opacity:0;" in flat + ";" or "scale(0" in flat or "scaleX(0" in flat:
                assert selector.strip() in (".scan", ".flow"), (name, selector)
    hero = drawn[2]["hero-light.svg"]
    offset = re.search(r"\.wipe\{transform:translateX\((\d+(?:\.\d+)?)px\)", hero)
    width = re.search(r'<rect class="wipe" x="[^"]+" y="[^"]+" width="([^"]+)"', hero)
    assert offset is not None
    assert width is not None
    assert float(offset.group(1)) >= float(width.group(1))


@needs_results
def test_stagger_delays_come_after_the_rules_they_delay(drawn):
    """An ``animation`` shorthand resets the delay, so the .dN rules must follow every image's own rules."""
    for name, svg in drawn[2].items():
        if name == "social.svg":
            continue
        style = svg[svg.index("<style>") : svg.index("@media (prefers-reduced-motion")]
        assert style.index(".d1{") > style.rfind("animation:"), name


@needs_results
def test_timeline_shows_the_recorded_times(drawn):
    svg = drawn[2]["timeline-light.svg"]
    for t in (visuals.SEALED_AT, visuals.SCORED_AT, visuals.PUBLISHED_AT):
        assert f">{visuals.stamp(t)}</text>" in svg
    assert f"block{visuals.NBSP}{visuals.SEALED_BLOCK:,}" in svg
    assert ">5 days 17 hours later</text>" in svg  # 5 days 16 hours 45 minutes, to the nearest hour


def test_published_time_is_the_commit_that_added_the_results():
    if shutil.which("git") is None:
        pytest.skip("git is not installed")
    shown = subprocess.run(["git", "show", "-s", "--format=%cI", "42f6fa8"], cwd=ROOT, capture_output=True, text=True)
    if shown.returncode != 0:
        pytest.skip("git history is not available in this checkout")
    assert datetime.fromisoformat(shown.stdout.strip()) == visuals.PUBLISHED_AT


def test_dates_never_depend_on_the_locale():
    source = Path(visuals.__file__).read_text(encoding="utf-8")
    assert "strftime" not in source
    assert "%b" not in source


def test_tapestry_cells_round_trip_and_keep_a_calendar_gap():
    """Every non-missing cell is drawn exactly once, in its level's colour, at its calendar position."""
    days = [date(2025, 1, 1) + timedelta(days=k) for k in (0, 1, 2, 20, 21)]
    grid = np.array([[0.0, 0.2, 0.2, -0.1, np.nan], [0.05, 0.05, 0.05, 0.05, 0.05]])
    offsets, span = visuals.calendar(days)
    assert span == 22
    assert visuals.gaps(offsets) == [(3, 17)]
    svg = visuals._cells(grid, 0.0, 0.0, 1.0, 10, 8, visuals.LIGHT, offsets)
    level_of = {color: level for level, color in visuals._levels(visuals.LIGHT).items()}
    seen: dict[tuple[int, int], int] = {}
    for fill, path in re.findall(r'<path fill="([^"]+)" d="([^"]+)"/>', svg):
        for x, y, run in re.findall(r"M([\d.]+) ([\d.]+)h([\d.]+)v", path):
            row = int(float(y) // 10)
            for off in range(round(float(x)), round(float(x) + float(run))):
                assert (row, off) not in seen
                seen[(row, off)] = level_of[fill]
    expected = {
        (r, offsets[c]): visuals._level(float(grid[r, c]))
        for r in range(grid.shape[0])
        for c in range(grid.shape[1])
        if not np.isnan(grid[r, c])
    }
    assert seen == expected


def test_build_draws_from_a_fresh_pipeline_run(built, tmp_path):
    """The command works on the pipeline's own output, using that run's truth panel."""
    written = visuals.build(built["paths"].results, tmp_path / "visuals")
    themed = {f"{n}-{t}.svg" for n in visuals.NAMES for t in ("light", "dark")}
    assert {p.name for p in written} == themed | {"social.svg"}
    for p in written:
        ET.fromstring(p.read_bytes())
        assert "_" not in p.read_text(encoding="utf-8")


@needs_results
def test_images_do_not_depend_on_the_hash_seed_or_the_locale(drawn):
    code = (
        "import hashlib, locale, sys\n"
        "from pathlib import Path\n"
        "locale.setlocale(locale.LC_ALL, '')\n"
        "from herdbench import visuals\n"
        "from herdbench.api import load_truth_panel\n"
        "facts = visuals.load_facts(Path(sys.argv[1]))\n"
        "images = visuals.render_all(facts, load_truth_panel(cohort='non_mm', as_pandas=True))\n"
        "print(hashlib.sha256(''.join(images[k] for k in sorted(images)).encode()).hexdigest())\n"
    )
    env = dict(os.environ, PYTHONHASHSEED="20261124")
    done = subprocess.run([sys.executable, "-c", code, str(RESULTS)], capture_output=True, text=True, env=env)
    assert done.returncode == 0, done.stderr[-2000:]
    images = drawn[2]
    assert done.stdout.strip() == hashlib.sha256("".join(images[k] for k in sorted(images)).encode()).hexdigest()
