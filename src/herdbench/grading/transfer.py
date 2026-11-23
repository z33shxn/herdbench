"""Transfer to US single-stock perpetuals: does equity-side short interest agree with perp positioning?

The unit is a stock and a FINRA settlement date. Statistics are two-way fixed-effects slopes (stock and
period) on standardised, demeaned variables, with standard errors clustered by stock (t with G - 1
degrees of freedom) and two-way clustering as a robustness check. p-values are reported only when the
simulated power on the panel's actual shape reaches the pre-registered threshold; otherwise the result is
descriptive (estimate and interval only).
"""

from __future__ import annotations

import math
from datetime import date

import numpy as np
import pandas as pd
from scipy import stats as sps

from herdbench.grading import stats

#: FINRA settlement date -> publication date (FINRA's short interest reporting schedule; each is the 7th
#: business day after settlement, which also gives the two December 2025 dates).
FINRA_PUBLICATION: dict[date, date] = {
    date(2025, 12, 15): date(2025, 12, 24),
    date(2025, 12, 31): date(2026, 1, 12),
    date(2026, 1, 15): date(2026, 1, 27),
    date(2026, 1, 30): date(2026, 2, 10),
    date(2026, 2, 13): date(2026, 2, 25),
    date(2026, 2, 27): date(2026, 3, 10),
    date(2026, 3, 13): date(2026, 3, 24),
    date(2026, 3, 31): date(2026, 4, 10),
    date(2026, 4, 15): date(2026, 4, 24),
    date(2026, 4, 30): date(2026, 5, 11),
    date(2026, 5, 15): date(2026, 5, 27),
    date(2026, 5, 29): date(2026, 6, 9),
    date(2026, 6, 15): date(2026, 6, 25),
    date(2026, 6, 30): date(2026, 7, 10),
    date(2026, 7, 15): date(2026, 7, 24),
    date(2026, 7, 31): date(2026, 8, 11),
    date(2026, 8, 14): date(2026, 8, 25),
}


#: Settlement dates before the first entry of FINRA_PUBLICATION (FINRA's calendar), so the first period of
#: the panel has its true length.
PRIOR_SETTLEMENTS: tuple[date, ...] = (date(2025, 11, 14), date(2025, 11, 28))


def previous_settlement(day: date) -> date:
    """The FINRA settlement date immediately before ``day``."""
    earlier = [d for d in sorted({*PRIOR_SETTLEMENTS, *FINRA_PUBLICATION}) if d < day]
    if not earlier:
        raise ValueError(f"no known FINRA settlement before {day}")
    return earlier[-1]


def transfer_stats(
    panel: pd.DataFrame, proxies: list[str], expected: dict[str, int], power: float, threshold: float
) -> pd.DataFrame:
    """``panel``: stock, period, ni and proxy columns."""
    rows = []
    for p in proxies:
        d = panel[["stock", "period", "ni", p]].dropna()
        g1 = pd.factorize(d["stock"])[0]
        g2 = pd.factorize(d["period"])[0]
        G = len(set(g1))
        base = {"proxy": p, "n_stocks": G, "n_obs": len(d), "power": power, "expected_sign": expected.get(p, 1)}
        if len(d) < 10 or G < 3 or d[p].std() == 0:
            rows.append(
                {
                    **base,
                    "beta": float("nan"),
                    "se": float("nan"),
                    "ci_low": float("nan"),
                    "ci_high": float("nan"),
                    "p_value": float("nan"),
                    "se_twoway": float("nan"),
                    "direction_as_expected": None,
                }
            )
            continue
        y = stats.twoway_demean(d["ni"].to_numpy(float), g1, g2)
        x = stats.twoway_demean(d[p].to_numpy(float), g1, g2)
        y, x = y / y.std(), x / x.std()
        beta, se = stats.ols_cluster(y, x, g1)
        _b2, se2 = stats.twoway_fe_cluster(y, x, g1, g2)
        crit = sps.t.ppf(0.975, G - 1)
        p_val = float(2 * sps.t.sf(abs(beta / se), G - 1)) if power >= threshold else float("nan")
        rows.append(
            {
                **base,
                "beta": beta,
                "se": se,
                "ci_low": beta - crit * se,
                "ci_high": beta + crit * se,
                "p_value": p_val,
                "se_twoway": se2,
                "direction_as_expected": bool(np.sign(beta) == expected.get(p, 1)) if not math.isnan(beta) else None,
            }
        )
    return pd.DataFrame(rows)
