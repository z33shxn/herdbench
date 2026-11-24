# README images

The images on the front page, each in a light and a dark version. `herdbench visuals` draws them from the files in
[`results/`](../../results/) and from the bundled truth panel, so every number they show is read from the published
results when they are built. `tests/test_visuals.py` fails if a committed image differs from what the command draws
from this checkout.

```bash
herdbench visuals --out docs/visuals
```

| Image | What it shows | Numbers from |
|---|---|---|
| `hero` | Net positioning of the crowd without market makers, ni(non_mm), in the 30 primary markets on every published date, rows sorted from most net long to most net short | `src/herdbench/data/truth_panel.parquet`, `results/score_summary.json`, `results/binance/primary_family.csv` |
| `how` | The three steps of the study | `results/score_summary.json` |
| `scorecard` | The seven confirmatory proxies: mean within-market Spearman ρ, 95% interval and verdict | `results/binance/primary_family.csv` |
| `insights` | Funding pinned at its floor; Binance's global ratio against account and money imbalance; fragility against past liquidations | `results/directional_primary.csv`, `results/binance/cross_venue.csv`, `results/binance/primary_family.csv`, `results/liquidation_target.csv` |
| `timeline` | When the plan was sealed, first compared with the truth and published | [`docs/timestamps/README.md`](../timestamps/README.md) |
| `social` | The card shown when the repository link is shared (one dark, still image; `social.png` is rendered from it at 1280 by 640) | as `hero` |

Each file is a plain SVG: no script, no outside font or image, system fonts only. Motion is CSS animation that plays
when the image loads and settles within five seconds, and a `prefers-reduced-motion` rule shows the final frame at
once for readers who ask for less motion. Every element's resting state is its final, readable frame. The colours
follow the project's data-visualisation palette: blue for long and red for short on a diverging scale with a grey
midpoint, stepped separately for the light and the dark surface, and green, yellow and red verdict marks that always
come with a symbol and a word.

To refresh the PNG after the SVG changes, render `social.svg` at 1280 by 640 in any browser, for example
`chrome --headless=new --window-size=1280,640 --screenshot=social.png social.svg`, then upload it under
Settings, General, Social preview.

**Licences.** `scorecard` and `insights` show statistics derived from Binance Vision data and are shared under
CC BY-NC-SA 4.0, like [`results/binance/`](../../results/binance/). The other images are CC BY 4.0. Attribution:
"HerdBench (Mohammed Zeeshan), https://github.com/z33shxn/herdbench".
