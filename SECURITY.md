# Security policy

## Reporting a vulnerability

Report vulnerabilities privately, through GitHub's private vulnerability reporting for
[z33shxn/herdbench](https://github.com/z33shxn/herdbench):

1. Open the repository's **Security** tab and choose **Report a vulnerability**, or go directly to
   <https://github.com/z33shxn/herdbench/security/advisories/new>.
2. Describe the problem, the affected version (`herdbench --version`), how to reproduce it, and what an attacker
   could do with it.

If that page is not available, open a public issue that only asks for a private contact, without any detail of
the problem.

**Do not open a public issue, pull request or discussion about a vulnerability**, and do not disclose it
elsewhere before a fix is released. The report stays private between you and the maintainer until then.

If the report is about a published address or entity-level data, state where it appears: file, column, row, and
release or commit. Do not copy the data itself into the report.

## Scope

| Area | In scope |
|---|---|
| Code | The `herdbench` package and command line (`src/herdbench/`) and the CI workflow (`.github/workflows/`). For example: a crafted input file or server response that executes code or writes outside the folders the user chose; a download that bypasses the pinned dataset revision or the SHA-256 check of snapshot files; a credential that appears in an error message, log, traceback or `repr`. |
| Published data | The truth panel and native proxies (`truth_panel.parquet`, `proxies.parquet`), every file in `results/`, the pre-analysis addendum and the figures. For example: a published file that contains an address or an entity-level row; a published cell with fewer than 20 entities, or whose two largest entities hold more than 80% of its gross notional; a published file whose SHA-256 differs from `results/manifest.json`. |
| Privacy of the panel | Any way to learn about a single entity from published files, alone or combined with the venue's public data: its position in a cell, its cohort membership (for example, that it is classified as a market maker), or the entity map. Combining cells across cohorts, markets or dates, and undoing the rounding, are the usual routes. The disclosure rules are in section 12 of `docs/preregistration.md`. |

Out of scope:

- Vulnerabilities in the services and datasets HerdBench reads (Hyperliquid, Hugging Face, Ironflow, Binance
  Vision, FINRA, CFTC, Coinalyze) or in third-party packages. Report those to their owners, unless HerdBench
  uses them unsafely.
- The fact that Hyperliquid publishes every account's positions. That is the venue's design and the premise of
  the study.
- Disagreement with a statistical or design choice. Open a public issue; see [CONTRIBUTING.md](CONTRIBUTING.md).

## Credentials

HerdBench needs no credentials. Every source it reads is public and needs no account or key: the Hugging Face
mirror of Hyperliquid's snapshots and fills, Ironflow's funding dataset, Hyperliquid's public info API, Binance
Vision, FINRA's short-interest files and the CFTC's public reporting API. The CI workflow uses no secrets and
runs with read-only repository permissions.

The one exception is optional: a free Coinalyze API key, read from the `COINALYZE_API_KEY` environment variable
by `herdbench.sources.coinalyze` and used only for cross-source checks. No published result depends on it.

- **Never commit the key**: not in code, tests, notebooks, configuration files, logs, issues or pull requests.
  Set it in the environment only. `.gitignore` excludes `.env` and `.env.*` files.
- Coinalyze accepts the key only as a URL parameter. The client therefore rebuilds every error without the URL
  or the chained exception, and its `repr` omits the key. A code path that still exposes the key is a
  vulnerability; report it privately.
- If the key is ever committed or exposed, treat it as compromised: replace it at Coinalyze and remove it from
  the repository's history.

## Supported versions

Only the latest release receives security fixes. The current release is 1.0.0.
