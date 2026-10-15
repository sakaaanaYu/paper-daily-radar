from __future__ import annotations

import logging
import os
from datetime import date
from pathlib import Path

from .classify import classify_keywords
from .config import load_config
from .push.feishu import send_digest
from .push.zotero import ZoteroClient
from .rank import rank_and_filter
from .sources.arxiv import fetch_arxiv
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

    # 1. 抓取
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

    fresh = [p for p in papers if p.source_id not in seen]
    log.info("抓取 %d 篇，其中未见过 %d 篇", len(papers), len(fresh))

    # 2. 筛选
    selected = rank_and_filter(fresh, cfg["keywords"], cfg["max_papers_per_day"])
    if not selected:
        log.info("今日无新增匹配论文")
        save_seen(SEEN_PATH, seen_list + [p.source_id for p in papers if p.source_id not in seen])
        return 0

    # 3. 分类（关键词预分类）+ 加工（LLM 修正分类 + 摘要）
    classify_keywords(selected, cfg["categories"])
    summarize(selected, categories=cfg["categories"])

    # 4. 推送
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

    # 5. 记录已见
    save_seen(SEEN_PATH, seen_list + [p.source_id for p in papers if p.source_id not in seen])
    log.info("完成：%d 篇已推送/入库", len(selected))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
