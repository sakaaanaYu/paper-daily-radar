from __future__ import annotations

import logging
import os
from datetime import date
from pathlib import Path

from .classify import select_with_quota
from .config import load_config
from .dedupe import dedupe_papers, identity_keys, is_seen
from .push.feishu import send_digest
from .push.zotero import ZoteroClient
from .quality import (
    apply_venue_boost,
    apply_venue_labels,
    exclude_heuristics,
    heuristic_quality,
    verify_venues,
)
from .rank import rank_and_filter
from .sources.acm_conf import fetch_acm_confs
from .sources.arxiv import fetch_arxiv
from .sources.journals import fetch_journals
from .sources.openreview import fetch_openreview
from .store import load_seen, save_seen
from .summarize import summarize

log = logging.getLogger("paper_radar")

SEEN_PATH = Path(__file__).resolve().parent.parent / "data" / "seen.json"


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    cfg = load_config()
    seen_list = load_seen(SEEN_PATH)
    seen = set(seen_list)

    # 1. 抓取：arXiv 预印本 + OpenReview 顶会 + 顶刊 RSS + ACM 会议
    papers = []
    if cfg["arxiv"].get("categories"):
        papers.extend(
            fetch_arxiv(cfg["arxiv"]["categories"], int(cfg["arxiv"].get("max_results", 80)))
        )
    if cfg["openreview"].get("venues"):
        papers.extend(
            fetch_openreview(
                cfg["openreview"]["venues"], int(cfg["openreview"].get("max_per_venue", 40))
            )
        )
    if cfg.get("journals"):
        papers.extend(fetch_journals(cfg["journals"], int(cfg.get("max_per_journal", 20))))
    if cfg.get("acm_confs"):
        papers.extend(
            fetch_acm_confs(cfg["acm_confs"], int(cfg.get("max_per_conf", 25)))
        )

    # 2. 跨源去重（DOI / 标题指纹，同一篇的 arXiv 版和会议版只留一条）
    raw_n = len(papers)
    papers = dedupe_papers(papers)
    fresh = [p for p in papers if not is_seen(p, seen)]
    log.info("抓取 %d 篇，跨源去重后 %d，其中未见过 %d 篇", raw_n, len(papers), len(fresh))

    # 3. 排除启发 + 关键词初筛（先拿到候选池，再做质量加权）
    cleaned = exclude_heuristics(fresh)
    candidates = rank_and_filter(cleaned, cfg["keywords"], limit=max(len(cleaned), 1))
    if not candidates:
        log.info("今日无新增匹配论文")
        _mark_seen(SEEN_PATH, seen_list, papers, seen)
        return 0

    # 3. 录用查证（Semantic Scholar / DBLP）+ 顶会加权 + 出处标注
    verify_venues(candidates)
    apply_venue_boost(candidates)
    apply_venue_labels(candidates)

    # 4. 分类保底取 top（每类至少 per_cat_min 篇，再按分数补满）
    selected = select_with_quota(
        candidates,
        cfg["categories"],
        limit=int(cfg["max_papers_per_day"]),
        per_cat_min=int(cfg.get("per_cat_min", 2)),
    )
    for p in selected:
        log.info("入选: [%s][%s] %s", p.category, p.venue, p.title[:50])

    # 5. 先用启发式给质量分（无 LLM 也有区分度），LLM 导读跑起来后会覆盖
    heuristic_quality(selected)
    summarize(selected, categories=cfg["categories"])

    # 5. 推送
    today = date.today().isoformat()
    feishu_url = os.environ.get("FEISHU_WEBHOOK_URL", "")
    if feishu_url:
        send_digest(feishu_url, selected, today, categories=cfg["categories"])
    else:
        log.warning("未设置 FEISHU_WEBHOOK_URL，跳过飞书推送")

    zotero_key = os.environ.get("ZOTERO_API_KEY", "")
    if zotero_key:
        try:
            client = ZoteroClient(zotero_key, os.environ.get("ZOTERO_USER_ID") or None)
            client.save_papers(selected)
        except Exception as e:
            log.error("Zotero 写入失败: %s", e)
    else:
        log.warning("未设置 ZOTERO_API_KEY，跳过 Zotero")

    # 6. 记录已见（记 DOI/arXiv id + 标题指纹，下一次同一篇的任何来源都命中）
    _mark_seen(SEEN_PATH, seen_list, papers, seen)
    log.info("完成：%d 篇已推送/入库", len(selected))
    return 0


def _mark_seen(path, seen_list: list[str], papers, seen: set[str]) -> None:
    """把本轮所有论文的多重身份 key 写入 seen，保证跨源、跨天都不重复推送。"""
    new_keys: list[str] = []
    for p in papers:
        for k in identity_keys(p):
            if k not in seen:
                seen.add(k)
                new_keys.append(k)
    save_seen(path, seen_list + new_keys)


if __name__ == "__main__":
    raise SystemExit(main())
