from __future__ import annotations

import logging
import os

from .config import get_track, load_config
from .models import Paper
from .pipeline import run_track
from .summarize import summarize

log = logging.getLogger("paper_radar")


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    cfg = load_config()
    if os.environ.get("SMOKE_LLM") == "1":
        return _smoke_llm(cfg)
    # 默认跑 ai 频道；换名/加频道用 python -m paper_radar.run --track NAME
    return run_track(os.environ.get("PAPER_RADAR_TRACK", "ai"), cfg)


def _smoke_llm(cfg: dict) -> int:
    """只测 LLM 导读链路：1 篇假论文，不抓取、不推送、不写 seen。"""
    track = get_track(cfg, "ai") or {}
    sample = Paper(
        source_id="smoke:test",
        title="Smoke Test: Can Large Language Models Generate Structured Chinese Paper Digests?",
        abstract=(
            "We evaluate whether a pluggable OpenAI-compatible LLM can produce structured "
            "Chinese reading guides for research papers. Given title and abstract, the model "
            "must output problem, method, results, and highlights in 120-220 Chinese characters, "
            "plus a 1-5 relevance quality score and a one-line takeaway. We test prompt design "
            "and JSON reliability across batches."
        ),
        authors=["Paper Radar"],
        date="2026-10-16",
        url="https://example.com/smoke",
        venue="smoke",
    )
    log.info("SMOKE_LLM：开始测试摘要链路 base=%s model=%s",
             os.environ.get("LLM_BASE_URL", ""), os.environ.get("LLM_MODEL", ""))
    summarize([sample], batch_size=1, categories=track.get("categories"))
    log.info("SMOKE_LLM 结果 quality=%s why_read=%r", sample.quality, sample.why_read)
    log.info("SMOKE_LLM 导读: %s", (sample.zh_summary or "（空）")[:300])
    if not sample.zh_summary:
        log.error("SMOKE_LLM 失败：导读为空")
        return 1
    log.info("SMOKE_LLM 通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
