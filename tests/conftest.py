"""Shared fixtures: one synthetic archive, built and run through the whole pipeline once per test session."""

from __future__ import annotations

import pytest

from herdbench import pipeline
from tests.utils import world

P = world.PREREG_WORLD


@pytest.fixture(scope="session")
def built(tmp_path_factory):
    root = tmp_path_factory.mktemp("world")
    paths = world.make(root)
    cells = pipeline.stage_truth(paths, P)
    gates = pipeline.stage_gates(paths, P)  # the order of run_all: truth, gates, proxies, publish, score
    proxies = pipeline.stage_proxies(paths, P)
    panel = pipeline.stage_publish(paths, P)
    _, info = pipeline.load_entity_map(paths)
    addendum = root / "addendum.md"
    addendum.write_text(f"| Entity map SHA-256 | `{info['map_sha256']}` |\n", encoding="utf-8")
    res = pipeline.stage_score(paths, P, strict_addendum=True, addendum=addendum)
    manifest = pipeline.write_manifest(paths, P, addendum)
    return {
        "paths": paths,
        "cells": cells,
        "proxies": proxies,
        "panel": panel,
        "res": res,
        "gates": gates,
        "manifest": manifest,
        "info": info,
        "root": root,
    }
