from __future__ import annotations

import logging
import re
import time
from datetime import datetime

import feedparser
import requests

from ..models import Paper

log = logging.getLogger(__name__)

# RSS 是 arXiv 为"每日新文"提供的接口，比 API 限流宽松得多
RSS_URL = "https://rss.arxiv.org/rss/{cat}"
UA = "paper-radar/1.0 (research digest)"


def fetch_arxiv(categories: list[str], max_results: int = 80) -> list[Paper]:
    """按分类抓 arXiv 当日新文（RSS）。categories 如 ["cs.HC", "cs.AI"]。"""
    if not categories:
        return []

    papers: list[Paper] = []
    seen_ids: set[str] = set()
    for cat in categories:
        body = _get_with_retry(RSS_URL.format(cat=cat))
        if body is None:
            continue
        feed = feedparser.parse(body)
        if feed.get("bozo") and not feed.entries:
            log.warning("arXiv %s 解析失败: %s", cat, feed.get("bozo_exception"))
            continue

        n = 0
        for e in feed.entries:
            if n >= max_results:
                break
            # 只要当日"新"公告，跳过 cross-list / replacement 重复通知
            if e.get("arxiv_announce_type") not in (None, "", "new"):
                continue
            paper = _to_paper(e, cat)
            if paper is None or paper.source_id in seen_ids:
                continue
            seen_ids.add(paper.source_id)
            papers.append(paper)
            n += 1
        log.info("arXiv %s: %d 篇", cat, n)
    return papers


def _to_paper(e, fallback_cat: str) -> Paper | None:
    link = e.get("link", "")
    m = re.search(r"arxiv\.org/abs/([\w.\-]+)", link)
    if not m:
        return None
    arxiv_id = re.sub(r"v\d+$", "", m.group(1))

    summary = e.get("summary", e.get("description", "")) or ""
    # 摘要里带着 "arXiv:xxxxvN Announce Type: new\nAbstract: " 前缀
    summary = re.sub(
        r"^arXiv:[\w.\-]+\s+Announce Type:\s*\S+\s*(?:\n|$)\s*Abstract:\s*",
        "",
        summary.strip(),
        flags=re.I,
    )
    summary = re.sub(r"\s+", " ", summary).strip()

    # RSS 把所有作者塞在一个逗号分隔的 name 里
    authors: list[str] = []
    for a in e.get("authors", []):
        name = a.get("name", "")
        if "," in name:
            authors.extend([x.strip() for x in name.split(",") if x.strip()])
        elif name:
            authors.append(name.strip())
    if not authors and e.get("author"):
        authors = [x.strip() for x in e["author"].split(",") if x.strip()]

    cats = [t.get("term", "") for t in e.get("tags", []) if t.get("term")]
    date = ""
    if e.get("published_parsed"):
        date = datetime(*e["published_parsed"][:6]).strftime("%Y-%m-%d")

    # 出版方 DOI（如果有），用于和会议/期刊版本跨源去重
    doi = ""
    for k in ("prism_doi", "arxiv_doi", "dc_identifier", "doi"):
        v = e.get(k)
        if isinstance(v, str) and "10." in v:
            doi = v.strip()
            break

    return Paper(
        source_id=f"arxiv:{arxiv_id}",
        title=re.sub(r"\s+", " ", e.get("title", "")).strip(),
        abstract=summary,
        authors=authors,
        date=date,
        url=f"https://arxiv.org/abs/{arxiv_id}",
        venue=f"arXiv {cats[0] if cats else fallback_cat}",
        pdf_url=f"https://arxiv.org/pdf/{arxiv_id}.pdf",
        keywords=cats[:5],
        doi=doi,
    )


def _get_with_retry(url: str, tries: int = 3) -> str | None:
    for i in range(tries):
        try:
            r = requests.get(url, headers={"User-Agent": UA}, timeout=30)
            if r.status_code == 200 and "<rss" in r.text[:500]:
                return r.text
            log.warning("arXiv 响应异常 status=%s，重试 %d/%d", r.status_code, i + 1, tries)
        except requests.RequestException as e:
            log.warning("arXiv 请求失败 %s，重试 %d/%d", e, i + 1, tries)
        time.sleep(2 * (i + 1))
    log.error("arXiv 抓取失败，已重试 %d 次", tries)
    return None
