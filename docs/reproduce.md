# Reproducing the results

This page shows how to rebuild every published number from public data: the files in `results/` and the report
generated from them, `docs/results.md`. For the numbers themselves, see [docs/results.md](results.md). The inputs and their
terms are described in [docs/data.md](data.md), the method in [docs/methodology.md](methodology.md).

Below, the *results folder* is the folder your build writes to. `results/` is the folder published in the
repository.

The whole sequence, from the root of a clone:

```bash
pip install -e ".[report]"
export HERDBENCH_DATA_DIR="$HOME/herdbench-data"       # raw inputs and private tables
export HERDBENCH_RESULTS_DIR="$HOME/herdbench-rerun"   # keeps the published results/ intact
herdbench fetch                                        # every input, about 25 GB
herdbench build entities                               # entity map: several hours, resumable
herdbench build all --strict                           # truth, gates, proxies, publish, score, manifest
herdbench check                                        # optional: funding inputs against the venue's API
herdbench report --out rerun/results.md                # a report from HERDBENCH_RESULTS_DIR, figures next to it
```

The rest of this page explains each step, what it reads and writes, how long it takes, and how to check your
output against `results/manifest.json`.

## What is fixed and what is not

| Element | How it is fixed |
|---|---|
| Hyperliquid snapshots and fills | Hugging Face mirror `craftify2221/hyperliquid-fills-raw` at revision `de05dc101c819aa68accfefd6647b8bf6de02189` (`PREREG.mirror_revision`). Every request names the revision. Each snapshot must match the SHA-256 that the Hub publishes for it. |
| Funding and premium | Ironflow `hyperliquid-funding` at revision `b2153cf3aed73b6b200a378064538a2263923e9d` (`FUNDING_REVISION` in `src/herdbench/sources/ironflow.py`). |
| Binance Vision, FINRA, CFTC | Not versioned. Files are addressed by symbol, contract and date, and you receive what the source serves on the day you fetch. |
| Entity map | Built from live answers of Hyperliquid's info API. Its SHA-256 is recorded in the pre-analysis addendum. A later rerun can produce a different map ([see below](#the-pre-analysis-addendum-and-strict-scoring)). |
| Analysis | Every parameter is in `herdbench.config.PREREG`. Every resampling step is seeded from `PREREG.seed` (20261117). |

## Requirements

| Item | Requirement |
|---|---|
| Python | 3.10 or later. CI tests 3.10 to 3.13 on Linux, Windows and macOS. |
| Disk | About 25 GB for the raw inputs, plus room for the intermediate tables in `derived/`. |
| Memory | About 8 GB of RAM. |
| Network | Needed only by `herdbench fetch`, `herdbench build entities` and the optional `herdbench build panel-selection`. Hosts: `huggingface.co` (and the download hosts it redirects to), `api.hyperliquid.xyz`, `data.binance.vision`, `cdn.finra.org`, `publicreporting.cftc.gov`. |
| Accounts and data costs | None. No source needs a login, a key or a payment. |
| Coinalyze key | Optional. `COINALYZE_API_KEY` enables an optional cross-check client. No build stage uses it. |

## Install

```bash
git clone https://github.com/z33shxn/herdbench
cd herdbench
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -e ".[report]"         # or ".[dev]", which adds the test tools
```

- **Install in editable mode** (`-e`) from the clone. The build reads three files from the source tree. Scoring
  requires `docs/preregistration_addendum.md`, the manifest hashes it and `docs/preregistration.md`, and the
  report includes `docs/deviations.md`. A regular install cannot find these files, so scoring refuses to run.
- The `report` extra installs matplotlib for the report's figures. Without matplotlib, `herdbench report` writes
  tables only. The `dev` extra includes matplotlib.
- Line endings are fixed by `.gitattributes`: every text file is stored and checked out with LF on every platform,
  so the committed files match their SHA-256 in `results/manifest.json` and in `docs/timestamps/` on Windows too,
  whatever `core.autocrlf` says. Every JSON and Markdown file HerdBench writes also uses LF.
- Run every command from the repository root. The default folders are relative to the current directory.

## Folders and environment variables

| Variable | Default | Read by |
|---|---|---|
| `HERDBENCH_DATA_DIR` | `./data` | `fetch`, `build`, `doctor`. The data folder holds `raw/` (downloads) and `derived/` (private intermediate tables). |
| `HERDBENCH_RESULTS_DIR` | `./results` | `build`, `check` and `report`: the results folder. |
| `HERDBENCH_PANEL` | the truth panel bundled in `src/herdbench/data/` | `info`, `panel`, `grade` and `herdbench.load_truth_panel()` |
| `HERDBENCH_PROXIES` | the proxies bundled in `src/herdbench/data/` | the baselines of `grade`, and `herdbench.load_proxies()` |

- The `--data` and `--results` options override the first two variables. With `--data` and without
  `--results`, results go to `<data>/results` and `HERDBENCH_RESULTS_DIR` is ignored.
- `herdbench report` reads `HERDBENCH_RESULTS_DIR` (or `--results`) and writes the figures into a `figures/` folder
  next to `--out` (or into `--figures`).
- Build into a folder other than the repository's `results/`. The committed files and `results/manifest.json`
  then stay intact for the comparison in [Verify the output](#verify-the-output).

```bash
export HERDBENCH_DATA_DIR="$HOME/herdbench-data"
export HERDBENCH_RESULTS_DIR="$HOME/herdbench-rerun"
```

```powershell
$env:HERDBENCH_DATA_DIR = "$HOME\herdbench-data"
$env:HERDBENCH_RESULTS_DIR = "$HOME\herdbench-rerun"
```

`derived/` holds addresses, the entity map and every cell before disclosure control. `raw/` holds third-party
files that HerdBench does not redistribute. Share neither ([docs/data.md](data.md#on-disk-layout)).

## The command sequence

```bash
herdbench doctor                  # versions, and which input folders exist
herdbench fetch                   # 1. download every input
herdbench build entities          # 2. entity map (several hours, resumable)
herdbench build truth             # 3. every cohort cell
herdbench build gates             # 4. reconciliation, Gate 0, kill criterion 1
herdbench build proxies           # 5. every proxy
herdbench build publish           # 6. public truth panel and native proxies
herdbench build score --strict    # 7. every pre-registered statistic
herdbench build manifest          # 8. SHA-256 of every published file
herdbench report --out rerun/results.md   # 9. the report, with figures in rerun/figures/
```

`herdbench build all` runs steps 3 to 8 in this order (the gates right after the truth stage, so that a failed gate
labels every published file, the truth panel included). It does not run step 2, so the entity map must be complete first. `--strict` works with both `build score`
and `build all`.

`herdbench doctor` checks only that each input folder, and the entity-map file, exists. It does not check that
they are complete.

Two other stages are not part of a reproduction. `herdbench build addendum` refuses to overwrite the registered
addendum unless given `--force` ([see below](#the-pre-analysis-addendum-and-strict-scoring)). `herdbench build panel-selection`
re-derives the frozen primary panel ([optional check](#optional-check-the-primary-panel-selection)).

### Step 1: fetch

| `--only` | Source | Content and dates | Stored under `raw/` |
|---|---|---|---|
| `mirror` | Hugging Face mirror, pinned revision | Every snapshot and fill file in the mirror | `hf/perp_archive/`: snapshots, verbatim. `hf/fills_slim/`: fills reduced to 14 columns, with a JSON provenance sidecar per day. |
| `funding` | Ironflow, pinned revision | Monthly files, July 2025 to August 2026 | `ironflow/hyperliquid-funding/` |
| `binance` | Binance Vision | The 30 symbols of the primary panel. Daily metrics from 20 August 2025 to 18 August 2026; monthly 5-minute klines and funding, August 2025 to August 2026. | `binance/futures/um/` |
| `finra` | FINRA | Short-interest files for settlement dates from 1 November 2025 to 31 August 2026, found by probing candidate dates | `finra/` |
| `cftc` | CFTC public reporting API | 14 contracts, reports dated 1 July 2025 to 31 August 2026 | `cftc/` |

- Without `--only`, all five steps run in the order shown. `--only` can be repeated. `--workers N` sets the number
  of parallel downloads from the mirror (default 2).
- Rerunning is safe. Files already present are reused: a snapshot only if it passes the size and SHA-256 check, a
  fill day only if its sidecar records the same source hash, revision and column list, and every other file
  whenever it exists. An interrupted snapshot download resumes from its `.part` file.
- A Binance file that Vision never published (HTTP 403 or 404) is skipped, not treated as an error. The step
  prints how many metrics days were missing.
- Each step prints what it counted. The funding step prints 14 months and the CFTC step 14 series.
- The Binance symbols come from the frozen panel, `src/herdbench/data/primary_panel.json`, unless
  `derived/primary_panel.json` exists.

### Step 2: build entities

1. The stages that read the archive (entities, truth, proxies, gates and panel-selection) first make sure two
   cached tables exist: daily fill summaries (`derived/fills_daily/`) and a per-market summary of each snapshot
   (`derived/markets.parquet`). The first such stage builds them in one pass over every fill file and snapshot.
2. The stage lists the addresses to look up in `derived/entity_map_targets.parquet`. They are every market-maker
   candidate (an address that passes the market-maker tests in any 30-day window at the loosest maker share, 70%,
   without the turnover test) and every address that is ever one of the three largest holders of a market with at
   least $1M of open interest on that snapshot.
3. It asks Hyperliquid's info API for each target's role (`userRole`). It then lists the sub-accounts
   (`subAccounts`) of every master and every plain user found. Requests are spaced to stay inside the public limit
   of 1,200 request weight per minute: one `userRole` every 3.4 seconds (at most about 1,060 an hour) and one
   `subAccounts` every 1.15 seconds.
4. Each answer is appended to `derived/entity_roles.jsonl` as it arrives. If the run stops, run the same command
   again. It requests only the answers that are missing.

The stage prints a summary: counts, the map's SHA-256, and `"complete"`. `"complete"` is true once every target
has a role and every master's list has been read; the truth stage refuses to run until then. The summary holds no
address. To follow progress, watch `derived/entity_roles.jsonl` grow: one line per target's role first, then one
line per master's list.

### Steps 3 to 8: analysis

| Step | Reads | Writes |
|---|---|---|
| `build truth` | snapshots under `raw/hf/`; `derived/fills_daily/` and `derived/markets.parquet`; the entity map, which must be complete | `derived/style/`, `derived/entity_gross.parquet`, `derived/truth_cells.parquet`, `derived/mm_sets.json`, `derived/truth_info.json` |
| `build proxies` | `derived/fills_daily/` (hourly bars and liquidations) and `derived/markets.parquet`; `raw/ironflow/`; `raw/binance/` for the 30 primary-panel symbols | `derived/binance/` (parsed Vision tables), `derived/proxies.parquet` |
| `build publish` | `derived/truth_cells.parquet`, `derived/proxies.parquet`, `derived/markets.parquet`; the entity map | `truth_panel.parquet`, `truth_panel_summary.json`, `proxies.parquet` (Hyperliquid-native columns only) |
| `build score` | `docs/preregistration_addendum.md`; `derived/truth_cells.parquet`, `proxies.parquet`, `markets.parquet` and `fills_daily/`; the entity map; the published `truth_panel.parquet`; `raw/finra/`, `raw/ironflow/`, `raw/cftc/` | every statistic: the CSV files at the top of the results folder, `liquidation_delta_rho.json`, `score_summary.json`, and everything in `binance/` and `finra/` |
| `build gates` | `derived/fills_daily/` (daily volume); every consecutive snapshot pair with the fills between them, the entity map and `derived/mm_sets.json` (cached; recomputed when any of them changes) | `derived/reconciliation_pairs.json`, `gates.json` |
| `build manifest` | every file in the results folder; `docs/preregistration.md` and `docs/preregistration_addendum.md` | `manifest.json` |

File names without a folder are in the results folder. Statistics that involve a Binance proxy, and the whole
primary family, go to `binance/`. Statistics from FINRA short interest go to `finra/`
([licences](data.md#what-herdbench-publishes)). `herdbench build publish --ship` also copies the two Parquet files
into `src/herdbench/data/`; a reproduction does not need it.

No analysis stage downloads anything. A missing input, `raw/cftc/` included, stops the stage with an error that
points to `herdbench fetch`. `herdbench check` is the one command that reads the network after fetching: it
compares a seeded sample of the funding inputs with Hyperliquid's own API and writes `funding_check.json`.

### Step 9: report

`herdbench report` reads the results folder (`--results`, else `HERDBENCH_RESULTS_DIR`, else `./results`) and
`docs/deviations.md`. It writes the Markdown report to `--out` (default `docs/results.md`) and light and dark SVG
figures into a `figures/` folder next to it (or `--figures`). To compare your report with the published one, write
it elsewhere (`--out rerun/results.md`) and diff it against `docs/results.md`; the build time in the first paragraph
always differs.

## Run time, caching and reruns

Each stage reads its inputs from disk, so any stage can be rerun alone once its inputs exist. The expensive
intermediate tables are cached in `derived/`.

| Step | What takes time | After an interruption |
|---|---|---|
| `fetch` | About 25 GB from public hosts | Rerun. Complete files are kept and partial snapshot downloads resume. |
| `build entities` | One pass over the archive (done by the first stage that reads it), then the paced lookups: at least 3.4 s per target and 1.15 s per master. Several hours in total. | Rerun. Daily summaries already written and answers already stored are reused. |
| `build truth` | Reads every snapshot | Rerun. Per-date trend-score tables already written are reused. |
| `build gates` | Reconciles every consecutive snapshot pair against its fills (a few minutes) | Rerun. It starts over; the result is cached only when the run completes. |
| `build score` | CPU: 2,000 bootstrap replications per scored pair, 1,000 rotation draws for the primary family, 2,000 power simulations per transfer pairing | Rerun the stage. |

| Cache in `derived/` | Reused when | Delete it to |
|---|---|---|
| `fills_daily/<kind>_YYYY-MM-DD.parquet` | the file exists | rebuild that day's summaries |
| `markets.parquet`, `markets.json` | the list of snapshot file paths is unchanged | nothing: it rebuilds itself when the list changes, including after the data folder moves |
| `entity_map_targets.parquet` | the file exists | recompute the lookup list |
| `entity_roles.jsonl` | always; only missing answers are requested | query every address again (several hours) |
| `style/contrib_YYYY-MM-DD.parquet` | the file exists | rebuild that date's trend-score contributions |
| `entity_gross.parquet`, `entity_gross.json` | its key matches: entity-map hash, entities, snapshot file names | nothing: it rebuilds itself when the key changes |
| `binance/*.parquet`, `binance/*.json` | its key matches: the zip file names per symbol | nothing: it rebuilds itself when the key changes |
| `reconciliation_pairs.json` | its key matches: entity-map hash, market-maker sets, snapshot and fill file names | nothing: it rebuilds itself when the key changes |

## The pre-analysis addendum and strict scoring

`docs/preregistration_addendum.md` is part of the registered record. It was rendered by `herdbench build addendum`
once the entity map and the truth panel existed and before any proxy was compared with the truth. It was then
committed and timestamped (`docs/timestamps/`). It records counts, coverage, suppression and the entity map's
SHA-256, never an address.

| Command | Refuses to score unless |
|---|---|
| `herdbench build score`, `herdbench build all` | `docs/preregistration_addendum.md` exists in the source tree |
| `herdbench build score --strict`, `herdbench build all --strict` | the addendum exists, and your entity map hashes to the SHA-256 it records |

A failed check stops the stage with `RuntimeError: pre-analysis addendum check failed`, followed by the addendum's
name, both hashes and whether they match. Each completed scoring run also records this comparison in
`score_summary.json` under `addendum`.

**Why the hash can differ.** The entity map comes from live answers of the venue's API. A master account can open
sub-accounts after the registration date, so a rerun months later can map more addresses and get a different
hash. `--strict` then refuses to score; without it, scoring runs with your map. A changed map moves addresses
between entities. That can change which entities count as market makers, which cohort an entity falls in, and
which cells pass disclosure control. Results should then agree closely with the published ones, but not exactly.
The `entity_map` block of `truth_panel_summary.json` (targets, addresses mapped, entities with several addresses,
largest entity, map SHA-256) shows how your map differs from the registered one without naming any address.

Use `--strict` to confirm that you reproduced the registered map exactly:

```bash
herdbench build score --strict
```

**Do not run `herdbench build addendum --force` in a reproduction.** Without `--force` the command refuses to touch
the registered file; with it, the file is replaced by one rendered from your build, and the hash check then
compares your map with itself. If that happens, restore it with `git checkout -- docs/preregistration_addendum.md`.

## Optional: check the primary-panel selection

The primary panel is frozen in `src/herdbench/data/primary_panel.json`: the 30 largest main-dex markets by open
interest on the 27 August 2025 snapshot, among those with a Vision metrics file for their USDT perpetual on that
date. `herdbench build panel-selection` re-derives it. It downloads that date's Vision metrics files (it needs the
network), prints the selection and writes it to `derived/primary_panel.json`.

When `derived/primary_panel.json` exists, it replaces the bundled copy for every later stage and for
`herdbench fetch --only binance`. To check the selection without changing the build, compare the printed
selection with the bundled file, then delete `derived/primary_panel.json`.

## Verify the output

### The manifest

`herdbench build manifest` writes `manifest.json` in the results folder.

| Field | Content |
|---|---|
| `herdbench_version` | Version of the package that built the results |
| `built_at` | UTC time of the build. It differs on every run. |
| `mirror` | Repository and pinned revision of the archive mirror. `listing_sha256` is the SHA-256 of `raw/hf/listing.json`, the mirror's file list in a canonical form (sorted, no timestamp) that `herdbench fetch` writes, so it is the same for everyone who lists the pinned revision. |
| `environment` | Python, polars, pandas, NumPy, SciPy and pyarrow versions of the build |
| `funding` | Ironflow dataset and pinned revision |
| `preregistration_sha256` | SHA-256 of `docs/preregistration.md` |
| `addendum_sha256` | SHA-256 of `docs/preregistration_addendum.md` |
| `results_sha256` | SHA-256 of every file in the results folder, keyed by relative path. `manifest.json` itself is excluded. |

In the repository, `results/manifest.json` lists `truth_panel.parquet` and `proxies.parquet` with the other files,
but both are stored in `src/herdbench/data/`, where the package loads them.

### Compare with the published manifest

Run this from the repository root, with `HERDBENCH_RESULTS_DIR` set to your results folder. It compares the
manifest's metadata, then every published file by SHA-256. When a hash differs, it compares the content: parsed
JSON, Parquet tables, and the largest numeric difference in a CSV.

```python
import json
import os
from pathlib import Path

import pandas as pd
import polars as pl

from herdbench.pipeline import sha256_file

published = json.loads(Path("results/manifest.json").read_text(encoding="utf-8"))
rebuilt = Path(os.environ["HERDBENCH_RESULTS_DIR"])
mine = json.loads((rebuilt / "manifest.json").read_text(encoding="utf-8"))

for key in ("herdbench_version", "mirror", "funding", "preregistration_sha256", "addendum_sha256"):
    print(f"{'same' if mine.get(key) == published.get(key) else 'differs':8} {key}")

for rel, digest in sorted(published["results_sha256"].items()):
    new = rebuilt / rel
    if not new.exists():
        print(f"missing  {rel}")
        continue
    if sha256_file(new) == digest:
        print(f"same     {rel}")
        continue
    # the repository keeps the two published Parquet files in src/herdbench/data/
    old = Path("src/herdbench/data" if rel.endswith(".parquet") else "results") / rel
    if rel.endswith(".json"):
        equal = json.loads(old.read_text(encoding="utf-8")) == json.loads(new.read_text(encoding="utf-8"))
        note = "same content" if equal else "content differs"
    elif rel.endswith(".parquet"):
        note = "same content" if pl.read_parquet(old).equals(pl.read_parquet(new)) else "content differs"
    else:
        a, b = pd.read_csv(old), pd.read_csv(new)
        cols = a.select_dtypes("number").columns.intersection(b.columns)
        gap = (a[cols] - b[cols]).abs().max().max() if a.shape == b.shape else float("nan")
        note = f"shape {a.shape} vs {b.shape}, largest numeric difference {gap:.3g}"
    print(f"differs  {rel}  ({note})")

for f in sorted(rebuilt.rglob("*")):
    rel = f.relative_to(rebuilt).as_posix()
    if f.is_file() and rel != "manifest.json" and rel not in published["results_sha256"]:
        print(f"extra    {rel}")
```

| Output | Meaning |
|---|---|
| `same` | Identical bytes |
| `missing` | The stage that writes the file did not run, or lacked an input. For example, `cot_bridges*.csv` need `raw/cftc/`. |
| `differs (same content)` | Different bytes, same values. See the causes below. |
| `differs (content differs)`, or a CSV line with a numeric difference | Different values. The CSV check compares rows in file order, so treat it as a first look, not a full diff. |
| `extra` | A file the published build does not have |

### When a hash differs

A hash compares bytes, not numbers. In rough order from harmless to substantive, the causes are:

1. **The Parquet writer.** A Parquet file's footer records the version of polars that wrote it. Another polars
   version gives different bytes for the same table. (Every JSON, CSV and Markdown file is written with LF line
   ends, so the operating system does not change their bytes.)
2. **Library versions.** Numerical results can differ in the last digits between versions of NumPy, SciPy,
   pandas or polars. CSV numbers are written to six significant digits, which absorbs most such differences but
   not all. The manifest's `environment` block records the versions used for the published build.
3. **Inputs that are not pinned.** Binance Vision, FINRA and CFTC data are read as served on the day you fetch. A
   correction by the source changes the statistics that use it: `binance/`, `finra/` and `cot_bridges*.csv`.
4. **The entity map.** A different map can change any truth-based number
   ([see above](#the-pre-analysis-addendum-and-strict-scoring)). `score_summary.json` records under `addendum`
   whether your map matches the registered hash.

### The registered documents and your checkout

- `docs/timestamps/prereg-v1.txt` and `docs/timestamps/addendum-v1.txt` record a commit and the SHA-256 of each
  registered document, and `preregistration_sha256` and `addendum_sha256` in the manifest repeat the two hashes.
  They are hashes of the documents as registered, which git keeps at the recorded commits. In version 1.1.0 the
  working-tree copy of `docs/preregistration.md` was retypeset (en dashes and minus signs became ASCII hyphens;
  nothing else changed), so hash the registered versions directly:

  ```bash
  git show f205501:docs/preregistration.md | sha256sum            # ae32842f...
  git show a77b84d:docs/preregistration_addendum.md | sha256sum   # 5cf5cbb7...
  ```

  The addendum in the working tree is unchanged and hashes to the same value.
  `tests/test_preregistration.py` checks both hashes against the records, and checks that each working-tree file
  differs from its registered version only in those characters (it is skipped in a checkout without history).

- Each `.ots` file in `docs/timestamps/` is an OpenTimestamps proof of the text file next to it. The
  OpenTimestamps client checks it, for example `ots verify docs/timestamps/prereg-v1.txt.ots`.
- `pytest tests/test_bundled.py` (needs the `dev` extra) checks that every published file in your checkout
  matches `results/manifest.json`. It also checks that the bundled panel passes disclosure control, that the
  bundled proxies hold no Binance column, and that no Markdown, results or Python source file names an address
  outside the registered protocol accounts. The manifest and panel checks are skipped when the checkout has no
  published results.

## Use your rebuilt panel

`herdbench info`, `herdbench panel`, `herdbench grade` and the Python API read the truth panel and proxies bundled
with the package. To use your rebuild instead:

```bash
export HERDBENCH_PANEL="$HERDBENCH_RESULTS_DIR/truth_panel.parquet"
export HERDBENCH_PROXIES="$HERDBENCH_RESULTS_DIR/proxies.parquet"
herdbench info
```

`herdbench build publish --ship` copies the files into `src/herdbench/data/` instead, replacing the copies that
`results/manifest.json` describes. The environment variables leave the published copies untouched.

## Run the test suite

```bash
pip install -e ".[dev]"
pytest
```

- The suite needs no network and downloads nothing. It writes a synthetic archive to a temporary folder and runs
  every stage from truth to manifest on it, including the addendum check. The archive holds 150 days of snapshots
  and fills for 19 markets, funding, Binance Vision, FINRA and CFTC files in their real formats, and a ready-made
  entity map. Further tests render the report and run the command line on the result.
- The source clients, including the entity lookups and their resume logic, are tested against a local HTTP server
  on 127.0.0.1 that behaves like the real hosts.
- `pytest` deselects tests marked `network` (`-m 'not network'` in `pyproject.toml`). `pytest -m network` selects
  them.
- The suite takes about two minutes. CI runs it on Linux, Windows and macOS with Python 3.10 to 3.13.

## Troubleshooting

Only `herdbench fetch` downloads input files. Two stages query a source directly: `build entities` (the info API)
and `build panel-selection` (Binance Vision). Every other stage reads local files only and never falls back to
downloading. A missing input stops the stage with an error; the one exception is `raw/cftc/` (see the table).

| Symptom | Cause | Fix |
|---|---|---|
| `FileNotFoundError: no snapshots under .../raw/hf/perp_archive; run herdbench fetch first` | Nothing was fetched into this data folder, or `--data` or `HERDBENCH_DATA_DIR` points elsewhere | Run `herdbench fetch`, or point to the folder you fetched into. `herdbench doctor` shows which folders it finds. |
| `FileNotFoundError: funding months [...] are not under ...` | Funding files missing | `herdbench fetch --only funding` |
| `FileNotFoundError: no Binance metrics files under ...` (or `klines`, `funding`) | Binance files missing | `herdbench fetch --only binance` |
| `FileNotFoundError: FINRA short interest for <date> is missing` | FINRA files missing | `herdbench fetch --only finra` |
| `FileNotFoundError: no activity tables under ...` (or `hourly`, `volume`, `liq_hourly`) | `raw/hf/fills_slim/` is empty, so no daily fill summary could be built | `herdbench fetch --only mirror` |
| `FileNotFoundError: .../entity_roles.jsonl not found` | The entity stage has not run | `herdbench build entities` |
| `RuntimeError: the entity map is incomplete` | The entity stage stopped before the end | Rerun `herdbench build entities`. It resumes. |
| A `derived/` or results file is not found, such as `truth_cells.parquet`, `proxies.parquet` or `truth_panel.parquet` | An earlier stage has not run | Run the stages in order: entities, truth, gates, proxies, publish, score, manifest |
| `RuntimeError: pre-analysis addendum check failed`, with `'addendum': None` | `docs/preregistration_addendum.md` is not in the source tree | Install with `pip install -e` from a clone that contains the file |
| The same error with `'matches': False`, under `--strict` | Your entity map differs from the registered one | See [the addendum section](#the-pre-analysis-addendum-and-strict-scoring). Without `--strict`, scoring runs with your map. |
| `HttpError: ... failed after N attempts` | A network failure, or a rate limit that outlasted the retries (6 attempts; 8 for entity lookups) | Run the same command again. Completed files and stored answers are kept. |
| `MirrorError: ...: size or SHA-256 does not match the published value` | A download failed its check twice | Rerun `herdbench fetch --only mirror`. If it repeats, check free disk space. |
| An error reading a cached Binance, FINRA or CFTC file after an interrupted fetch | These files are written in one step, without a temporary file, so an interruption can leave one cut short | Delete the damaged file and rerun that fetch step. `zipfile.is_zipfile()` returns False for a truncated Binance zip. |
| `JSONDecodeError` while loading `entity_roles.jsonl` | A hard stop cut the last line short | Delete the incomplete last line and rerun `herdbench build entities` |
| `FileNotFoundError: no CFTC series under .../raw/cftc` | The CFTC series were not fetched | `herdbench fetch --only cftc`, then `herdbench build score` and `herdbench build manifest` |
| Results written to an unexpected folder | With `--data` and without `--results`, results go to `<data>/results` | Pass `--results`, or use the environment variables without `--data` |
| Report sections say "Not run" or "was not built" | `herdbench report` read the wrong folder | Pass `--results`, or set `HERDBENCH_RESULTS_DIR` |
| The report has no figures | matplotlib is not installed | `pip install -e ".[report]"` |
| `FileNotFoundError: truth_panel.parquet is not bundled with this installation` | The installed package has no bundled panel | Set `HERDBENCH_PANEL` and `HERDBENCH_PROXIES` ([above](#use-your-rebuilt-panel)) |
| Only `.parquet` files differ from the manifest, with the same content | Another polars version wrote them | None needed ([causes](#when-a-hash-differs)) |
