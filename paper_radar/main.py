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
    """只测 LLM 导读链路：1 篇硬核 AI 论文，不抓取、不推送、不写 seen。

    故意选术语密集的论文——prompt 好不好，看它能不能把这类讲给非科班读者听懂。
    """
    track = get_track(cfg, "ai") or {}
    sample = Paper(
        source_id="smoke:test",
        title=(
            "From Stories to Features: Scaling Concept Vectors for AI "
            "Alignment & Interpretability"
        ),
        abstract=(
            "We study feature vectors: directions in a language model's activation space "
            "that correspond to human-interpretable concepts, such as honesty or deception. "
            "Existing unsupervised methods like sparse autoencoders attempt to recover the "
            "whole feature space, often at the cost of feature quality and interpretability. "
            "We take a more pragmatic route: focusing on concepts relevant to AI safety and "
            "alignment, we generate synthetic concept-antagonist story pairs and use the "
            "difference of their mean model activations as a feature vector. We scale this "
            "approach to 1,036 alignment-relevant concepts using 2M synthetic stories across "
            "multiple languages and genres, which we publicly release. Across six models from "
            "three model families, our feature vectors reliably generalize to detecting their "
            "corresponding concepts in chat dialogues and web text. They form an interpretable "
            "geometry shared across models and can causally steer model behavior when injected "
            "into activations. We further use these features to analyze model representations "
            "during jailbreak attacks, mathematical and agentic reasoning. Our targeted feature "
            "construction thus offers a scalable route to studying concepts in language models, "
            "potentially extending to millions of features across arbitrary domains."
        ),
        authors=["Paper Radar"],
        date="2026-10-20",
        url="https://arxiv.org/abs/2610.24556",
        venue="arXiv preprint (cs.CL)",
    )
    log.info("SMOKE_LLM：开始测试摘要链路 base=%s model=%s",
             os.environ.get("LLM_BASE_URL", "") or os.environ.get("ANTHROPIC_BASE_URL", ""),
             os.environ.get("LLM_MODEL", "") or os.environ.get("CLAUDE_MODEL", ""))
    summarize([sample], batch_size=1, categories=track.get("categories"))
    log.info("SMOKE_LLM 结果 quality=%s why_read=%r", sample.quality, sample.why_read)
    log.info("SMOKE_LLM 导读全文:\n%s", sample.zh_summary or "（空）")
    if not sample.zh_summary:
        log.error("SMOKE_LLM 失败：导读为空")
        return 1
    log.info("SMOKE_LLM 通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
