"""The results report: every section renders from results/, figures are deterministic, nothing private leaks."""

from __future__ import annotations

import re

from herdbench import report

SECTIONS = [
    "## 1. Integrity gates and kill criteria",
    "## 2. The truth panel",
    "## 3. Primary family",
    "## 4. Positive control and cross-venue agreement",
    "## 5. Secondary statistics",
    "## 6. Second target",
    "## 7. Transfer to US single-stock perpetuals",
    "## 8. Futures bridges",
    "## 9. Robustness",
    "## 10. Deviations from the pre-registration",
]


def test_report_renders_every_section_with_light_and_dark_figures(built, tmp_path):
    dev = tmp_path / "deviations.md"
    dev.write_text("# Deviations\n\nNone so far.\n", encoding="utf-8")
    text = report.render(built["paths"].results, figures=tmp_path / "fig", deviations=dev)
    for s in SECTIONS:
        assert s in text
    assert "None so far." in text
    assert "`premium_24h`" in text
    assert not re.search(r"0x[0-9a-f]{40}", text)
    names = {p.name for p in (tmp_path / "fig").iterdir()}
    for stem in ("primary", "checks", "transfer"):
        assert {f"{stem}-light.svg", f"{stem}-dark.svg"} <= names
    assert 'media="(prefers-color-scheme: dark)"' in text


def test_figures_are_byte_for_byte_reproducible(built, tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    report.render(built["paths"].results, figures=a)
    report.render(built["paths"].results, figures=b)
    for f in a.iterdir():
        assert (b / f.name).read_bytes() == f.read_bytes(), f.name
        assert b"\r\n" not in f.read_bytes(), f.name


def test_formatting_helpers():
    assert report.pval(0.0004) == "<0.001"
    assert report.pval(float("nan")) == "–"
    assert report.interval(-0.1234, 0.5) == "[-0.123, 0.500]"
    assert report.num(1234) == "1,234"
    assert report.table([["a", "1"]], ["x", "y"]).splitlines()[1] == "|:---|---:|"


def test_without_a_figures_folder_the_report_writes_no_files(built, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    text = report.render(built["paths"].results)
    assert "<picture>" not in text
    assert not any(tmp_path.iterdir())
