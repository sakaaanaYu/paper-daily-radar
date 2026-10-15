from __future__ import annotations

import logging
from datetime import datetime, timezone

import requests

from ..models import Paper

log = logging.getLogger(__name__)

API = "https://api2.openreview.net/notes/search"
# 列表接口有反爬挑战，search 接口可用；用超高频词当"取最新"的抓手
DEFAULT_TERM = "the"


def fetch_openreview(venues: list[str], max_per_venue: int = 40) -> list[Paper]:
    """按 venueid 抓取顶会最新条目，如 "NeurIPS.cc/2026/Conference"。"""
    papers: list[Paper] = []
    for venue_id in venues:
        try:
            r = requests.get(
                API,
                params={
                    "term": DEFAULT_TERM,
                    "venueid": venue_id,
                    "limit": max_per_venue,
                    "sort": "cdate:desc",
                },
                headers={"User-Agent": "Mozilla/5.0 (paper-radar)"},
                timeout=30,
            )
            r.raise_for_status()
            notes = r.json().get("notes", [])
        except Exception as e:
            log.warning("OpenReview %s 抓取失败: %s", venue_id, e)
            continue

        for note in notes:
            c = note.get("content", {})
            title = _val(c, "title")
            if not title:
                continue
            nid = note.get("id", "")
            forum = note.get("forum") or nid
            authors = _val(c, "authors") or []
            if isinstance(authors, str):
                authors = [authors]
            kws = _val(c, "keywords") or []
            if isinstance(kws, str):
                kws = [kws]
            pdf = _val(c, "pdf")
            pdf_url = f"https://openreview.net{pdf}" if isinstance(pdf, str) and pdf.startswith("/") else (
                f"https://openreview.net/pdf?id={nid}" if nid else ""
            )
            papers.append(
                Paper(
                    source_id=f"openreview:{nid}",
                    title=str(title).strip(),
                    abstract=str(_val(c, "abstract") or "").strip(),
                    authors=[str(a) for a in authors if a],
                    date=_ts_to_date(note.get("pdate") or note.get("cdate")),
                    url=f"https://openreview.net/forum?id={forum}",
                    venue=str(_val(c, "venue") or venue_id).strip(),
                    pdf_url=pdf_url,
                    keywords=[str(k) for k in kws[:6]],
                )
            )
        log.info("OpenReview %s: %d 篇", venue_id, len(notes))
    return papers


def _val(content: dict, key: str):
    """OpenReview API v2 的字段是 {"value": ...} 包一层。"""
    v = content.get(key)
    if isinstance(v, dict):
        return v.get("value", "")
    return v or ""


def _ts_to_date(ts) -> str:
    """cdate/pdate 是 Unix 毫秒时间戳。"""
    if not ts:
        return ""
    try:
        return datetime.fromtimestamp(int(ts) / 1000, tz=timezone.utc).strftime("%Y-%m-%d")
    except (TypeError, ValueError, OSError):
        return str(ts)[:10]
