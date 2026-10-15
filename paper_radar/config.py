from __future__ import annotations

import os
from pathlib import Path

import yaml

DEFAULTS = {
    "arxiv": {"categories": ["cs.HC", "cs.AI", "cs.CL"], "max_results": 80},
    "openreview": {"venues": [], "max_per_venue": 40},
    "keywords": [],
    "max_papers_per_day": 10,
}


def load_config(path: str | Path | None = None) -> dict:
    p = Path(path or os.environ.get("PAPER_RADAR_CONFIG", "config.yaml"))
    data: dict = {}
    if p.exists():
        data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}

    arxiv = {**DEFAULTS["arxiv"], **(data.get("arxiv") or {})}
    openreview = {**DEFAULTS["openreview"], **(data.get("openreview") or {})}
    return {
        "arxiv": arxiv,
        "openreview": openreview,
        "keywords": list(data.get("keywords") or []),
        "max_papers_per_day": int(data.get("max_papers_per_day") or 10),
    }
