"""Command line: ``herdbench <command>``. Run ``herdbench <command> --help`` for the options of each.

Using the published results (no downloads):
  info      what the bundled truth panel covers
  panel     export the truth panel (CSV or Parquet)
  grade     score your own signal against the panel, with the study's baselines

Reproducing the study (downloads about 25 GB; see docs/reproduce.md):
  fetch     download every input
  build     run pipeline stages: entities, truth, proxies, publish, score, gates, manifest, all
  report    render docs/results.md from a results folder
  visuals   draw the README images (animated SVG) from a results folder, such as a clone's results/
  doctor    check the environment and which inputs are present
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from datetime import date, timedelta
from pathlib import Path

from herdbench import __version__
from herdbench.config import PREREG


def _paths(args):
    from herdbench.pipeline import Paths

    if args.data:
        return Paths.under(Path(args.data), Path(args.results) if args.results else None)
    p = Paths.default()
    return Paths(p.raw, p.derived, Path(args.results)) if args.results else p


def cmd_info(args) -> int:
    import polars as pl

    from herdbench import api

    panel = api.load_truth_panel()
    assert isinstance(panel, pl.DataFrame)
    out = {
        "herdbench": __version__,
        "panel": str(api.panel_path()),
        "rows": panel.height,
        "dates": [str(panel["date"].min()), str(panel["date"].max())],
        "markets": panel["market"].n_unique(),
        "cohorts": sorted(panel["cohort"].unique().to_list()),
        "registered_on": PREREG.registered_on,
        "label": api.IN_SAMPLE_LABEL,
    }
    print(json.dumps(out, indent=1))
    return 0


def cmd_panel(args) -> int:
    import polars as pl

    from herdbench import api

    df = api.load_truth_panel(cohort=args.cohort, markets=args.market or None, eligible_only=args.eligible)
    assert isinstance(df, pl.DataFrame)
    out = Path(args.out)
    if out.suffix == ".parquet":
        df.write_parquet(out)
    elif out.suffix == ".csv":
        df.write_csv(out)
    else:
        raise SystemExit("--out must end in .csv or .parquet")
    print(f"wrote {df.height} rows to {out}")
    return 0


def _read_table(path: Path):
    import pandas as pd

    if path.suffix == ".parquet":
        return pd.read_parquet(path)
    if path.suffix in (".csv", ".txt"):
        return pd.read_csv(path)
    raise SystemExit("the signal file must be .csv or .parquet")


def cmd_grade(args) -> int:
    from herdbench import api

    table = _read_table(Path(args.file))
    baselines: Sequence[str] | bool = False if args.no_baselines else (args.baseline or True)
    report = api.grade(
        table,
        truth=args.truth,
        cohort=args.cohort,
        value=args.value,
        markets=args.market or None,
        expected_sign=args.sign,
        baselines=baselines,
        bootstrap_reps=args.reps,
    )
    print(report)
    if args.json:
        from herdbench.pipeline import write_text

        write_text(Path(args.json), json.dumps(report.to_dict(), indent=1, default=str) + chr(10))
        print(f"wrote {args.json}")
    return 0


def cmd_fetch(args) -> int:
    from herdbench import fetch

    paths = _paths(args)
    raw = paths.raw
    raw.mkdir(parents=True, exist_ok=True)
    start, end = date.fromisoformat(PREREG.evaluation_start), date.fromisoformat(PREREG.evaluation_end)
    steps = args.only or ["mirror", "funding", "binance", "finra", "cftc"]
    if "mirror" in steps:
        print("mirror (snapshots, fills):", fetch.fetch_mirror(raw, workers=args.workers))
    if "funding" in steps:
        print("funding months:", fetch.fetch_funding(raw, date(2025, 7, 1), end))
    if "binance" in steps:
        from herdbench import pipeline

        panel = pipeline.primary_panel(paths)
        symbols = list(panel["binance_symbols"].values())
        print("binance:", fetch.fetch_binance(raw, symbols, start - timedelta(days=8), end))
    if "finra" in steps:
        print("finra settlement dates:", len(fetch.fetch_finra(raw, date(2025, 11, 1), date(2026, 8, 31))))
    if "cftc" in steps:
        print("cftc series:", fetch.fetch_cftc(raw, date(2025, 7, 1), date(2026, 8, 31)))
    return 0


def cmd_build(args) -> int:
    from herdbench import pipeline

    paths = _paths(args)
    stage = args.stage
    if stage == "panel-selection":
        inp = pipeline.stage_inputs(paths)
        sel = pipeline.select_primary_panel(inp.markets)
        pipeline.write_json(paths.derived / "primary_panel.json", sel)
        print(json.dumps(sel, indent=1))
    elif stage == "entities":
        _, info = pipeline.stage_entities(paths)
        print(json.dumps(info, indent=1))
    elif stage == "truth":
        cells = pipeline.stage_truth(paths)
        print(f"truth cells: {cells.height}")
    elif stage == "proxies":
        print(f"proxy rows: {pipeline.stage_proxies(paths).height}")
    elif stage == "publish":
        print(f"published rows: {pipeline.stage_publish(paths, ship=args.ship).height}")
    elif stage == "addendum":
        out = pipeline.ADDENDUM
        if out.exists() and not args.force:
            raise SystemExit(f"{out} exists and is part of the registered record; use --force to overwrite")
        pipeline.write_text(out, pipeline.addendum_markdown(paths))
        print(f"wrote {out}; commit and timestamp it before `herdbench build score`")
    elif stage == "score":
        pipeline.stage_score(paths, strict_addendum=args.strict)
        print(f"results written to {paths.results}")
    elif stage == "gates":
        print(json.dumps(pipeline.stage_gates(paths), indent=1, default=str))
    elif stage == "manifest":
        print(json.dumps(pipeline.write_manifest(paths), indent=1)[:2000])
    elif stage == "all":
        pipeline.run_all(paths, strict_addendum=args.strict)
        print(f"results written to {paths.results}")
    return 0


def cmd_report(args) -> int:
    from herdbench import report
    from herdbench.config import results_dir

    out = Path(args.out)
    figures = Path(args.figures) if args.figures else out.parent / "figures"
    text = report.render(Path(args.results) if args.results else results_dir(), figures=figures)
    from herdbench.pipeline import write_text

    write_text(out, text)
    print(f"wrote {args.out}")
    return 0


def cmd_visuals(args) -> int:
    from herdbench import visuals
    from herdbench.config import results_dir

    results = Path(args.results) if args.results else results_dir()
    if not (results / "score_summary.json").exists():
        raise SystemExit(
            f"no results in {results}: pass --results, or run from a clone of the repository, whose results/ "
            "folder holds the published results"
        )
    written = visuals.build(results, Path(args.out))
    print(f"wrote {len(written)} images to {args.out}")
    return 0


def cmd_check(args) -> int:
    from herdbench import pipeline

    out = pipeline.check_funding(_paths(args), markets=args.market or pipeline.CHECK_MARKETS, days=args.days)
    print(json.dumps(pipeline.jsonable(out), indent=1))
    return 0 if out["hours_compared"] and out["share_equal"] == 1.0 else 1


def cmd_doctor(args) -> int:
    import platform

    from herdbench import api

    paths = _paths(args)
    checks = {"python": platform.python_version(), "herdbench": __version__}
    try:
        checks["bundled_panel"] = str(api.panel_path())
    except FileNotFoundError as e:
        checks["bundled_panel"] = f"missing ({e})"
    for name, p in {
        "snapshots": paths.hf / "perp_archive",
        "fills": paths.hf / "fills_slim",
        "funding": paths.raw / "ironflow",
        "binance": paths.raw / "binance",
        "finra": paths.raw / "finra",
        "cftc": paths.raw / "cftc",
        "entity_map": paths.derived / "entity_roles.jsonl",
    }.items():
        checks[name] = "present" if p.exists() else "absent"
    print(json.dumps(checks, indent=1))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="herdbench", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--version", action="version", version=f"herdbench {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    def data_opts(p):
        p.add_argument("--data", help="data folder holding raw/ and derived/ (default: HERDBENCH_DATA_DIR or ./data)")
        p.add_argument("--results", help="results folder (default: HERDBENCH_RESULTS_DIR or ./results)")

    sub.add_parser("info", help="what the bundled truth panel covers").set_defaults(func=cmd_info)

    p = sub.add_parser("panel", help="export the truth panel")
    p.add_argument("--out", required=True, help="output file (.csv or .parquet)")
    p.add_argument("--cohort", choices=PREREG.cohorts)
    p.add_argument("--market", action="append", help="keep only this market (repeatable)")
    p.add_argument("--eligible", action="store_true", help="only market-days with open interest >= $1M the day before")
    p.set_defaults(func=cmd_panel)

    p = sub.add_parser("grade", help="score your own signal against the panel")
    p.add_argument("file", help="CSV or Parquet: columns date, market and a value (or wide: date then markets)")
    p.add_argument("--value", help="name of the value column (when the file has several)")
    p.add_argument("--truth", default="ni", help="truth metric (default ni)")
    p.add_argument("--cohort", default="non_mm", choices=PREREG.cohorts)
    p.add_argument("--market", action="append", help="restrict to this market (repeatable)")
    p.add_argument("--sign", type=int, default=1, choices=(1, -1), help="expected sign of the relation")
    p.add_argument("--baseline", action="append", help="baseline proxy to compare with (repeatable)")
    p.add_argument("--no-baselines", action="store_true", help="score the signal alone")
    p.add_argument("--reps", type=int, help="bootstrap replications (default 2000)")
    p.add_argument("--json", help="also write the full report as JSON")
    p.set_defaults(func=cmd_grade)

    p = sub.add_parser("fetch", help="download every input of the study")
    data_opts(p)
    p.add_argument("--only", action="append", choices=("mirror", "funding", "binance", "finra", "cftc"))
    p.add_argument("--workers", type=int, default=2)
    p.set_defaults(func=cmd_fetch)

    p = sub.add_parser("build", help="run a pipeline stage")
    data_opts(p)
    p.add_argument(
        "stage",
        choices=(
            "panel-selection",
            "entities",
            "truth",
            "proxies",
            "publish",
            "addendum",
            "score",
            "gates",
            "manifest",
            "all",
        ),
    )
    p.add_argument("--ship", action="store_true", help="publish: also copy the panel into the installed package")
    p.add_argument("--strict", action="store_true", help="score/all: require the registered entity-map hash")
    p.add_argument("--force", action="store_true", help="addendum: overwrite an existing addendum")
    p.set_defaults(func=cmd_build)

    p = sub.add_parser("report", help="render the results report")
    p.add_argument("--results", help="results folder (default: HERDBENCH_RESULTS_DIR or ./results)")
    p.add_argument("--out", default="docs/results.md")
    p.add_argument("--figures", help="folder for the figures (default: figures/ next to --out)")
    p.set_defaults(func=cmd_report)

    p = sub.add_parser("visuals", help="draw the README images from a results folder")
    p.add_argument("--results", help="results folder (default: HERDBENCH_RESULTS_DIR or ./results)")
    p.add_argument("--out", default="docs/visuals", help="folder for the images (default: docs/visuals)")
    p.set_defaults(func=cmd_visuals)

    p = sub.add_parser("check", help="cross-check the funding inputs against Hyperliquid's API (network)")
    data_opts(p)
    p.add_argument("--market", action="append", help="market to sample (repeatable)")
    p.add_argument("--days", type=int, default=2, help="days per market (default 2)")
    p.set_defaults(func=cmd_check)

    p = sub.add_parser("doctor", help="check the environment and inputs")
    data_opts(p)
    p.set_defaults(func=cmd_doctor)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return int(args.func(args) or 0)


if __name__ == "__main__":
    sys.exit(main())
