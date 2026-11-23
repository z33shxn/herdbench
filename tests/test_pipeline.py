"""The whole pipeline on a synthetic archive: every stage runs, the gates pass on consistent data, the
licences are split, the pre-analysis addendum is enforced, and nothing private reaches ``results/``."""

from __future__ import annotations

import json
import re
from datetime import date

import pandas as pd
import polars as pl
import pytest

from herdbench import pipeline
from herdbench.truth import snapshots
from tests.utils import world

P = world.PREREG_WORLD
ADDRESS = re.compile(r"0x[0-9a-f]{40}")
VERDICTS = {"confirmed", "reversed", "uninformative", "inconclusive"}


def test_entity_map_groups_a_master_with_its_sub_accounts(built):
    info = built["info"]
    assert info["complete"]
    assert info["addresses_mapped"] == 3
    assert info["entities_with_several_addresses"] == 1


def test_gates_pass_when_snapshots_and_fills_agree(built):
    g = built["gates"]
    assert g["reconciliation"]["pairs"] == world.DAYS - 1
    assert g["reconciliation"]["reconciled_share"] == pytest.approx(1.0)
    assert g["reconciliation"]["market_maker_share"] == pytest.approx(1.0)
    assert g["gate0"]["passed"]
    assert g["label"] is None


def test_all_cohort_is_the_mirror_image_of_the_protocol_accounts(built):
    """Every long has a short: the net notional of all non-protocol accounts equals minus the protocol's."""
    day = date(2026, 3, 2)
    raw = snapshots.read_raw(
        built["paths"].hf
        / "perp_archive"
        / f"date={day}"
        / next(p.name for p in (built["paths"].hf / "perp_archive" / f"date={day}").glob("*.parquet"))
    )
    signed = raw.with_columns(net=pl.when(pl.col("size") > 0).then(pl.col("notional")).otherwise(-pl.col("notional")))
    hlp = signed.filter(pl.col("user") == world.HLP).group_by("market").agg(pl.col("net").sum())
    cells = built["cells"].filter((pl.col("date") == day) & (pl.col("cohort") == "all"))
    joined = cells.join(hlp, on="market", how="inner")
    assert joined.height > 0
    for row in joined.iter_rows(named=True):
        assert row["net_notional"] == pytest.approx(-row["net"], rel=1e-9, abs=1e-6)


def test_market_maker_is_removed_from_the_crowd(built):
    info = json.loads((built["paths"].derived / "truth_info.json").read_text(encoding="utf-8"))
    sizes = [n for n in info["market_makers"]["non_mm"].values() if n is not None]
    assert sizes
    assert max(sizes) >= 1
    mm_sets = pipeline.load_mm_sets(built["paths"])["non_mm"]
    assert any(s and world.MM in s for s in mm_sets.values())


def test_published_panel_is_rounded_and_carries_no_addresses(built):
    panel, cells = built["panel"], built["cells"]
    assert panel.height > 0
    assert set(panel["cohort"].unique()) <= set(P.cohorts)
    assert (panel["n_entities"] % 10 == 0).all()
    assert panel["ni"].drop_nulls().round(3).equals(panel["ni"].drop_nulls())
    assert {"cutoff", "dtl"} <= set(panel.columns)
    assert not {"top2_share", "suppressed"} & set(panel.columns)
    kept = panel.select(["date", "market", "cohort"]).join(
        cells.select(["date", "market", "cohort", "suppressed"]), on=["date", "market", "cohort"], how="left"
    )
    assert not kept["suppressed"].any()
    for col, dtype in panel.schema.items():
        if dtype == pl.String:
            assert not panel[col].str.contains(ADDRESS.pattern).any(), col


def test_no_result_file_contains_an_address(built):
    results = built["paths"].results
    files = [f for f in results.rglob("*") if f.is_file()]
    assert files
    for f in files:
        if f.suffix in (".csv", ".json", ".md"):
            assert not ADDRESS.search(f.read_text(encoding="utf-8")), f.name
        elif f.suffix == ".parquet":
            df = pl.read_parquet(f)
            for col, dtype in df.schema.items():
                if dtype == pl.String:
                    assert not df[col].str.contains(ADDRESS.pattern).any(), (f.name, col)


def test_primary_family_is_complete_and_licensed_with_binance(built):
    b = built["paths"].binance_results
    table = pd.read_csv(b / "primary_family.csv")
    assert list(table["proxy"]) == list(P.confirmatory_proxies)
    assert set(table["verdict"]) <= VERDICTS
    assert (table["licence"] == "CC BY-NC-SA 4.0").all()
    meta = json.loads((b / "primary_family.json").read_text(encoding="utf-8"))
    assert P.bootstrap_block_min <= meta["block_length"] <= P.bootstrap_block_max
    assert meta["published_panel_rerun"]["label"] == pipeline.IN_SAMPLE_LABEL


def test_binance_rows_never_land_in_the_open_licence_files(built):
    results = built["paths"].results
    native = list(results.glob("*.csv"))
    assert native
    for f in native:
        df = pd.read_csv(f)
        if "proxy" in df.columns:
            assert not df["proxy"].astype(str).str.startswith("bn_").any(), f.name
    for f in built["paths"].binance_results.glob("*.csv"):
        df = pd.read_csv(f)
        if f.name != "primary_family.csv":
            assert df["proxy"].astype(str).str.startswith("bn_").all(), f.name


def test_positive_control_and_cross_venue_agreement_are_found(built):
    tables = built["res"]["tables"]
    pc = tables["positive_control"]
    assert (pc["rho"] > 0).all()  # momentum against ni(trend): trend followers buy after up days
    cv = tables["cross_venue"].set_index("proxy")
    assert cv.loc["bn_global_ls", "rho"] > 0.3  # the synthetic Binance ratio is built from the crowd's imbalance


def test_transfer_reports_power_and_both_pairings(built):
    t = built["res"]["transfer"]
    for pairing in ("settlement", "publication"):
        assert t[pairing]["n_obs"] > 0
        assert 0.0 <= t[pairing]["power"] <= 1.0
        table = t[pairing]["table"]
        assert list(table["proxy"]) == list(P.equity_proxies)
    finra_files = sorted(f.name for f in built["paths"].finra_results.iterdir())
    assert finra_files == ["transfer.json", "transfer_publication.csv", "transfer_settlement.csv"]


def test_cot_bridges_cover_the_mapped_futures(built):
    summary = pd.read_csv(built["paths"].results / "cot_bridges.csv")
    assert set(summary["market"]) == {"xyz:GOLD", "BTC"}
    am = pd.read_csv(built["paths"].results / "cot_bridges_asset_managers.csv")
    assert set(am["market"]) == {"BTC"}


def test_manifest_hashes_every_published_file(built):
    results = built["paths"].results
    manifest = built["manifest"]
    on_disk = {f.relative_to(results).as_posix() for f in results.rglob("*") if f.is_file()} - {"manifest.json"}
    assert set(manifest["results_sha256"]) == on_disk
    for name, digest in manifest["results_sha256"].items():
        assert pipeline.sha256_file(results / name) == digest


def test_scoring_refuses_to_run_without_the_addendum(built, tmp_path):
    with pytest.raises(RuntimeError, match="addendum"):
        pipeline.stage_score(built["paths"], P, addendum=tmp_path / "missing.md")
    wrong = tmp_path / "wrong.md"
    wrong.write_text("| Entity map SHA-256 | `" + "0" * 64 + "` |\n", encoding="utf-8")
    with pytest.raises(RuntimeError, match="addendum"):
        pipeline.stage_score(built["paths"], P, strict_addendum=True, addendum=wrong)


def test_truth_refuses_an_incomplete_entity_map(built, tmp_path):
    src = built["paths"]
    partial = pipeline.Paths(src.raw, tmp_path / "derived", tmp_path / "results")
    partial.derived.mkdir()
    targets = (src.derived / "entity_map_targets.parquet").read_bytes()
    (partial.derived / "entity_map_targets.parquet").write_bytes(targets)
    lines = (src.derived / "entity_roles.jsonl").read_text(encoding="utf-8").splitlines()
    (partial.derived / "entity_roles.jsonl").write_text("\n".join(lines[:2]) + "\n", encoding="utf-8")
    _, info = pipeline.load_entity_map(partial)
    assert not info["complete"]
    with pytest.raises(RuntimeError, match="incomplete"):
        pipeline.stage_truth(partial, P)


def test_analysis_never_downloads_missing_inputs(built, tmp_path):
    empty = pipeline.Paths(tmp_path / "raw", built["paths"].derived, tmp_path / "results")
    with pytest.raises(FileNotFoundError, match="herdbench fetch"):
        pipeline.load_funding(empty, date(2026, 1, 1), date(2026, 2, 1))


def test_addendum_records_the_map_hash_without_any_address(built, tmp_path):
    text = pipeline.addendum_markdown(built["paths"], P, recorded_at="2026-01-01 00:00 UTC")
    f = tmp_path / "addendum.md"
    f.write_text(text, encoding="utf-8")
    assert pipeline.registered_map_hash(f) == built["info"]["map_sha256"]
    assert not ADDRESS.search(text.replace(built["info"]["map_sha256"], ""))
    assert "before any proxy-versus-truth" in text
