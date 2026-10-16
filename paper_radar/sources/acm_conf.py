from __future__ import annotations

import logging

import requests

from ..models import Paper

log = logging.getLogger(__name__)

API = "https://api.openalex.org/works"
UA = "paper-radar/1.0 (research digest)"

# ACM 会议不在 OpenReview，用 OpenAlex source id 抓（有完整摘要）
DEFAULT_VENUES = {
    "CHI": "S4363607743",
    "UIST": "S7407087746",
}


def fetch_acm_confs(venues: dict[str, str] | None = None, max_per_venue: int = 25) -> list[Paper]:
    """按 OpenAlex source id 抓 CHI/UIST 等 ACM 会议最新论文。venues: {会议名: source_id}。"""
    sources = venues or DEFAULT_VENUES
    papers: list[Paper] = []
    for name, sid in sources.items():
        try:
            r = requests.get(
                API,
                params={
                    "filter": f"primary_location.source.id:{sid}",
                    "per-page": max_per_venue,
                    "sort": "publication_date:desc",
                    "select": "id,doi,title,publication_date,abstract_inverted_index,authorships,primary_location",
                },
                headers={"User-Agent": UA},
                timeout=30,
            )
            r.raise_for_status()
            results = r.json().get("results") or []
        except Exception as e:
            log.warning("OpenAlex %s 抓取失败: %s", name, e)
            continue

        n = 0
        for w in results:
            p = _to_paper(w, name)
            if p is None:
                continue
            papers.append(p)
            n += 1
        log.info("ACM %s: %d 篇", name, n)
    return papers


def _to_paper(w: dict, conf: str) -> Paper | None:
    title = (w.get("title") or "").strip()
    if not title:
        return None

    doi = (w.get("doi") or "").replace("https://doi.org/", "")
    source_id = f"conf:{_slug(conf)}:{doi or (w.get('id') or '').split('/')[-1]}"

    abstract = _deinvert(w.get("abstract_inverted_index"))
    authors = [
        (a.get("author") or {}).get("display_name", "")
        for a in (w.get("authorships") or [])
    ]
    authors = [a for a in authors if a]

    date = (w.get("publication_date") or "")[:10]
    year = date[:4]
    url = w.get("doi") or (f"https://doi.org/{doi}" if doi else (w.get("id") or ""))

    pdf = (w.get("primary_location") or {}).get("pdf_url") or ""

    return Paper(
        source_id=source_id,
        title=title,
        abstract=abstract,
        authors=authors,
        date=date,
        url=url,
        venue=f"{conf} {year}".strip(),
        pdf_url=pdf or "",
    )


def _deinvert(inv: dict | None) -> str:
    if not inv:
        return ""
    pos: dict[int, str] = {}
    for word, idxs in inv.items():
        for i in idxs or []:
            pos[i] = word
    return " ".join(pos[i] for i in sorted(pos))


def _slug(name: str) -> str:
    import re

    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
