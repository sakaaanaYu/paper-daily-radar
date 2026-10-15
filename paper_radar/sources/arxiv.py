from __future__ import annotations

import logging
import re
import time

import feedparser
import requests

from ..models import Paper

log = logging.getLogger(__name__)

API = "http://export.arxiv.org/api/query"
UA = "paper-radar/1.0 (research digest; +https://github.com/sakaaanaYu/paper-daily-radar)"


def fetch_arxiv(categories: list[str], max_results: int = 80) -> list[Paper]:
    """按分类抓取 arXiv 最新提交。categories 如 ["cs.HC", "cs.AI"]。"""
    if not categories:
        return []
    query = "+OR+".join(f"cat:{c}" for c in categories)
    url = (
        f"{API}?search_query={query}"
        f"&sortBy=submittedDate&sortOrder=descending&max_results={max_results}"
    )
    body = _get_with_retry(url)
    if body is None:
        return []

    feed = feedparser.parse(body)
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
                pdf_url=pdf_url or f"https://arxiv.org/pdf/{arxiv_id}.pdf",
                keywords=cats[:5],
            )
        )
    log.info("arXiv: %d 篇", len(papers))
    return papers


def _get_with_retry(url: str, tries: int = 3) -> str | None:
    """arXiv 偶发返回错误页/503，重试后再交给 feedparser 解析。"""
    for i in range(tries):
        try:
            r = requests.get(url, headers={"User-Agent": UA}, timeout=30)
            if r.status_code == 200 and r.text.lstrip().startswith("<?xml"):
                return r.text
            log.warning("arXiv 响应异常 status=%s，重试 %d/%d", r.status_code, i + 1, tries)
        except requests.RequestException as e:
            log.warning("arXiv 请求失败 %s，重试 %d/%d", e, i + 1, tries)
        time.sleep(2 * (i + 1))
    log.error("arXiv 抓取失败，已重试 %d 次", tries)
    return None


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()
