"""Paths and the frozen pre-registration.

Every analysis choice lives in :data:`PREREG`. ``docs/preregistration.md`` states the same values in
words, and a test checks that the two agree, so the published study cannot drift from its plan.
"""

from __future__ import annotations

import os
from dataclasses import asdict, dataclass
from pathlib import Path

#: Public Hugging Face mirror that holds the all-account position snapshots and fills.
MIRROR_REPO = "craftify2221/hyperliquid-fills-raw"


@dataclass(frozen=True)
class PreRegistration:
    """All pre-registered parameters. Frozen: changing one is a new study, not a tweak."""

    registered_on: str = "2026-11-18"

    # Data and timing
    mirror_revision: str = "de05dc101c819aa68accfefd6647b8bf6de02189"
    evaluation_start: str = "2025-08-28"
    evaluation_end: str = "2026-08-18"

    # Eligibility and disclosure control (publication only; the analysis uses every cell)
    min_oi_notional: float = 1_000_000.0
    min_entities: int = 20
    dominance_top2_max: float = 0.80
    publish_decimals: int = 3
    publish_count_floor: int = 10
    suppressed_flag_share: float = 0.05

    # Protocol accounts removed from every cohort: the HLP vault, its leader and its child accounts.
    protocol_addresses: tuple[str, ...] = (
        "0xdfc24b077bc1425ad1dea75bcb6f8158e10df303",
        "0x677d831aef5328190852e24f13c46cac05f984e7",
        "0x010461c14e146ac35fe42271bdc1134ee31c703a",
        "0x2e3d94f0562703b25c83308a05046ddaf9a8dd14",
        "0x2ed5c4484ea3ff8b57d5f2fb152a40d9f2b68308",
        "0x31ca8395cf837de08b24da3f660e77761dfb974b",
        "0x469f690213c467c39a23efacfd2816896009d7d8",
        "0x5e177e5e39c0f4e421f5865a6d8beed8d921cb70",
        "0xb0a55f13d22f66e6d495ac98113841b2326e9540",
    )
    #: Builder-dex backstop liquidators are 0x4000...0000 plus the dex index.
    backstop_liquidator_base: int = 0x4000000000000000000000000000000000000000
    backstop_liquidator_span: int = 4096

    # Market-maker rule (entity level, all perp markets pooled)
    mm_window_days: int = 30
    mm_maker_share: float = 0.80
    mm_min_notional: float = 10_000_000.0
    mm_min_active_days: int = 10
    mm_turnover_multiple: float = 50.0
    mm_min_fill_days: int = 15
    mm_carry_forward_days: int = 15
    mm_robustness_maker_shares: tuple[float, ...] = (0.70, 0.90)
    mm_robustness_turnover: tuple[float, ...] = (20.0, 100.0)

    # Cohorts
    top_av_fraction: float = 0.20
    top_av_lag_days: int = 8
    top_av_lag_max_days: int = 14
    style_window_days: int = 30
    trend_threshold: float = 0.20
    style_min_flows: int = 5
    cohorts: tuple[str, ...] = ("all", "non_mm", "top_av", "trend", "contrarian")
    robustness_cohorts: tuple[str, ...] = ("non_mm_s070", "non_mm_s090", "non_mm_t020", "non_mm_t100")

    # Truth metrics
    fragile_distance: float = 0.10
    top_share_fraction: float = 0.10
    lwi_leverage_cap: float = 50.0
    coheld_min_other: int = 5
    signed_truths: tuple[str, ...] = ("ni", "ai", "lwi")
    unsigned_truths: tuple[str, ...] = ("abs_ni", "top10_share", "fragile_share", "coheld_share")
    primary_cohort: str = "non_mm"
    primary_truth: str = "ni"

    # Primary panel: the 30 largest main-dex markets by OI on the selection snapshot with a Binance USDT perp
    binance_markets: int = 30
    binance_selection_snapshot: str = "2025-08-27"
    binance_max_staleness_minutes: int = 60

    # Proxies (expected sign positive against their paired truths)
    confirmatory_proxies: tuple[str, ...] = (
        "premium_24h", "momentum_7d", "oi_flow_7d",
        "bn_global_ls", "bn_top_account_ls", "bn_top_position_ls", "bn_taker_ls",
    )
    secondary_directional: tuple[str, ...] = ("funding_24h", "funding_7d", "bn_funding_24h")
    unsigned_proxies: tuple[str, ...] = (
        "abs_premium_24h", "oi_change_7d", "comovement_30d", "liq_intensity_7d", "oi_to_volume_7d", "bn_oi_change_7d",
    )
    comovement_window: int = 30
    comovement_min_returns: int = 20

    # Statistics
    min_market_days: int = 60
    bootstrap_block_min: int = 20
    bootstrap_block_max: int = 40
    bootstrap_reps: int = 2000
    seed: int = 20261117
    nw_lags: int = 10
    min_xs_markets: int = 10
    placebo_shift_days: int = 60
    rotation_draws: int = 1000
    rotation_min_shift: int = 60
    equivalence_bound: float = 0.10
    holm_alpha: float = 0.05
    rank_dominance: float = 0.95
    stability_tau_threshold: float = 0.40
    stability_min_verdicts: int = 3
    tail_proxy_fraction: float = 0.10
    tail_truth_fraction: float = 0.20

    # Target (b): subsequent liquidations
    liquidation_horizon_days: int = 7

    # Gates
    reconciliation_threshold: float = 0.99
    gate0_max_gap: float = 0.01
    gate0_min_share: float = 0.99

    # Transfer panel (US single-stock perps on the xyz dex)
    transfer_start: str = "2025-12-15"
    transfer_end: str = "2026-08-14"
    equity_proxies: tuple[str, ...] = (
        "si_change_pct", "days_to_cover", "log_short_interest",
        "funding_period", "premium_period", "momentum_period", "comovement_30d",
    )
    equity_negative_sign: tuple[str, ...] = ("si_change_pct", "days_to_cover", "log_short_interest")

    # Kill criterion 3: clustered power on the actual stock panel
    power_rho: float = 0.20
    power_ar1: float = 0.50
    power_reps: int = 2000
    power_threshold: float = 0.80

    # Appendix (not gating): Barone and Lillo's TWAP coefficient, arXiv 2606.15715
    gate1_theta: float = -0.0177
    gate1_se: float = 0.0010

    # Kill criterion 1 (reported, not gating)
    kc1_min_stocks: int = 25
    kc1_min_days: int = 183

    @property
    def gate1_ci(self) -> tuple[float, float]:
        """The paper's 95% interval for the TWAP coefficient (estimate +/- 1.96 SE)."""
        return (self.gate1_theta - 1.96 * self.gate1_se, self.gate1_theta + 1.96 * self.gate1_se)

    def is_protocol(self, address: str) -> bool:
        a = address.lower()
        if a in self.protocol_addresses:
            return True
        try:
            offset = int(a, 16) - self.backstop_liquidator_base
        except ValueError:
            return False
        return 0 <= offset < self.backstop_liquidator_span

    def as_dict(self) -> dict:
        return asdict(self)


PREREG = PreRegistration()


def repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def data_dir() -> Path:
    """Where raw downloads and intermediate tables live (never published). ``HERDBENCH_DATA_DIR`` wins."""
    env = os.environ.get("HERDBENCH_DATA_DIR")
    return Path(env).expanduser().resolve() if env else Path.cwd() / "data"


def results_dir() -> Path:
    """Where published aggregates are written. ``HERDBENCH_RESULTS_DIR`` wins."""
    env = os.environ.get("HERDBENCH_RESULTS_DIR")
    return Path(env).expanduser().resolve() if env else Path.cwd() / "results"
