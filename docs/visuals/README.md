# README images

The images on the front page, each in a light and a dark version, and a share card. `herdbench visuals` draws them
when it is run on a results folder: every statistic comes from the files in [`results/`](../../results/), the hero's
tapestry from the truth panel, and the timeline's times from constants that the tests check against
[`docs/timestamps/README.md`](../timestamps/README.md) and the git history. `tests/test_visuals.py` fails if a
committed image differs from what the command draws from this checkout.

```bash
herdbench visuals --out docs/visuals
```

| Image | What it shows | Drawn from |
|---|---|---|
| `hero` | Net positioning of the crowd without market makers, ni(non_mm), in the 30 primary markets on every published snapshot date, rows sorted from most net long to most net short and columns placed by calendar date, so the archive's gap (25 October to 14 December 2025) shows as a gap; beside it, the count of signals that passed | `src/herdbench/data/truth_panel.parquet`, `results/score_summary.json`, `results/binance/primary_family.csv` |
| `how` | The three steps of the study | `results/score_summary.json`, `results/truth_panel_summary.json` |
| `scorecard` | The seven confirmatory proxies: mean within-market Spearman ρ, 95% interval and verdict | `results/binance/primary_family.csv` |
| `insights` | Funding pinned at the baseline rate; Binance's global ratio against account and money imbalance; fragility against past liquidations | `results/directional_primary.csv`, `results/binance/cross_venue.csv`, `results/binance/primary_family.csv`, `results/liquidation_target.csv` |
| `timeline` | When the plan was sealed, when the signals first met the truth, and when the results were published | the timestamp record and the commit that added the results (`42f6fa8`) |
| `social` | The card meant for link previews (one dark, still image); `social.png` is rendered from it at 1280 by 640 | as `hero` |

Each file is a plain SVG: no script, no outside font or image, system fonts only. Motion is CSS animation that plays
when the image loads and settles within five seconds, and a `prefers-reduced-motion` rule shows the final frame at
once for readers who ask for less motion. Every element's resting state is its final, readable frame. The colours
follow the project's data-visualisation palette: blue for long and red for short on a diverging scale with a grey
midpoint, stepped separately for the light and the dark surface, green, yellow and red verdict marks that always
come with a symbol and a word, and muted text that clears 4.5:1 contrast on every surface it sits on.

The share card becomes the repository's link preview once it is uploaded under Settings, General, Social preview.
To refresh the PNG after the SVG changes, render `social.svg` at 1280 by 640 in any browser, for example
`chrome --headless=new --window-size=1280,640 --screenshot=social.png social.svg`.

**Licences.** `hero`, `scorecard`, `insights` and `social` show statistics derived from Binance Vision data and are
shared under CC BY-NC-SA 4.0, like [`results/binance/`](../../results/binance/); each carries a notice saying so.
`how` and `timeline` are CC BY 4.0. The funding values in `insights` come from Ironflow's `hyperliquid-funding`
dataset (CC BY 4.0). Attribution: "HerdBench (Mohammed Zeeshan), https://github.com/z33shxn/herdbench".
