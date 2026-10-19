from __future__ import annotations

import logging

from .pipeline import run_track

log = logging.getLogger("paper_radar.comm")


def run_comm() -> int:
    """传播学日更（comm 频道，kind=windows）。"""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    return run_track("comm")


if __name__ == "__main__":
    raise SystemExit(run_comm())
