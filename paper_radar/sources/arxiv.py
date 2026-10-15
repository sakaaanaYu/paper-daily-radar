from __future__ import annotations

import logging
import re
import urllib.parse

import feedparser

from ..models import Paper

log = logging.getLogger(__name__)

API = "http://export.arxiv.org/api/query"


def fetch_arxiv(categories: list[str], max_results: int = 80) -> list[Paper]:
    """按分类抓取 arXiv 最新提交。categories 如 ["cs.HC", "cs.AI"]。"""
    if not categories:
        return []
    query = "+OR+".join(f"cat:{c}" for c in categories)
    url = (
        f"{API}?search_query={query}"
        f"&sortBy=submittedDate&sortOrder=descending&max_results={max_results}"
    )
    feed = feedparser.parse(url)
    if feed.get("bozo") and not feed.entries:
        log.warning("arXiv 解析失败: %s", feed.get("bozo_exception"))
        return []

    papers: list[Paper] = []
    for e in feed.entries:
        raw_id = e.get("id", "")
        if "/abs/" not in raw_id:
            continue
        arxiv_id = re.sub(r"v\d+$", "", raw_id.split("/abs/")[-1])

        pdf_url = ""
        for link in e.get("links", []):
            if link.get("title") == "pdf" or link.get("href", "").endswith(".pdf"):
                pdf_url = link["href"]
                break

        cats = [t.get("term", "") for t in e.get("tags", []) if t.get("term")]
        published = (e.get("published") or "")[:10]
        papers.append(
            Paper(
                source_id=f"arxiv:{arxiv_id}",
                title=_clean(e.get("title", "")),
                abstract=_clean(e.get("summary", "")),
                authors=[a.get("name", "") for a in e.get("authors", []) if a.get("name")],
                date=published,
                url=f"https://arxiv.org/abs/{arxiv_id}",
                venue=f"arXiv {cats[0]}" if cats else "arXiv",
                pdf_url=pdf_url or f"https://arxiv.org/pdf/{arxiv_id}",
                keywords=cats[:5],
            )
        )
    log.info("arXiv: %d 篇", len(papers))
    return papers


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()
