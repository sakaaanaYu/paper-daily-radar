from __future__ import annotations

import logging
import re
from html import unescape

import feedparser
import requests

from ..models import Paper

log = logging.getLogger(__name__)

UA = "paper-radar/1.0 (research digest)"
OPENALEX = "https://api.openalex.org/works/doi:{doi}"

# ACM 的 RSS 摘要只是引文串（"Volume 33, Issue 5, Page 1-36, ..."），不是真摘要
_CITATION_STUB = re.compile(
    r"^(volume\s+\d+|.*volume\s+\d+,\s*issue\s+\d+,\s*page\s+\d+)",
    re.I,
)

# 顶刊 RSS。entry 是"当期/最新"，不是"每日新增"，靠 seen.json 去重。
# journal id 用于 source_id 和出处展示。
JOURNAL_FEEDS = {
    "Nature Machine Intelligence": "https://www.nature.com/natmachintell.rss",
    "Nature Human Behaviour": "https://www.nature.com/nathumbehav.rss",
    "Science": "https://www.science.org/action/showFeed?type=etoc&feed=rss&jc=science",
    "PNAS": "https://www.pnas.org/action/showFeed?type=etoc&feed=rss&jc=pnas",
    # HCI 顶会/顶刊：CHI/UIST 走 ACM，不在 OpenReview
    "ACM TOCHI": "https://dl.acm.org/action/showFeed?type=etoc&feed=rss&jc=tochi",
    "PACM HCI (CSCW/IUI)": "https://dl.acm.org/action/showFeed?type=etoc&feed=rss&jc=pacmhci",
}


def fetch_journals(journals: dict[str, str] | None = None, max_per_journal: int = 20) -> list[Paper]:
    """按顶刊 RSS 抓最新文章。journals: {显示名: feed url}。"""
    feeds = journals or JOURNAL_FEEDS
    papers: list[Paper] = []
    for name, url in feeds.items():
        try:
            r = requests.get(url, headers={"User-Agent": UA}, timeout=30)
            r.raise_for_status()
            feed = feedparser.parse(r.content)
        except Exception as e:
            log.warning("顶刊 %s 抓取失败: %s", name, e)
            continue

        n = 0
        for e in feed.entries:
            if n >= max_per_journal:
                break
            p = _to_paper(e, name)
            if p is None:
                continue
            papers.append(p)
            n += 1
        log.info("顶刊 %s: %d 篇", name, n)
    return papers


def _to_paper(e, journal: str) -> Paper | None:
    link = e.get("link", "")
    title = _strip_html(e.get("title", ""))
    if not title or not link:
        return None

    # DOI 或链接做稳定 id
    doi = ""
    for t in (e.get("tags") or []):
        term = t.get("term", "") if isinstance(t, dict) else str(t)
        if term.lower().startswith("10."):
            doi = term
            break
    if not doi:
        m = re.search(r"(10\.\d{4,9}/[^\s\"'<>?]+)", link)
        if m:
            doi = m.group(1)
    doi = doi.split("?")[0].strip()
    raw_id = doi or link.rstrip("/").split("/")[-1]
    source_id = f"journal:{_slug(journal)}:{raw_id}"

    abstract = _strip_html(e.get("summary", e.get("description", "")) or "")
    # ACM RSS 只给引文串，去 OpenAlex 补真摘要
    if not abstract or len(abstract) < 150 or _CITATION_STUB.search(abstract):
        enriched = _abstract_from_openalex(doi) if doi else ""
        if enriched:
            abstract = enriched

    authors = []
    for a in e.get("authors") or []:
        if isinstance(a, dict):
            authors.append(a.get("name", ""))
        else:
            authors.append(str(a))
    if not authors:
        # Nature RSS 有时把作者塞在 author 字段
        authors = [x.strip() for x in (e.get("author") or "").split(";") if x.strip()]

    date = e.get("published") or e.get("updated") or ""
    date = str(date)[:10]

    pdf = ""
    for l in e.get("links") or []:
        if (l.get("type") or "").startswith("application/pdf") or (l.get("href") or "").endswith(".pdf"):
            pdf = l.get("href", "")
            break

    return Paper(
        source_id=source_id,
        title=title,
        abstract=abstract,
        authors=authors,
        date=date,
        url=link,
        venue=journal,  # 出处直接标期刊名
        pdf_url=pdf,
    )


def _abstract_from_openalex(doi: str) -> str:
    """OpenAlex 有完整 abstract_inverted_index，用来补 ACM RSS 缺失的摘要。"""
    if not doi:
        return ""
    try:
        r = requests.get(
            OPENALEX.format(doi=doi),
            headers={"User-Agent": UA},
            timeout=20,
        )
        if r.status_code != 200:
            return ""
        inv = (r.json() or {}).get("abstract_inverted_index") or {}
    except Exception as e:
        log.debug("OpenAlex 摘要补全失败 %s: %s", doi, e)
        return ""
    if not inv:
        return ""
    pos: dict[int, str] = {}
    for word, idxs in inv.items():
        for i in idxs:
            pos[i] = word
    return " ".join(pos[i] for i in sorted(pos))


def _strip_html(text: str) -> str:
    text = re.sub(r"<[^>]+>", " ", text or "")
    return re.sub(r"\s+", " ", unescape(text)).strip()


def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
