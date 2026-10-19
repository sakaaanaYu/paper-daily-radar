from __future__ import annotations

import logging
from datetime import date, timedelta

import requests

from ..models import Paper

log = logging.getLogger(__name__)

API = "https://api.openalex.org/works"
UA = "paper-radar/1.0 (research digest)"

# 传播学刊单：计算传播 / 科技传播为主（OpenAlex source id）
DEFAULT_JOURNALS = {
    # 计算传播 · 数字媒体（核心）
    "Journal of Computer-Mediated Communication": "S30594836",
    "New Media & Society": "S88603889",
    "Computational Communication Research": "S4210235366",
    "Information, Communication & Society": "S70010600",
    "Digital Journalism": "S2764497610",
    # 科技传播 · 科学与社会（核心）
    "Science Communication": "S30803906",
    "Public Understanding of Science": "S124907306",
    "Science, Technology & Human Values": "S2181421",
    # 传播学基础顶刊（回补保质量）
    "Journal of Communication": "S107737141",
    "Communication Research": "S28604305",
    "Human Communication Research": "S195553305",
    "Journalism": "S111964278",
}

# 刊分：计算传播 / 科技传播优先，基础顶刊次之
TIER_BONUS = {
    "Journal of Computer-Mediated Communication": 1.5,
    "New Media & Society": 1.2,
    "Science Communication": 1.5,
    "Public Understanding of Science": 1.2,
    "Computational Communication Research": 1.2,
    "Information, Communication & Society": 1.0,
    "Science, Technology & Human Values": 1.0,
    "Digital Journalism": 0.8,
    "Journal of Communication": 1.0,
    "Communication Research": 1.0,
    "Human Communication Research": 0.8,
    "Journalism": 0.5,
}


def fetch_comm_journals(
    journals: dict[str, str] | None = None,
    *,
    fresh_days: int = 30,
    backfill_min_months: int = 3,
    backfill_max_months: int = 18,
    max_per_window: int = 40,
) -> tuple[list[Paper], list[Paper]]:
    """按刊抓两池：新文（近 fresh_days）与回补（min–max 个月前）。"""
    sources = journals or DEFAULT_JOURNALS
    today = date.today()
    fresh_from = (today - timedelta(days=fresh_days)).isoformat()
    backfill_from = _shift_months(today, -backfill_max_months)
    backfill_to = _shift_months(today, -backfill_min_months)

    fresh: list[Paper] = []
    backlog: list[Paper] = []
    for name, sid in sources.items():
        f = _fetch_window(sid, name, fresh_from, today.isoformat(), max_per_window)
        b = _fetch_window(sid, name, backfill_from, backfill_to, max_per_window)
        fresh.extend(f)
        backlog.extend(b)
        if f or b:
            log.info("传播 %s: 新文 %d / 回补池 %d", name, len(f), len(b))
    return fresh, backlog


def _shift_months(d: date, months: int) -> str:
    y, m = divmod(d.month - 1 + months, 12)
    year = d.year + y
    month = m + 1
    day = min(d.day, [31, 29 if year % 4 == 0 and (year % 100 != 0 or year % 400 == 0) else 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31][month - 1])
    return date(year, month, day).isoformat()


def _fetch_window(
    sid: str, name: str, date_from: str, date_to: str, limit: int
) -> list[Paper]:
    try:
        r = requests.get(
            API,
            params={
                "filter": (
                    f"primary_location.source.id:{sid},"
                    f"from_publication_date:{date_from},to_publication_date:{date_to}"
                ),
                "per-page": limit,
                "sort": "publication_date:desc",
                "select": (
                    "id,doi,title,publication_date,abstract_inverted_index,"
                    "authorships,primary_location,cited_by_count"
                ),
            },
            headers={"User-Agent": UA},
            timeout=30,
        )
        r.raise_for_status()
        results = r.json().get("results") or []
    except Exception as e:
        log.warning("OpenAlex %s 抓取失败: %s", name, e)
        return []
    out = []
    for w in results:
        p = _to_paper(w, name)
        if p is not None:
            out.append(p)
    return out


def _to_paper(w: dict, journal: str) -> Paper | None:
    title = (w.get("title") or "").strip()
    if not title:
        return None
    doi = (w.get("doi") or "").replace("https://doi.org/", "")
    source_id = f"comm:{_slug(journal)}:{doi or (w.get('id') or '').split('/')[-1]}"
    authors = [
        (a.get("author") or {}).get("display_name", "")
        for a in (w.get("authorships") or [])
    ]
    authors = [a for a in authors if a]
    date_s = (w.get("publication_date") or "")[:10]
    year = date_s[:4]
    url = w.get("doi") or (f"https://doi.org/{doi}" if doi else (w.get("id") or ""))
    pdf = (w.get("primary_location") or {}).get("pdf_url") or ""
    return Paper(
        source_id=source_id,
        title=title,
        abstract=_deinvert(w.get("abstract_inverted_index")),
        authors=authors,
        date=date_s,
        url=url,
        venue=f"{journal} {year}".strip(),
        pdf_url=pdf or "",
        doi=doi,
        citations=int(w.get("cited_by_count") or 0),
        accepted_at=journal,  # 期刊名即出处
    )


def score_backfill(p: Paper, keywords: list[str], months_old: float) -> float:
    """回补质量分：刊分 + 引用 + 关键词相关 − 年龄惩罚。"""
    s = TIER_BONUS.get(p.accepted_at or "", 0.5)
    if p.citations >= 30:
        s += 1.5
    elif p.citations >= 15:
        s += 1.0
    elif p.citations >= 5:
        s += 0.5
    text = f"{p.title} {p.abstract}".lower()
    for kw in keywords or []:
        k = (kw or "").lower().strip()
        if k and k in text:
            s += 0.4
    s -= 0.25 * (months_old // 3)
    return s


def months_between(d_from: str, d_to: date) -> float:
    try:
        y, m, dd = map(int, d_from.split("-"))
        a = date(y, m, dd)
    except Exception:
        return 0.0
    return max(0.0, (d_to.year - a.year) * 12 + (d_to.month - a.month) + (d_to.day - a.day) / 30.0)


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
