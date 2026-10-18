from __future__ import annotations

import logging
import os
from datetime import date
from pathlib import Path

from .config import load_config
from .dedupe import identity_keys
from .models import Paper
from .push.feishu import send_digest
from .push.zotero import ZoteroClient
from .rank import rank_and_filter
from .sources.comm_journals import (
    DEFAULT_JOURNALS,
    fetch_comm_journals,
    months_between,
    score_backfill,
)
from .store import load_seen, save_seen
from .summarize import summarize, summarize_daily_insight

log = logging.getLogger("paper_radar.comm")

SEEN_PATH = Path(__file__).resolve().parent.parent / "data" / "seen_comm.json"


def run_comm() -> int:
    """传播学日更：2 篇新文 + 2 篇回补精选，推到传播学飞书机器人。"""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    cfg = load_config()
    comm = cfg.get("comm") or {}
    dry_run = os.environ.get("DRY_RUN") == "1"
    if dry_run:
        log.info("DRY_RUN 测试模式：不写 seen_comm.json、不写 Zotero")

    seen_list = [] if dry_run else load_seen(SEEN_PATH)
    seen = set(seen_list)

    journals = comm.get("journals") or DEFAULT_JOURNALS
    fresh_n = int(comm.get("fresh_count", 2))
    back_n = int(comm.get("backfill_count", 2))
    limit = int(comm.get("max_papers_per_day", fresh_n + back_n))
    keywords = comm.get("keywords") or cfg.get("keywords") or []

    fresh_pool, back_pool = fetch_comm_journals(
        journals,
        fresh_days=int(comm.get("fresh_days", 30)),
        backfill_min_months=int(comm.get("backfill_min_months", 3)),
        backfill_max_months=int(comm.get("backfill_max_months", 18)),
        max_per_window=int(comm.get("max_per_window", 40)),
    )

    # 去重：跨源/跨期
    def _unseen(items: list[Paper]) -> list[Paper]:
        out, local = [], set()
        for p in items:
            keys = identity_keys(p)
            if any(k in seen or k in local for k in keys):
                continue
            local.update(keys)
            out.append(p)
        return out

    fresh = _unseen(fresh_pool)
    backlog = _unseen(back_pool)
    log.info("传播：新文未见 %d，回补未见 %d", len(fresh), len(backlog))

    # 新文：关键词相关度排序
    fresh_ranked = rank_and_filter(fresh, keywords, limit=max(fresh_n * 5, 1)) if fresh else []
    selected: list[Paper] = []
    for p in fresh_ranked[:fresh_n]:
        p.category = "fresh"
        p.why_read = f"新文 · {p.accepted_at}"
        selected.append(p)

    # 回补：质量分，避开已选作者/同刊撞车
    today = date.today()
    scored = []
    for p in backlog:
        age_m = months_between(p.date, today)
        scored.append((score_backfill(p, keywords, age_m), p, age_m))
    scored.sort(key=lambda x: x[0], reverse=True)

    used_authors: set[str] = set()
    used_venues: set[str] = set()
    for s, p, age_m in scored:
        if len(selected) >= fresh_n + back_n or len([x for x in selected if x.category == "backfill"]) >= back_n:
            break
        authors = {a.lower() for a in (p.authors or [])[:2]}
        if authors & used_authors:
            continue
        v = (p.accepted_at or "").lower()
        if v in used_venues:
            continue
        p.category = "backfill"
        p.why_read = f"回补精选 · {p.accepted_at} · {p.date[:7]}（{age_m:.0f}个月前）"
        selected.append(p)
        used_authors.update(authors)
        used_venues.add(v)

    if not selected:
        log.info("传播：今日无可推论文")
        return 0

    for p in selected:
        log.info("传播入选 [%s][%s] %s", p.category, p.venue, p.title[:50])

    summarize(selected, batch_size=6, update_category=False)
    insight = summarize_daily_insight(selected)

    # 卡片按「新文 / 回补精选」两栏展示，而不是配置里的四板块
    groups_map: dict[str, list[Paper]] = {"fresh": [], "backfill": []}
    for p in selected:
        groups_map.setdefault(p.category, []).append(p)
    comm_cats = [
        {"id": "fresh", "label": "📰 新文"},
        {"id": "backfill", "label": "📎 回补精选"},
    ]

    today_s = today.isoformat()
    url = os.environ.get("FEISHU_COMM_WEBHOOK_URL") or ""
    if not url:
        log.warning("未设置 FEISHU_COMM_WEBHOOK_URL（请单独建 Secret，避免和 AI 雷达混发）")
    if url:
        send_digest(url, selected, f"{today_s} · 传播", categories=comm_cats, insight=insight)
    else:
        log.info("跳过飞书（缺 FEISHU_COMM_WEBHOOK_URL）")

    if not dry_run and os.environ.get("ZOTERO_API_KEY"):
        try:
            client = ZoteroClient(
                os.environ["ZOTERO_API_KEY"], os.environ.get("ZOTERO_USER_ID") or None
            )
            client.save_papers(selected)
        except Exception as e:
            log.error("Zotero 写入失败: %s", e)
    elif dry_run:
        log.info("DRY_RUN：跳过 Zotero")

    if dry_run:
        log.info("DRY_RUN 完成：%d 篇（未写 seen）", len(selected))
        return 0

    new_keys: list[str] = []
    for p in selected:
        for k in identity_keys(p):
            if k not in seen:
                seen.add(k)
                new_keys.append(k)
    save_seen(SEEN_PATH, seen_list + new_keys)
    log.info("传播完成：%d 篇", len(selected))
    return 0


if __name__ == "__main__":
    raise SystemExit(run_comm())
