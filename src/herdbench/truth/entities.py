"""Group addresses into entities: a master account and all of its sub-accounts are one entity.

Hyperliquid lets one master run several sub-accounts, so counting addresses would let one firm pass the
20-account rule alone, or hide a market maker's hedging sub-account inside the crowd. The map covers every
market-maker candidate (loosest rule, any window) and every address that is ever one of the three largest
holders of an eligible market; any other address is its own entity. Vaults are their own entities.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from pathlib import Path

import polars as pl


def build_map(roles: dict[str, tuple[str, str | None]], subs: dict[str, list[str]]) -> dict[str, str]:
    """Address -> entity (master address) for every address known to belong to a master."""
    emap: dict[str, str] = {}
    for address, (role, master) in roles.items():
        if role == "subAccount" and master:
            emap[address.lower()] = master.lower()
            emap[master.lower()] = master.lower()
    for master, children in subs.items():
        if children:
            emap[master.lower()] = master.lower()
            for child in children:
                emap[child.lower()] = master.lower()
    return emap


def load_store(store: Path) -> tuple[dict[str, tuple[str, str | None]], dict[str, list[str]]]:
    roles: dict[str, tuple[str, str | None]] = {}
    subs: dict[str, list[str]] = {}
    if store.exists():
        for line in store.read_text(encoding="utf-8").splitlines():
            rec = json.loads(line)
            if rec["kind"] == "role":
                roles[rec["address"]] = (rec["role"], rec["master"])
            else:
                subs[rec["address"]] = rec["subs"]
    return roles, subs


def fetch_roles(targets: Iterable[str], api, store: Path) -> tuple[dict, dict]:
    """Look up each target's role, then list the sub-accounts of every master and of every plain user.

    Appends one JSON line per answer to ``store``, so an interrupted run resumes where it stopped.
    """
    store = Path(store)
    store.parent.mkdir(parents=True, exist_ok=True)
    roles, subs = load_store(store)
    with open(store, "a", encoding="utf-8") as fh:
        for address in (t.lower() for t in targets):
            if address not in roles:
                role, master = api.user_role(address)
                roles[address] = (role, master)
                fh.write(json.dumps({"kind": "role", "address": address, "role": role, "master": master}) + "\n")
                fh.flush()
        masters = {m for r, m in roles.values() if r == "subAccount" and m}
        masters |= {a for a, (r, _m) in roles.items() if r == "user"}
        for master in sorted(masters):
            if master not in subs:
                subs[master] = api.sub_accounts(master)
                fh.write(json.dumps({"kind": "subs", "address": master, "subs": subs[master]}) + "\n")
                fh.flush()
    return roles, subs


def attach(positions: pl.DataFrame, emap: dict[str, str], user_col: str = "user") -> pl.DataFrame:
    if not emap:
        return positions.with_columns(entity=pl.col(user_col))
    mapping = pl.DataFrame({user_col: list(emap), "_entity": list(emap.values())})
    return (
        positions.join(mapping, on=user_col, how="left")
        .with_columns(entity=pl.coalesce(pl.col("_entity"), pl.col(user_col)))
        .drop("_entity")
    )


def map_hash(emap: dict[str, str]) -> str:
    """SHA-256 of the map in canonical form, frozen in the pre-registration before analysis."""
    canonical = json.dumps(sorted(emap.items()), separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()
