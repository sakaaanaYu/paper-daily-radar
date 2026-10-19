from __future__ import annotations

import logging
import os
from datetime import date

from .classify import select_with_quota
from .config import get_track, load_config
from .dedupe import dedupe_papers, identity_keys, is_seen
from .models import Paper
from .notes import save_notes
from .push.feishu import send_digest
from .push.notion import client_from_env as notion_client_from_env
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
from .sources.comm_journals import (
    DEFAULT_JOURNALS,
    fetch_comm_journals,
    months_between,
    score_backfill,
)
from .sources.journals import fetch_journals
from .sources.openreview import fetch_openreview
from .store import load_seen, save_seen
from .summarize import summarize, summarize_daily_insight

log = logging.getLogger("paper_radar.pipeline")


def run_track(name: str, cfg: dict | None = None) -> int:
    """按名字跑一个频道。track 不存在时安静跳过（fork 删掉频道不会打红 CI）。"""
    if cfg is None:
        cfg = load_config()
    track = get_track(cfg, name)
    if track is None:
        log.warning("配置里没有 track %r，跳过（在 config.yaml 的 tracks: 下定义它）", name)
        return 0
    if track["kind"] == "windows":
        return run_windows_track(track)
    return run_quota_track(track)


# ---------------------------------------------------------------------------
# kind=quota：排除启发 + 录用查证 + 板块保底选篇（原 main.py 流水线）
# ---------------------------------------------------------------------------


def run_quota_track(track: dict) -> int:
    dry_run = os.environ.get("DRY_RUN") == "1"
    if dry_run:
        log.info("DRY_RUN 测试模式：不写 seen、不写 Zotero")
    seen_path = track["seen_file"]
    seen_list = load_seen(seen_path) if not dry_run else []
    seen = set(seen_list)

    # 1. 抓取：sources 里启用的块各抓一批
    papers = _fetch_quota_sources(track.get("sources") or {})

    # 2. 跨源去重（DOI / 标题指纹，同一篇的 arXiv 版和会议版只留一条）
    raw_n = len(papers)
    papers = dedupe_papers(papers)
    if dry_run:
        fresh = papers  # 测试：忽略 seen，每次都出结果
    else:
        fresh = [p for p in papers if not is_seen(p, seen)]
    log.info("抓取 %d 篇，跨源去重后 %d，其中未见过 %d 篇", raw_n, len(papers), len(fresh))

    # 3. 排除启发 + 关键词初筛（先拿到候选池，再做质量加权）
    cleaned = exclude_heuristics(fresh)
    candidates = rank_and_filter(cleaned, track["keywords"], limit=max(len(cleaned), 1))
    if not candidates:
        log.info("今日无新增匹配论文")
        if not dry_run:
            _mark_seen(seen_path, seen_list, papers, seen)
        return 0

    # 3. 录用查证（Semantic Scholar / DBLP）+ 顶会加权 + 出处标注
    verify_venues(candidates)
    apply_venue_boost(candidates)
    apply_venue_labels(candidates)

    # 4. 分类保底取 top（每类至少 per_cat_min 篇，再按分数补满）
    selected = select_with_quota(
        candidates,
        track["categories"],
        limit=int(track["max_papers_per_day"]),
        per_cat_min=int(track.get("per_cat_min", 2)),
    )
    for p in selected:
        log.info("入选: [%s][%s] %s", p.category, p.venue, p.title[:50])

    # 5. 先用启发式给质量分（无 LLM 也有区分度），LLM 导读跑起来后会覆盖
    heuristic_quality(selected)
    summarize(selected, categories=track["categories"])
    for p in selected:
        log.info(
            "导读 [%s] | %s | %s\n%s",
            p.category,
            p.venue,
            (p.why_read or "")[:60],
            (p.zh_summary or "（无导读）"),
        )
    insight = summarize_daily_insight(selected)
    if insight:
        log.info("今日总结：\n%s", insight)

    # 6. 推送（飞书 / 笔记 / Notion / Zotero）
    _push_all(track, selected, insight, groups=track["categories"])

    # 7. 记录已见（测试模式不落盘）——quota 频道把本轮抓到的全部记入，防止明日重复
    if dry_run:
        log.info("DRY_RUN 完成：%d 篇（未写 seen，可直接重跑）", len(selected))
    else:
        _mark_seen(seen_path, seen_list, papers, seen)
        log.info("完成：%d 篇已推送/入库", len(selected))
    return 0


def _fetch_quota_sources(sources: dict) -> list[Paper]:
    papers: list[Paper] = []
    arxiv = sources.get("arxiv") or {}
    if arxiv.get("categories"):
        papers.extend(fetch_arxiv(arxiv["categories"], int(arxiv.get("max_results", 80))))
    openreview = sources.get("openreview") or {}
    if openreview.get("venues"):
        papers.extend(
            fetch_openreview(openreview["venues"], int(openreview.get("max_per_venue", 40)))
        )
    journals = sources.get("journals") or {}
    if journals.get("feeds"):
        papers.extend(fetch_journals(journals["feeds"], int(journals.get("max_per_journal", 20))))
    acm = sources.get("acm_confs") or {}
    if acm.get("venues"):
        papers.extend(fetch_acm_confs(acm["venues"], int(acm.get("max_per_venue", 25))))
    return papers


# ---------------------------------------------------------------------------
# kind=windows：新文 + 回补双窗口（原 comm.py 流水线）
# ---------------------------------------------------------------------------


def run_windows_track(track: dict) -> int:
    dry_run = os.environ.get("DRY_RUN") == "1"
    if dry_run:
        log.info("DRY_RUN 测试模式：不写 seen、不写 Zotero")

    seen_path = track["seen_file"]
    seen_list = [] if dry_run else load_seen(seen_path)
    seen = set(seen_list)

    src = (track.get("sources") or {}).get("comm_journals") or {}
    journals = src.get("journals") or DEFAULT_JOURNALS
    fresh_n = int(track.get("fresh_count", 2))
    back_n = int(track.get("backfill_count", 2))
    keywords = track["keywords"]

    fresh_pool, back_pool = fetch_comm_journals(
        journals,
        fresh_days=int(src.get("fresh_days", 30)),
        backfill_min_months=int(src.get("backfill_min_months", 3)),
        backfill_max_months=int(src.get("backfill_max_months", 18)),
        max_per_window=int(src.get("max_per_window", 40)),
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
    log.info("新文未见 %d，回补未见 %d", len(fresh), len(backlog))

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
        log.info("今日无可推论文")
        return 0

    for p in selected:
        log.info("入选 [%s][%s] %s", p.category, p.venue, p.title[:50])

    summarize(selected, batch_size=6, update_category=False)
    insight = summarize_daily_insight(selected)

    _push_all(track, selected, insight, groups=track["groups"])

    if dry_run:
        log.info("DRY_RUN 完成：%d 篇（未写 seen）", len(selected))
        return 0

    # windows 频道只把入选的记入 seen（与 quota 频道不同）
    _mark_seen(seen_path, seen_list, selected, seen)
    log.info("完成：%d 篇", len(selected))
    return 0


# ---------------------------------------------------------------------------
# 共享
# ---------------------------------------------------------------------------


def _push_all(track: dict, selected: list[Paper], insight: str, groups: list[dict]) -> None:
    """飞书卡片 → 本地 Markdown → Notion → Zotero。出口和文案全部来自 track["push"]。"""
    push = track.get("push") or {}
    feishu = push.get("feishu") or {}
    notes_cfg = push.get("notes") or {}
    notion_cfg = push.get("notion") or {}
    zotero_cfg = push.get("zotero") or {}
    today_s = date.today().isoformat()

    # 1. 飞书卡片
    webhook_env = feishu.get("webhook_env") or "FEISHU_WEBHOOK_URL"
    url = os.environ.get(webhook_env) or ""
    if url:
        date_str = today_s + (feishu.get("date_suffix") or "")
        send_digest(
            url,
            selected,
            date_str,
            categories=groups,
            insight=insight,
            color=feishu.get("color") or "blue",
            title_prefix=feishu.get("title_prefix") or "📚 Paper Radar",
        )
    else:
        log.warning("未设置 %s，跳过飞书推送", webhook_env)

    # 2. 笔记沉淀：本地 Markdown + 可选 Notion
    save_notes(
        selected,
        today_s,
        kind=notes_cfg.get("kind") or "ai",
        title=notes_cfg.get("title") or "",
        insight=insight,
        groups=groups,
    )
    notion = notion_client_from_env(notion_cfg.get("kind") or "ai")
    if notion:
        try:
            notion.save_digest(
                selected,
                today_s,
                title=notes_cfg.get("title") or "",
                insight=insight,
                groups=groups,
            )
        except Exception as e:
            log.error("Notion 写入失败: %s", e)
    else:
        log.info("未配置 NOTION_API_KEY / NOTION_PAGE_ID，跳过 Notion")

    # 3. Zotero 入库
    if os.environ.get("DRY_RUN") == "1":
        log.info("DRY_RUN：跳过 Zotero")
        return
    zotero_key = os.environ.get("ZOTERO_API_KEY", "")
    if zotero_key:
        try:
            client = ZoteroClient(zotero_key, os.environ.get("ZOTERO_USER_ID") or None)
            client.save_papers(selected, collection=zotero_cfg.get("collection") or "")
        except Exception as e:
            log.error("Zotero 写入失败: %s", e)
    else:
        log.warning("未设置 ZOTERO_API_KEY，跳过 Zotero")


def _mark_seen(path: str, seen_list: list[str], papers: list[Paper], seen: set[str]) -> None:
    """把论文的多重身份 key 写入 seen，保证跨源、跨天都不重复推送。"""
    new_keys: list[str] = []
    for p in papers:
        for k in identity_keys(p):
            if k not in seen:
                seen.add(k)
                new_keys.append(k)
    save_seen(path, seen_list + new_keys)
