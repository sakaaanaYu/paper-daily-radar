from __future__ import annotations

import argparse
import logging
import sys

from .config import load_config
from .pipeline import run_track

log = logging.getLogger("paper_radar.run")


def main(argv: list[str] | None = None) -> int:
    """跑任意频道：python -m paper_radar.run --track bio | --all。

    daily.yml 里给每个要跑的频道加一行即可；只想跑一个频道也可以用
    PAPER_RADAR_TRACK 环境变量配 python -m paper_radar.main。
    """
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    ap = argparse.ArgumentParser(prog="paper_radar.run", description="跑 config.yaml 里定义的频道")
    ap.add_argument("--track", action="append", default=[], help="频道名，可重复；缺省且无 --all 时报错")
    ap.add_argument("--all", action="store_true", help="跑 tracks: 下所有频道")
    args = ap.parse_args(argv)

    cfg = load_config()
    names = list(cfg.get("tracks") or {})

    if args.all:
        targets = names
    elif args.track:
        targets = args.track
    else:
        ap.error("请指定 --track NAME（可重复）或 --all")

    rc = 0
    for name in targets:
        log.info("===== track %s =====", name)
        rc = run_track(name, cfg) or rc
    return rc


if __name__ == "__main__":
    sys.exit(main())
