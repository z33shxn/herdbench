"""The README images: drawn from results/, valid and deterministic SVG, accessible, and free of underscores and
long dashes. The committed files in docs/visuals must be exactly what ``herdbench visuals`` draws."""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from pathlib import Path

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
