# Contributing to HerdBench

HerdBench is a pre-registered study as well as a Python package. Two rules follow from that: the registered
analysis does not change, and nothing published may identify an account. Bug reports, fixes, tests and
documentation are welcome within those rules.

## Development environment

Python 3.10 to 3.13 are supported and tested.

```bash
git clone https://github.com/z33shxn/herdbench
cd herdbench
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
```

The `dev` extra installs pytest, Hypothesis, ruff, mypy and type stubs, matplotlib for the report's figures, and
statsmodels and arch, which the tests use as reference implementations (Newey–West and clustered standard
errors, the Politis–White block length). Developing and testing need no data download, no account and no key.

## Checks that must pass

CI runs these on every push to `main` and on every pull request. Run them before you open a pull request:

```bash
ruff check src tests
ruff format --check src tests
mypy
pytest
```

| Check | What it covers |
|---|---|
| `ruff check src tests` | Lint rules configured in `pyproject.toml` (line length 120, Python 3.10 target) |
| `ruff format --check src tests` | Formatting; `ruff format src tests` applies it |
| `mypy` | Type checks of `src/herdbench` with the settings in `pyproject.toml`, including `check_untyped_defs` |
| `pytest` | The offline test suite; CI runs it on Linux, Windows and macOS with Python 3.10 to 3.13 |

The test suite needs no network. It writes a complete synthetic archive (snapshots, fills, funding, Binance
Vision, FINRA and CFTC files in their real formats) and runs the pipeline on it, from the truth panel to the
manifest. Source tests talk to a local HTTP server that behaves like the real data hosts. A test that needs the
internet must carry the `network` marker: the default run deselects it, and `pytest -m network` runs it. A
`FutureWarning` fails the run.

CI also builds the sdist and the wheel, installs the wheel in a clean environment and runs `herdbench --version`,
`herdbench info` and `load_truth_panel()` against it.

## The pre-registration is frozen

Every analysis parameter lives in two places:

- `herdbench.config.PREREG`, an instance of the frozen dataclass `PreRegistration` in `src/herdbench/config.py`;
- the parameter tables of `docs/preregistration.md`, one row per parameter: `` | `name` | value | meaning | ``.

`tests/test_preregistration.py` reads every such row. It fails if a field of `PreRegistration` is missing from
the document, if a value differs, or if the document states a parameter the code does not have.

`docs/preregistration.md` is the registered text. Its SHA-256 is recorded in `docs/timestamps/prereg-v1.txt`,
which an OpenTimestamps proof anchors, and `results/manifest.json` records it again. Do not edit it. Because the
test keeps code and document equal, a registered parameter cannot be changed in place either. The rules:

1. **Do not hard-code analysis choices.** Thresholds, windows, seeds, cohorts, proxy lists and bootstrap
   settings come from `PREREG`, passed through as a `PreRegistration` the way the pipeline does it.
2. **Changing a registered parameter is a new study, not a fix.** A change that alters a registered parameter,
   or makes the code compute something the document does not describe, is a deviation. Open an issue first. If
   it goes ahead, add an entry to `docs/deviations.md` stating what changed, why, and its effect. The registered
   version is still run, and its result is shown next to the new one. `herdbench report` copies the list into
   the Deviations section of `docs/results.md`.
3. **A bug fix that makes the code do what the document says is not a deviation.** If it changes a published
   number, say so in the pull request and rebuild the results (see below).
4. **Do not edit `docs/results.md` by hand.** `herdbench report` generates it from `results/`. Only its
   Deviations section comes from a hand-written file, `docs/deviations.md`.

Scoring refuses to run without the pre-analysis addendum (`docs/preregistration_addendum.md`), and
`herdbench build score --strict` also requires the current entity map to match the SHA-256 the addendum records.
Do not weaken these checks.

## Privacy: no addresses, no entity-level rows

The study reads every account's positions but publishes only aggregates. **No change may publish an address or
an entity-level row**: not in `results/`, the package data in `src/herdbench/data/`, the documentation, a figure,
a log message, an issue or a pull request. The only real addresses in the repository are the registered protocol
accounts in `PREREG`, which every cohort excludes. Tests otherwise use synthetic addresses.

These mechanisms keep the published files private and must stay intact:

- **`derived/` is never published.** It holds the entity map, the market-maker sets and the other tables at
  address or entity level. The default data folder, `./data` (raw downloads and `derived/`), is git-ignored.
  Never commit its contents.
- **Disclosure control** (`herdbench.truth.privacy`) runs before anything is written to `results/`. A cell needs
  at least 20 entities, and its two largest entities may hold at most 80% of its gross notional. A complement
  rule suppresses any cell that could be recovered by subtracting two published ones. Ratios are rounded to
  3 decimals, notional to 2 significant figures, and entity counts down to a multiple of 10. Robustness cohorts
  are never published.
- **Summaries carry counts, shares and hashes only.** This covers the pre-analysis addendum and
  `results/truth_panel_summary.json`.
- **Tests enforce it.** `tests/test_bundled.py` scans the root and `docs/` Markdown files, `results/` and the
  Python sources under `src/` for any address other than the registered protocol accounts, and checks the
  bundled panel's cohorts, entity counts and columns. `tests/test_pipeline.py` checks that no file the synthetic
  run writes to `results/` contains an address. Do not weaken or skip these tests. A new published output must
  be built from cells that pass disclosure control and must be covered by the same checks.

Data licences add a related rule: raw inputs are never redistributed. Statistics that use Binance Vision data go
only to `results/binance/` (CC BY-NC-SA 4.0), statistics from FINRA short interest only to `results/finra/`, and
the bundled proxy file holds no Binance-derived column. Tests check both.

## Rebuilding the results

A change that alters published numbers must be rebuilt from the full archive, about 25 GB:

```bash
herdbench fetch               # download every input
herdbench build entities      # entity map from the venue's public API; resumable, takes hours
herdbench build all           # truth, gates, proxies, publish, score, manifest
herdbench report              # docs/results.md and its figures
```

Inputs go to `./data` and results to `./results`. `HERDBENCH_DATA_DIR` and `HERDBENCH_RESULTS_DIR`, or the
`--data` and `--results` options, change that. Figures need matplotlib, from the `report` or `dev` extra. More
detail: `docs/reproduce.md`.

## Reporting bugs

Open an issue at <https://github.com/z33shxn/herdbench/issues>. Include:

- the output of `herdbench --version` and `herdbench doctor` (versions and which inputs are present; edit out
  local paths if you prefer), and your operating system;
- the command or code that fails, the full traceback, and what you expected;
- for a number you think is wrong: the file and row in `results/`, or the `grade()` call, with the value you
  expected and why.

Reproduce the problem with synthetic data where you can: `tests/utils/world.py` builds a complete synthetic
archive. **Never paste an address, a row from `derived/` or any other entity-level data into an issue.**

Report security vulnerabilities and privacy leaks privately, as described in [SECURITY.md](SECURITY.md), not in
a public issue.

If you disagree with a registered choice, open an issue and make the case. The registered analysis stays as
registered; an alternative can only be added as a reported deviation, with the registered version still run.

## Pull requests

- Branch from `main` and keep each pull request to one change.
- Add or update tests for any change in behaviour.
- Run the four checks above.
- State in the description whether the change affects a registered parameter, a published file or a published
  number. If it does, follow the rules above.

Contributions are accepted under the Apache License 2.0, the licence of the code (section 5 of
[LICENSE](LICENSE)).
