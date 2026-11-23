"""Render ``docs/results.md`` and its figures from a results folder (``herdbench report``).

Every number in the report is read from ``results/``; nothing is typed by hand except the Deviations
section, which is included from ``docs/deviations.md``. Figures are forest plots (estimate and 95% interval)
written as light and dark SVGs; each figure sits next to a table with the same numbers. Figures need the
``report`` extra (matplotlib); without it the report is rendered with tables only.
"""

from __future__ import annotations

import json
import logging
import math
from collections.abc import Sequence
from pathlib import Path
from types import SimpleNamespace

import pandas as pd

LIGHT = {
    "surface": "#fcfcfb",
    "ink": "#0b0b0b",
    "ink2": "#52514e",
    "muted": "#898781",
    "grid": "#e1e0d9",
    "axis": "#c3c2b7",
    "band": "#f0efec",
    "s1": "#2a78d6",
    "s2": "#eb6834",
}
DARK = {
    "surface": "#1a1a19",
    "ink": "#f0efec",
    "ink2": "#c3c2b7",
    "muted": "#898781",
    "grid": "#2c2c2a",
    "axis": "#383835",
    "band": "#383835",
    "s1": "#3987e5",
    "s2": "#d95926",
}


# ------------------------------------------------------------------------------------------ formatting
def num(x, nd: int = 3) -> str:
    if x is None or (isinstance(x, float) and math.isnan(x)):
        return "–"
    if isinstance(x, (bool,)):
        return "yes" if x else "no"
    if isinstance(x, (int,)) and not isinstance(x, bool):
        return f"{x:,}"
    return f"{float(x):.{nd}f}"


def pval(x) -> str:
    if x is None or (isinstance(x, float) and math.isnan(x)):
        return "–"
    return "<0.001" if x < 0.001 else f"{x:.3f}"


def interval(lo, hi, nd: int = 3) -> str:
    if any(v is None or (isinstance(v, float) and math.isnan(v)) for v in (lo, hi)):
        return "–"
    return f"[{lo:.{nd}f}, {hi:.{nd}f}]"


def table(rows: Sequence[Sequence[str]], header: Sequence[str], align: str | None = None) -> str:
    align = align or "l" + "r" * (len(header) - 1)
    marks = {"l": ":---", "r": "---:", "c": ":---:"}
    out = ["| " + " | ".join(header) + " |", "|" + "|".join(marks[a] for a in align) + "|"]
    out += ["| " + " | ".join(str(c) for c in r) + " |" for r in rows]
    return "\n".join(out)


def _records(df: pd.DataFrame) -> list[SimpleNamespace]:
    """Rows as attribute objects (``r.rho``), without pandas' named-tuple typing."""
    return [SimpleNamespace(**{str(k): v for k, v in r.items()}) for r in df.to_dict(orient="records")]


def _read_csv(path: Path) -> pd.DataFrame | None:
    return pd.read_csv(path) if path.exists() else None


def _read_json(path: Path) -> dict | None:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


# ------------------------------------------------------------------------------------------ figures
def _stem(figures: Path | None, name: str) -> Path | None:
    return figures / name if figures is not None else None


def forest(
    rows: Sequence[dict],
    stem: Path | None,
    title: str,
    xlabel: str,
    band: float | None = None,
    series: Sequence[str] = ("",),
) -> str | None:
    """Dot-and-interval chart, one row per label; ``rows``: label, series, est, lo, hi, note. Returns the
    Markdown ``<picture>`` for the light and dark SVGs, or None when matplotlib is not installed."""
    if stem is None:
        return None
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        return None
    logging.getLogger("matplotlib.font_manager").setLevel(logging.ERROR)  # font fallback is expected
    labels = list(dict.fromkeys(r["label"] for r in rows))
    stem.parent.mkdir(parents=True, exist_ok=True)
    multi = len(series) > 1
    for mode, c in (("light", LIGHT), ("dark", DARK)):
        plt.rcParams.update(
            {
                "svg.fonttype": "none",
                "svg.hashsalt": "herdbench",
                "font.family": ["Segoe UI", "Helvetica Neue", "DejaVu Sans", "sans-serif"],
            }
        )
        height = 0.9 + 0.42 * len(labels) + (0.3 if multi else 0)
        fig, ax = plt.subplots(figsize=(7.6, height), dpi=100)
        fig.patch.set_facecolor(c["surface"])
        ax.set_facecolor(c["surface"])
        if band:
            ax.axvspan(-band, band, color=c["band"], zorder=0, lw=0)
        ax.axvline(0, color=c["axis"], lw=1, zorder=1)
        ax.grid(axis="x", color=c["grid"], lw=1, zorder=0)
        ax.set_axisbelow(True)
        colors = [c["s1"], c["s2"]]
        for k, name in enumerate(series):
            pts = [r for r in rows if r.get("series", "") == name]
            off = (k - (len(series) - 1) / 2) * 0.22 if multi else 0.0
            for r in pts:
                y = labels.index(r["label"]) + off
                if not any(math.isnan(v) for v in (r["lo"], r["hi"])):
                    ax.plot([r["lo"], r["hi"]], [y, y], color=colors[k], lw=2, solid_capstyle="round", zorder=2)
                if not math.isnan(r["est"]):
                    ax.plot(
                        [r["est"]],
                        [y],
                        "o",
                        ms=8,
                        color=colors[k],
                        mec=c["surface"],
                        mew=2,
                        zorder=3,
                        label=name if (multi and r is pts[0]) else None,
                    )
        notes = {r["label"]: r.get("note", "") for r in rows if r.get("note")}
        ax.set_yticks(range(len(labels)))
        ax.set_yticklabels(labels, color=c["ink"], fontsize=10)
        ax.set_ylim(len(labels) - 0.5, -0.5)
        ax.tick_params(axis="x", colors=c["muted"], labelsize=9, length=0)
        ax.tick_params(axis="y", length=0)
        for side in ("top", "right", "left"):
            ax.spines[side].set_visible(False)
        ax.spines["bottom"].set_color(c["axis"])
        ax.set_xlabel(xlabel, color=c["ink2"], fontsize=9)
        lo = min([r["lo"] for r in rows if not math.isnan(r["lo"])] + [-(band or 0.1)])
        hi = max([r["hi"] for r in rows if not math.isnan(r["hi"])] + [band or 0.1])
        pad = 0.08 * (hi - lo)
        ax.set_xlim(lo - pad, hi + pad)
        for label, note in notes.items():
            ax.annotate(
                note,
                xy=(1.0, labels.index(label)),
                xycoords=("axes fraction", "data"),
                xytext=(8, 0),
                textcoords="offset points",
                va="center",
                ha="left",
                color=c["ink2"],
                fontsize=9,
            )
        ax.set_title(title, loc="left", color=c["ink"], fontsize=11, pad=24 if multi else 10)
        if multi:
            leg = ax.legend(
                loc="lower left",
                bbox_to_anchor=(0, 1.0),
                ncol=len(series),
                frameon=False,
                fontsize=9,
                handletextpad=0.3,
                columnspacing=1.2,
            )
            for text in leg.get_texts():
                text.set_color(c["ink2"])
        fig.tight_layout()
        out = stem.with_name(f"{stem.name}-{mode}.svg")
        fig.savefig(out, format="svg", facecolor=c["surface"], metadata={"Date": None}, bbox_inches="tight")
        out.write_bytes(out.read_bytes().replace(b"\r\n", b"\n"))  # LF on every platform, like every other output
        plt.close(fig)
    rel = f"figures/{stem.name}"
    return (
        f'<picture>\n  <source media="(prefers-color-scheme: dark)" srcset="{rel}-dark.svg">\n'
        f'  <img alt="{title}" src="{rel}-light.svg" width="760">\n</picture>'
    )


# ------------------------------------------------------------------------------------------ sections
def _gates(results: Path) -> str:
    g = _read_json(results / "gates.json")
    s = _read_json(results / "score_summary.json") or {}
    if not g:
        return "_Gates were not run._"
    rec, g0, k1 = g["reconciliation"], g["gate0"], g["kill_criterion_1"]
    rows = [
        [
            "Reconciliation (all results)",
            f"{100 * rec['reconciled_share']:.3f}% of changed notional over {rec['pairs']} "
            f"snapshot pairs (market makers {100 * rec['market_maker_share']:.3f}%, everyone else "
            f"{100 * rec['rest_share']:.3f}%; worst pair {100 * rec['worst_pair_share']:.3f}%)",
            "≥ 99%",
            "pass" if rec["passed"] else "FAIL",
        ],
        [
            "Gate 0 (all results)",
            f"maker = taker within 1% on {100 * g0['share_balanced']:.3f}% of "
            f"{g0['market_days']:,} market-days (largest gap {g0['max_gap']:.2e})",
            "≥ 99%",
            "pass" if g0["passed"] else "FAIL",
        ],
        [
            "Kill criterion 1 (reported)",
            f"{k1['count']} US single-stock perps with ≥ 183 days of fills",
            "≥ 25",
            "pass" if k1["passed"] else "fail (not gating)",
        ],
    ]
    for pairing, v in (s.get("kill_criterion_3") or {}).items():
        if v.get("power") is not None:
            rows.append(
                [
                    f"Kill criterion 3 ({pairing} pairing)",
                    f"clustered power {v['power']:.3f} at ρ = 0.2",
                    "≥ 0.80",
                    "pass" if v["passed"] else "fail: estimates only, no p-values",
                ]
            )
    k4 = s.get("kill_criterion_4")
    if k4:
        status = (
            "no ranking claimed (fewer than 3 decisive verdicts)"
            if k4.get("passed") is None
            else ("pass" if k4["passed"] else "fail: no ranking claimed")
        )
        tau = "–" if k4.get("tau") is None else f"{k4['tau']:.3f}"
        rows.append(
            ["Kill criterion 4 (ranking claims)", f"Kendall τ between half-sample rankings {tau}", "≥ 0.40", status]
        )
    note = "" if g.get("label") is None else f'\n\n**All results are labelled "{g["label"]}".**'
    short = rec.get("pairs_below_threshold") or []
    if short:
        listed = ", ".join(f"{p['date']} ({100 * p['reconciled_share']:.1f}%)" for p in short)
        note += (
            f"\n\nSnapshot pairs below 99% on their own: {listed}. Every other pair reconciles in full; a short pair"
            " points to fills missing inside an archived day."
        )
    return table(rows, ["Check", "Result", "Bar", "Status"], "llll") + note


def _primary(results: Path, figures: Path | None) -> str:
    b = results / "binance"
    t = _read_csv(b / "primary_family.csv")
    meta = _read_json(b / "primary_family.json")
    if t is None or meta is None:
        return "_The primary family was not scored._"
    t = t.sort_values("rank") if "rank" in t.columns else t
    rows = []
    for r in _records(t):
        rank = (
            f"{int(r.rank)} [{int(r.rank_lo)}–{int(r.rank_hi)}]"
            if not pd.isna(getattr(r, "rank", float("nan")))
            else "–"
        )
        rows.append(
            [
                rank,
                f"`{r.proxy}`",
                num(r.rho),
                interval(r.ci_low, r.ci_high),
                pval(r.p_holm),
                num(r.rot95),
                num(r.fe_beta),
                f"**{r.verdict}**",
                num(getattr(r, "ai_all_rho", float("nan"))),
            ]
        )
    fig = forest(
        [{"label": r.proxy, "est": r.rho, "lo": r.ci_low, "hi": r.ci_high, "note": r.verdict} for r in _records(t)],
        _stem(figures, "primary"),
        "Primary family: proxy vs ni(non_mm)",
        "Mean within-market Spearman ρ with 95% block-bootstrap interval (shaded: ±0.10)",
        band=0.10,
    )
    stab = meta.get("stability", {})
    rerun = meta.get("published_panel_rerun", {})
    rr = pd.DataFrame(rerun.get("table") or [])
    rerun_txt = ""
    if not rr.empty:
        pairs = ", ".join(f"`{p}` {num(v)}" for p, v in zip(rr["proxy"], rr["rho"], strict=True))
        rerun_txt = (
            f"\n\nRerun on the published panel only ({rerun.get('label')}): {pairs}. Flag: "
            f"**{rerun.get('flag')}** ({100 * rerun.get('primary_suppressed_share', float('nan')):.1f}% of "
            f"primary market-days suppressed; the flag trips above 5%)."
        )
    unstable = ", ".join(f"`{u}`" for u in stab.get("unstable", [])) or "none"
    wins = meta.get("dominance_claims") or []
    if wins:
        claims = (
            "Ranking claims (A outranks B in at least 95% of draws): "
            + "; ".join(f"`{w['winner']}` beats `{w['loser']}` ({100 * w['share_of_draws']:.1f}%)" for w in wins)
            + "."
        )
    elif stab.get("ranking_claimed"):
        claims = "No pair clears the 95% dominance bar, so no pairwise ranking claim is made."
    else:
        claims = "No ranking is claimed (kill criterion 4); the ranks above are descriptive."
    text = [
        f"{len(meta['markets'])} markets, {meta['n_common_days']} common dates (a market-day is dropped for every "
        f"proxy if any proxy or the truth is missing), block length {meta['block_length']} days, "
        f"{len(t)} proxies Holm-corrected together.",
        "",
        fig or "",
        "",
        table(
            rows,
            ["Rank [95%]", "Proxy", "ρ", "95% CI", "Holm p", "Rotation 95%", "FE slope", "Verdict", "ai(all) ρ"],
            "llrrrrrlr",
        ),
        "",
        f"Stability (half-samples split at {meta['half_split']}): unstable verdicts: {unstable}. {claims}" + rerun_txt,
    ]
    if meta.get("label"):
        text.insert(0, f"> **{meta['label']}**: a gate failed, so ai(all) is the headline check (column ai(all) ρ).\n")
    return "\n".join(text)


def _checks(results: Path, figures: Path | None) -> str:
    parts, fig_rows = [], []
    pc = _read_csv(results / "positive_control.csv")
    cv = _read_csv(results / "binance" / "cross_venue.csv")
    rows = []
    if pc is not None:
        for r in _records(pc):
            rows.append(
                [
                    f"Positive control, {r.panel} panel",
                    "`momentum_7d` vs ni(trend)",
                    num(r.rho),
                    interval(r.ci_low, r.ci_high),
                    num(r.n_markets) if not pd.isna(r.n_markets) else "–",
                    "above 0 as required" if r.rho > 0 else "**at or below 0: pipeline error flag**",
                ]
            )
            fig_rows.append(
                {"label": f"momentum_7d vs ni(trend), {r.panel}", "est": r.rho, "lo": r.ci_low, "hi": r.ci_high}
            )
    if cv is not None:
        for r in _records(cv):
            rows.append(
                [
                    "Cross-venue agreement",
                    f"`{r.proxy}` vs {r.truth}({r.cohort})",
                    num(r.rho),
                    interval(r.ci_low, r.ci_high),
                    num(int(r.n_markets)),
                    "",
                ]
            )
            fig_rows.append(
                {"label": f"{r.proxy} vs {r.truth}({r.cohort})", "est": r.rho, "lo": r.ci_low, "hi": r.ci_high}
            )
    if not rows:
        return "_Not run._"
    fig = forest(
        fig_rows,
        _stem(figures, "checks"),
        "Positive control and cross-venue agreement",
        "Mean within-market Spearman ρ with 95% interval",
    )
    parts += [fig or "", "", table(rows, ["Check", "Pair", "ρ", "95% CI", "Markets", "Reading"], "llrrrl")]
    return "\n".join(parts)


def _grid(results: Path, name: str, cohort: str = "non_mm", truths: Sequence[str] = ("ni", "ai", "lwi")) -> str:
    frames = [
        f for f in (_read_csv(results / f"{name}.csv"), _read_csv(results / "binance" / f"{name}.csv")) if f is not None
    ]
    if not frames:
        return "_Not run._"
    df = pd.concat(frames, ignore_index=True)
    df = df[(df["cohort"] == cohort) & (df["truth"].isin(truths))]
    if df.empty:
        return "_No rows._"
    rows = []
    for r in df.sort_values(["truth", "rho"], ascending=[True, False]).itertuples():
        rows.append(
            [
                f"`{r.proxy}`",
                r.truth,
                num(r.rho),
                interval(r.ci_low, r.ci_high),
                num(r.xs_rho),
                num(r.changes_rho),
                num(r.placebo_rho),
                num(getattr(r, "sign_agreement", float("nan"))),
                "yes" if r.bh_flag else "no",
            ]
        )
    return table(
        rows,
        ["Proxy", "Truth", "ρ", "95% CI", "Cross-section ρ", "Changes ρ", "Placebo ρ", "Sign agreement", "BH flag"],
        "llrrrrrrl",
    )


def _sensitivity(results: Path) -> str:
    t = _read_csv(results / "sensitivity_complete_flows.csv")
    if t is None:
        return "_Not run._"
    main = pd.concat(
        [
            f
            for f in (_read_csv(results / "unsigned_primary.csv"), _read_csv(results / "unsigned_all_markets.csv"))
            if f is not None
        ],
        ignore_index=True,
    )
    rows = []
    for r in _records(t[t["cohort"] == "non_mm"]):
        hit = (
            main[
                (main["panel"] == r.panel)
                & (main["proxy"] == r.proxy)
                & (main["truth"] == r.truth)
                & (main["cohort"] == r.cohort)
            ]
            if not main.empty
            else main
        )
        reg = hit.iloc[0] if len(hit) else None
        rows.append(
            [
                r.panel,
                f"`{r.proxy}`",
                r.truth,
                num(reg["rho"]) if reg is not None else "–",
                num(r.rho),
                interval(r.ci_low, r.ci_high),
                num(int(r.n_obs)),
            ]
        )
    return table(rows, ["Panel", "Proxy", "Truth", "ρ (registered)", "ρ (complete windows)", "95% CI", "N"], "lllrrrr")


def _liquidations(results: Path) -> str:
    t = _read_csv(results / "liquidation_target.csv")
    d = _read_json(results / "liquidation_delta_rho.json")
    if t is None:
        return "_Not run._"
    rows = [
        [
            f"`{r.predictor}`",
            r.kind,
            r.target,
            num(r.rho),
            num(r.se),
            pval(r.p_value),
            num(r.partial_rho_rv),
            "yes" if r.bh_flag is True else ("no" if r.bh_flag is False else "–"),
            getattr(r, "variant", "registered"),
        ]
        for r in _records(t)
    ]
    out = [
        table(
            rows,
            ["Predictor", "Kind", "Target", "Mean daily ρ", "NW s.e.", "p", "Partial ρ (rv_7d)", "BH flag", "Variant"],
            "lllrrrrll",
        )
    ]
    if d:
        drow = [
            [c["picked_on"], c["scored_on"], f"`{c['proxy']}`", num(c["delta"]), interval(c["ci_low"], c["ci_high"])]
            for c in d["comparisons"]
        ]
        out += [
            "",
            "Δρ: fragile_share minus the best unsigned proxy (chosen on one half, scored on the other):",
            "",
            table(drow, ["Chosen on", "Scored on", "Best proxy", "Δρ", "95% CI"], "lllrr"),
        ]
    return "\n".join(out)


def _transfer(results: Path, figures: Path | None) -> str:
    f = results / "finra"
    meta = _read_json(f / "transfer.json")
    if not meta:
        return "_Not run._"
    out, fig_rows = [], []
    for pairing in ("settlement", "publication"):
        t = _read_csv(f / f"transfer_{pairing}.csv")
        m = meta.get(pairing, {})
        if t is None or not m.get("n_obs"):
            continue
        out += [
            f"**{pairing.capitalize()} pairing**: {m['n_stocks']} stocks, {m['n_periods']} settlement dates, "
            f"{m['n_obs']} stock-dates; clustered power {m['power']:.3f} "
            f"({'p-values reported' if m['p_values_reported'] else 'below 0.80, so estimates and intervals only'}).",
            "",
        ]
        rows = [
            [
                f"`{r.proxy}`",
                "−" if r.expected_sign < 0 else "+",
                num(r.beta),
                interval(r.ci_low, r.ci_high),
                pval(r.p_value),
                num(r.se_twoway),
                num(int(r.n_obs)),
            ]
            for r in _records(t)
        ]
        out += [table(rows, ["Proxy", "Expected", "β (std.)", "95% CI", "p", "Two-way s.e.", "N"], "lcrrrrr"), ""]
        fig_rows += [
            {"label": r.proxy, "series": pairing, "est": r.beta, "lo": r.ci_low, "hi": r.ci_high} for r in _records(t)
        ]
    if fig_rows:
        fig = forest(
            fig_rows,
            _stem(figures, "transfer"),
            "US single-stock perps: equity-side proxies vs ni(non_mm)",
            "Two-way fixed-effects slope (standardised) with 95% interval, clustered by stock",
            series=("settlement", "publication"),
        )
        out = [fig or "", "", *out]
    return "\n".join(out) + f"\n\n{meta.get('source', '')}"


def _bridges(results: Path) -> str:
    out = []
    for name, label in (
        ("cot_bridges", "Managed money (metals, energy) or leveraged funds (indices, FX, CME crypto)"),
        ("cot_bridges_asset_managers", "Asset managers (financial futures only)"),
    ):
        t = _read_csv(results / f"{name}.csv")
        if t is None:
            continue
        rows = [[r.market, num(int(r.n_weeks)), num(r.spearman), pval(r.p_value)] for r in _records(t)]
        out += [f"**{label}**", "", table(rows, ["Perp", "Weeks", "Spearman ρ", "p (descriptive)"], "lrrr"), ""]
    return "\n".join(out) or "_Not run._"


def _robustness(results: Path) -> str:
    frames = [
        f
        for f in (
            _read_csv(results / "robustness_cohorts.csv"),
            _read_csv(results / "binance" / "robustness_cohorts.csv"),
        )
        if f is not None
    ]
    if not frames:
        return "_Not run._"
    df = pd.concat(frames, ignore_index=True)
    piv = df.pivot_table(index="proxy", columns="cohort", values="rho", aggfunc="first")
    rows = [[f"`{p}`", *[num(piv.loc[p, c]) for c in piv.columns]] for p in piv.index]
    return table(rows, ["Proxy", *list(piv.columns)], "l" + "r" * len(piv.columns))


def _coverage(results: Path) -> str:
    s = _read_json(results / "truth_panel_summary.json")
    if not s:
        return "_The truth panel was not built._"
    rows = [
        ["Snapshot dates", f"{s['dates']} ({s['first_date']} to {s['last_date']})"],
        ["Markets (eligible)", f"{s['markets']} ({s['eligible_markets']})"],
        ["Cohort cells (eligible)", f"{s['cells']:,} ({s['eligible_cells']:,})"],
        ["Eligible cells suppressed for publication", f"{100 * s['eligible_suppressed_share']:.1f}%"],
        ["Primary market-days (non_mm, primary panel)", f"{s['primary_market_days']:,}"],
        [
            "…of which suppressed",
            f"{100 * s['primary_suppressed_share']:.1f}% "
            f"({100 * s['primary_suppressed_oi_share']:.1f}% of their open interest)",
        ],
        ["Published rows", f"{s.get('published_rows', 0):,}"],
    ]
    return table(rows, ["Truth panel", "Value"], "ll")


def render(results: Path, figures: Path | None = None, deviations: Path | None = None) -> str:
    """The full Markdown report. Figures are written to ``figures`` when it is given (``herdbench report`` puts
    them next to the report); without it the report has tables only."""
    results = Path(results)
    root = Path(__file__).resolve().parents[2]
    deviations = deviations or root / "docs" / "deviations.md"
    manifest = _read_json(results / "manifest.json") or {}
    dev = deviations.read_text(encoding="utf-8").split("\n", 1)[1].strip() if deviations.exists() else "None."
    lines = [
        "# Results",
        "",
        "<!-- Generated by `herdbench report` from results/. Do not edit by hand: edit docs/deviations.md. -->",
        "",
        "Every number below is read from the files in `results/`, whose SHA-256 hashes are listed in "
        "`results/manifest.json`"
        + (f" (built {manifest['built_at']} with herdbench {manifest['herdbench_version']})" if manifest else "")
        + ". The design was registered before any proxy was compared with the truth "
        "([pre-registration](preregistration.md), [addendum](preregistration_addendum.md)).",
        "",
        "## 1. Integrity gates and kill criteria",
        "",
        _gates(results),
        "",
        "## 2. The truth panel",
        "",
        _coverage(results),
        "",
        "## 3. Primary family: which public proxies track net positioning?",
        "",
        "Truth: ni(non_mm), the net position of every account except market makers and protocol accounts, as a share "
        "of their gross position. Verdicts follow the pre-registration: *confirmed* needs the expected sign, Holm "
        "p < 0.05, |z̄| above the rotation null's 95th percentile and a fixed-effects slope of the same sign; "
        "*uninformative* means the 90% interval lies inside ±0.10.",
        "",
        _primary(results, figures),
        "",
        "## 4. Positive control and cross-venue agreement",
        "",
        _checks(results, figures),
        "",
        "## 5. Secondary statistics (exploratory)",
        "",
        "All-market panel (eligible main-dex markets with at least 60 days), cohort non_mm. Rows are flagged with "
        "Benjamini–Hochberg at q = 0.05 within each grid. Full grids, every cohort: `results/*.csv` and "
        "`results/binance/*.csv`.",
        "",
        "**Directional proxies**",
        "",
        _grid(results, "directional_all_markets"),
        "",
        "**Crowding (unsigned) proxies**",
        "",
        _grid(results, "unsigned_all_markets", truths=("abs_ni", "top10_share", "fragile_share", "coheld_share")),
        "",
        "**Sensitivity: flow proxies on complete 7-day windows** (cohort non_mm; see Deviations)",
        "",
        _sensitivity(results),
        "",
        "## 6. Second target: liquidations in the next 7 days",
        "",
        _liquidations(results),
        "",
        "## 7. Transfer to US single-stock perpetuals",
        "",
        _transfer(results, figures),
        "",
        "## 8. Futures bridges (description only)",
        "",
        _bridges(results),
        "",
        "## 9. Robustness: market-maker rule variants (primary panel, ni)",
        "",
        _robustness(results),
        "",
        "## 10. Deviations from the pre-registration",
        "",
        dev,
        "",
    ]
    return "\n".join(lines)
