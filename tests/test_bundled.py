"""The published files in this checkout: byte-identical to the manifest, and private by construction."""

from __future__ import annotations

import json
import re
from pathlib import Path

import polars as pl
import pytest

from herdbench import pipeline
from herdbench.config import PREREG

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "results" / "manifest.json"
DATA = ROOT / "src" / "herdbench" / "data"
PANEL = DATA / "truth_panel.parquet"
needs_release = pytest.mark.skipif(not MANIFEST.exists(), reason="no published results in this checkout")
needs_panel = pytest.mark.skipif(not PANEL.exists(), reason="no bundled panel in this checkout")


@needs_release
def test_every_published_file_matches_the_manifest():
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    for rel, digest in manifest["results_sha256"].items():
        path = DATA / rel if rel.endswith(".parquet") else ROOT / "results" / rel
        assert pipeline.sha256_file(path) == digest, rel


@needs_panel
def test_bundled_panel_passes_disclosure_control():
    panel = pl.read_parquet(PANEL)
    assert set(panel["cohort"].unique()) <= set(PREREG.cohorts)
    assert (panel["n_entities"] >= PREREG.min_entities).all()
    assert (panel["n_entities"] % PREREG.publish_count_floor == 0).all()
    assert not {"top2_share", "suppressed", "user", "entity", "address"} & set(panel.columns)
    for col, dtype in panel.schema.items():
        if dtype == pl.String:
            assert not panel[col].str.contains(r"0x[0-9a-fA-F]{40}").any(), col


@needs_panel
def test_bundled_proxies_hold_no_binance_columns():
    proxies = pl.read_parquet(DATA / "proxies.parquet")
    assert not [c for c in proxies.columns if c.startswith("bn_")]


def test_no_file_in_the_repository_names_a_non_protocol_address():
    allowed = set(PREREG.protocol_addresses) | {"0x4000000000000000000000000000000000000000"}
    pattern = re.compile(r"0x[0-9a-fA-F]{40}")
    for path in [
        *ROOT.glob("*.md"),
        *ROOT.glob("docs/**/*.md"),
        *ROOT.glob("results/**/*.*"),
        *ROOT.glob("src/**/*.py"),
    ]:
        if path.suffix in (".parquet", ".svg", ".ots"):
            continue
        found = set(pattern.findall(path.read_text(encoding="utf-8", errors="ignore"))) - allowed
        assert not found, (path, sorted(found)[:3])
