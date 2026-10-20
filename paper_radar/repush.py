"""重推历史日报：按 notes/ 名单回源取摘要，用当前提示词重写导读，只推飞书 + Notion。

用途：改完提示词后，把旧日报刷成新文风。不写 seen、不进 Zotero、不改本地
notes（notes 里可能有你自己写的笔记）。飞书出一张带原日期的卡片，Notion 按原
日期置顶插入一节——旧的一节还在下面，上面有你笔记的话记得自己取舍。

摘要回源顺序：arXiv API → OpenAlex(DOI) → nature 页 meta → OpenReview → S2。
都失败的论文保留 notes 里的旧导读，不会推空。

用法：python -m paper_radar.repush --dates 2026-10-19 2026-10-20
"""

from __future__ import annotations

import argparse
import logging
import os
import re
import time
from html import unescape
from pathlib import Path

import feedparser
import requests

from .config import load_config
from .models import Paper
from .notes import CATEGORY_LABELS, NOTES_DIR, _split_summary
from .push.feishu import send_digest
from .push.notion import client_from_env as notion_client_from_env
from .sources.journals import _abstract_from_openalex, _strip_html
from .summarize import summarize, summarize_daily_insight

log = logging.getLogger("paper_radar.repush")

ARXIV_API = "https://export.arxiv.org/api/query"
OR_SEARCH = "https://api2.openreview.net/notes/search"
S2_BATCH = "https://api.semanticscholar.org/graph/v1/paper/batch"
UA = "paper-radar/1.0 (research digest)"


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    ap = argparse.ArgumentParser(description="重推历史日报（只推飞书 + Notion）")
    ap.add_argument(
        "--dates",
        nargs="+",
        required=True,
        help="要重推的日期，如 2026-10-19 2026-10-20；逗号分隔也可以",
    )
    args = ap.parse_args()
    dates = sorted({d.strip() for chunk in args.dates for d in chunk.split(",") if d.strip()})

    cfg = load_config()
    for date_str in dates:
        for name, track in (cfg.get("tracks") or {}).items():
            log.info("=== 重推 %s / %s ===", date_str, name)
            repush_day(date_str, track)
    return 0


# ---------------------------------------------------------------------------
# 单日单频道
# ---------------------------------------------------------------------------


def repush_day(date_str: str, track: dict) -> None:
    push = track.get("push") or {}
    notes_cfg = push.get("notes") or {}
    kind = notes_cfg.get("kind") or track.get("name") or ""
    path = NOTES_DIR / f"{date_str}-{kind}.md"
    if not path.exists():
        log.info("%s 不存在，跳过", path.name)
        return

    groups = track.get("categories") or track.get("groups") or []
    papers = parse_notes(path, groups)
    if not papers:
        log.warning("%s 未解析出论文，跳过", path.name)
        return
    log.info("%s 解析出 %d 篇", path.name, len(papers))
    fill_abstracts(papers)
    n_abs = sum(1 for p in papers if p.abstract)
    log.info("摘要回源成功 %d/%d", n_abs, len(papers))

    # 有摘要的重写导读；回源失败的保留 notes 里的旧文本
    fresh = [p for p in papers if p.abstract]
    backup = {p.source_id: (p.zh_summary, p.zh_short, p.why_read, p.quality) for p in fresh}
    if fresh:
        summarize(fresh, categories=track.get("categories") or None, update_category=False)
    for p in fresh:
        if not p.zh_summary:
            p.zh_summary, p.zh_short, p.why_read, p.quality = backup[p.source_id]
            log.warning("导读重写失败，保留旧导读：%s", p.title[:50])

    insight = summarize_daily_insight(papers)

    # 只推飞书 + Notion：不动 seen / Zotero / notes
    feishu = push.get("feishu") or {}
    webhook_env = feishu.get("webhook_env") or "FEISHU_WEBHOOK_URL"
    url = os.environ.get(webhook_env) or ""
    if url:
        send_digest(
            url,
            papers,
            date_str + (feishu.get("date_suffix") or ""),
            categories=groups,
            insight=insight,
            color=feishu.get("color") or "blue",
            title_prefix=feishu.get("title_prefix") or "📚 Paper Radar",
        )
    else:
        log.warning("未设置 %s，跳过飞书", webhook_env)

    notion = notion_client_from_env(notes_cfg.get("kind") or track.get("name") or "")
    if notion:
        try:
            notion.save_digest(
                papers,
                date_str,
                title=notes_cfg.get("title") or "",
                insight=insight,
                groups=groups,
            )
        except Exception as e:
            log.error("Notion 写入失败: %s", e)
    else:
        log.info("未配置 NOTION_API_KEY / NOTION_PAGE_ID，跳过 Notion")


# ---------------------------------------------------------------------------
# 解析 notes/*.md
# ---------------------------------------------------------------------------


def parse_notes(path: Path, groups: list[dict]) -> list[Paper]:
    """从 notes 的 Markdown 里还原论文清单（标题/链接/出处/分类/旧导读）。"""
    label_to_cat = {g.get("label"): g.get("id") for g in groups or [] if g.get("label")}
    for cid, lab in CATEGORY_LABELS.items():
        label_to_cat.setdefault(lab, cid)

    papers: list[Paper] = []
    cat = "other"
    lines = path.read_text(encoding="utf-8").splitlines()
    i = 0
    while i < len(lines):
        ln = lines[i]
        if ln.startswith("## "):
            cat = label_to_cat.get(ln[3:].strip(), "other")
        elif ln.startswith("### "):
            title = ln[4:].strip()
            block: list[str] = []
            i += 1
            while i < len(lines) and not lines[i].startswith("### ") and not lines[i].startswith("## "):
                block.append(lines[i])
                i += 1
            p = _parse_paper(title, block, cat)
            if p:
                papers.append(p)
            continue
        i += 1
    return papers


def _parse_paper(title: str, block: list[str], cat: str) -> Paper | None:
    url = pdf_url = doi = why_read = venue = date_s = ""
    quality = 0
    body: list[str] = []
    for ln in block:
        s = ln.strip()
        if s.startswith("- **出处**："):
            left = s.split("：", 1)[1].strip()
            if " · " in left:
                v, _, tail = left.rpartition(" · ")
                if re.match(r"\d{4}-\d{2}-\d{2}$", tail.strip()):
                    venue, date_s = v.strip(), tail.strip()
                else:
                    venue = left
            else:
                venue = left
        elif s.startswith("- **链接**："):
            rest = s.split("：", 1)[1]
            url = rest.split(" · ")[0].strip()
            m = re.search(r"\[PDF\]\(([^)]+)\)", rest)
            if m:
                pdf_url = m.group(1)
        elif s.startswith("- **DOI**："):
            doi = s.split("：", 1)[1].strip()
        elif s.startswith("- **质量分**："):
            m = re.search(r"(\d+)\s*/\s*5", s)
            if m:
                quality = int(m.group(1))
        elif s.startswith("- **一句话看点**："):
            why_read = s.split("：", 1)[1].strip()
        elif s.startswith("**我的笔记**"):
            break
        else:
            body.append(ln)

    zh = _rebuild_summary("\n".join(body))
    if not url and not zh:
        return None
    return Paper(
        source_id=_source_id(url, doi),
        title=title,
        abstract="",
        authors=[],
        date=date_s,
        url=url,
        venue=venue,
        pdf_url=pdf_url,
        doi=doi,
        category=cat,
        quality=quality,
        zh_summary=zh,
        why_read=why_read,
    )


def _rebuild_summary(body: str) -> str:
    """notes 里的四段拆行存盘，这里拼回 zh_summary，作回源失败时的旧导读。"""
    parts = _split_summary(body)
    if not parts:
        return body.strip()
    return "\n".join(f"**{k}**：{parts[k]}" for k in ("问题", "方法", "结论", "亮点") if parts.get(k))


def _source_id(url: str, doi: str) -> str:
    m = re.search(r"arxiv\.org/abs/([\w.\-]+)", url or "")
    if m:
        return "arxiv:" + re.sub(r"v\d+$", "", m.group(1))
    m = re.search(r"openreview\.net/forum\?id=([\w.\-]+)", url or "")
    if m:
        return "openreview:" + m.group(1)
    if doi:
        return "doi:" + doi
    return "url:" + (url or "")


# ---------------------------------------------------------------------------
# 摘要回源
# ---------------------------------------------------------------------------


def fill_abstracts(papers: list[Paper]) -> None:
    """按 arXiv → OpenAlex → nature → OpenReview → S2 的顺序补摘要，就地填 p.abstract。"""

    def todo() -> list[Paper]:
        return [p for p in papers if not p.abstract]

    _from_arxiv([p for p in todo() if p.source_id.startswith("arxiv:")])
    _from_openalex([p for p in todo() if p.doi])
    _from_nature([p for p in todo() if "nature.com/articles/" in (p.url or "")])
    _from_openreview([p for p in todo() if p.source_id.startswith("openreview:")])
    _from_s2(todo())
    for p in papers:
        if not p.abstract:
            log.warning("摘要回源失败，保留旧导读：%s", p.title[:50])


def _from_arxiv(papers: list[Paper]) -> None:
    if not papers:
        return
    ids = [p.source_id.replace("arxiv:", "") for p in papers]
    try:
        r = requests.get(
            ARXIV_API,
            params={"id_list": ",".join(ids), "max_results": len(ids)},
            headers={"User-Agent": UA},
            timeout=30,
        )
        r.raise_for_status()
        feed = feedparser.parse(r.text)
    except Exception as e:
        log.warning("arXiv 摘要回源失败: %s", e)
        return
    by_id: dict[str, object] = {}
    for e in feed.entries:
        m = re.search(r"arxiv\.org/abs/([\w.\-]+)", e.get("link") or e.get("id") or "")
        if m:
            by_id[re.sub(r"v\d+$", "", m.group(1))] = e
    for p in papers:
        e = by_id.get(p.source_id.replace("arxiv:", ""))
        if not e:
            continue
        p.abstract = _strip_html(e.get("summary") or "")
        if not p.authors:
            p.authors = [a.get("name", "") for a in e.get("authors") or [] if a.get("name")]
    log.info("arXiv 摘要回源 %d/%d", sum(1 for p in papers if p.abstract), len(papers))


def _from_openalex(papers: list[Paper]) -> None:
    for p in papers:
        text = _abstract_from_openalex(p.doi)
        if text:
            p.abstract = text
        time.sleep(0.2)  # OpenAlex 对无 key 请求有礼貌限速


def _from_nature(papers: list[Paper]) -> None:
    for p in papers:
        try:
            r = requests.get(p.url, headers={"User-Agent": "Mozilla/5.0 (paper-radar)"}, timeout=30)
            m = re.search(r'name="dc\.description" content="(.*?)"', r.text, re.S)
            if m:
                text = _strip_html(unescape(m.group(1)))
                if text:
                    p.abstract = text
        except Exception as e:
            log.warning("Nature 摘要回源失败 %s: %s", p.url, e)


def _from_openreview(papers: list[Paper]) -> None:
    for p in papers:
        forum_id = p.source_id.replace("openreview:", "")
        term = " ".join((p.title or "").split()[:8])
        try:
            r = requests.get(
                OR_SEARCH,
                params={"term": term, "limit": 20},
                headers={"User-Agent": "Mozilla/5.0 (paper-radar)"},
                timeout=30,
            )
            r.raise_for_status()
            notes = r.json().get("notes", [])
        except Exception as e:
            log.warning("OpenReview 摘要回源失败 %s: %s", p.title[:40], e)
            continue
        # 先要投稿本身（id==forum），退而求其次取同 forum 下带摘要的条目
        hit = next((n for n in notes if n.get("id") == forum_id), None) or next(
            (n for n in notes if n.get("forum") == forum_id and _or_val(n.get("content") or {}, "abstract")),
            None,
        )
        if not hit:
            continue
        c = hit.get("content") or {}
        ab = _or_val(c, "abstract")
        if ab:
            p.abstract = str(ab).strip()
        if not p.authors:
            authors = _or_val(c, "authors") or []
            if isinstance(authors, str):
                authors = [authors]
            p.authors = [str(a) for a in authors if a]
    log.info("OpenReview 摘要回源 %d/%d", sum(1 for p in papers if p.abstract), len(papers))


def _or_val(content: dict, key: str):
    v = content.get(key)
    if isinstance(v, dict):
        return v.get("value", "")
    return v or ""


def _from_s2(papers: list[Paper]) -> None:
    if not papers:
        return

    def sid(p: Paper) -> str:
        if p.source_id.startswith("arxiv:"):
            return "ARXIV:" + p.source_id.replace("arxiv:", "")
        if p.doi:
            return "DOI:" + p.doi
        return "URL:" + (p.url or "")

    results = None
    for attempt in range(3):
        try:
            r = requests.post(
                S2_BATCH,
                params={"fields": "title,abstract"},
                json={"ids": [sid(p) for p in papers]},
                headers={"User-Agent": UA},
                timeout=30,
            )
            if r.status_code == 429:
                time.sleep(5 * (attempt + 1))
                continue
            r.raise_for_status()
            results = r.json()
            break
        except Exception as e:
            log.warning("Semantic Scholar 摘要回源失败（%d/3）: %s", attempt + 1, e)
            time.sleep(3 * (attempt + 1))
    if not results:
        return
    for p, data in zip(papers, results):
        if data and data.get("abstract"):
            p.abstract = _strip_html(str(data["abstract"]))


if __name__ == "__main__":
    raise SystemExit(main())
