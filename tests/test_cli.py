"""The command line, run in-process against the synthetic archive."""

from __future__ import annotations

import json

import polars as pl
import pytest

from herdbench import api, cli


@pytest.fixture
def bundled(built, monkeypatch):
    results = built["paths"].results
    monkeypatch.setenv("HERDBENCH_PANEL", str(results / "truth_panel.parquet"))
    monkeypatch.setenv("HERDBENCH_PROXIES", str(results / "proxies.parquet"))
    api._read.cache_clear()
    return built


def test_info_reports_the_panel(bundled, capsys):
    assert cli.main(["info"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["rows"] > 0
    assert "non_mm" in out["cohorts"]


@pytest.mark.parametrize("suffix", [".csv", ".parquet"])
def test_panel_export_filters_by_cohort(bundled, tmp_path, suffix):
    out = tmp_path / f"panel{suffix}"
    assert cli.main(["panel", "--out", str(out), "--cohort", "trend", "--eligible"]) == 0
    df = pl.read_csv(out) if suffix == ".csv" else pl.read_parquet(out)
    assert set(df["cohort"].unique()) == {"trend"}


def test_panel_export_rejects_unknown_formats(bundled, tmp_path):
    with pytest.raises(SystemExit):
        cli.main(["panel", "--out", str(tmp_path / "panel.xlsx")])


def test_grade_reads_a_file_and_writes_json(bundled, tmp_path, capsys):
    panel = pl.read_parquet(bundled["paths"].results / "truth_panel.parquet")
    sig = (
        panel.filter(pl.col("cohort") == "non_mm")
        .select(["date", "market", pl.col("ai").alias("my_signal")])
        .to_pandas()
    )
    f = tmp_path / "signal.csv"
    sig.to_csv(f, index=False)
    out = tmp_path / "report.json"
    assert cli.main(["grade", str(f), "--reps", "20", "--baseline", "premium_24h", "--json", str(out)]) == 0
    printed = capsys.readouterr().out
    assert "in-sample, published panel" in printed
    rep = json.loads(out.read_text(encoding="utf-8"))
    assert [r["signal"] for r in rep["table"]] == ["signal", "premium_24h"]


def test_doctor_lists_inputs(bundled, capsys):
    root = bundled["root"]
    assert cli.main(["doctor", "--data", str(root)]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["snapshots"] == "present"
    assert out["entity_map"] == "present"


def test_build_gates_reuses_the_cached_reconciliation(bundled, capsys):
    assert cli.main(["build", "gates", "--data", str(bundled["root"])]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["reconciliation"]["passed"]


def test_report_command_writes_markdown(bundled, tmp_path):
    out = tmp_path / "results.md"
    assert cli.main(["report", "--results", str(bundled["paths"].results), "--out", str(out)]) == 0
    assert out.read_text(encoding="utf-8").startswith("# Results")


def test_version_flag(capsys):
    with pytest.raises(SystemExit) as e:
        cli.main(["--version"])
    assert e.value.code == 0
    assert "herdbench" in capsys.readouterr().out


def test_grade_rejects_unknown_file_types(bundled, tmp_path):
    f = tmp_path / "signal.json"
    f.write_text("{}", encoding="utf-8")
    with pytest.raises(SystemExit):
        cli.main(["grade", str(f)])
