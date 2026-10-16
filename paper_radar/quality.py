from __future__ import annotations

import logging
import re
import time

import requests

from .models import Paper

log = logging.getLogger(__name__)

S2_BATCH = "https://api.semanticscholar.org/graph/v1/paper/batch"
S2_FIELDS = "title,venue,publicationVenue,year,citationCount,influentialCitationCount"
DBLP_API = "https://dblp.org/search/publ/api"

# 顶会/顶刊别名（小写）。venue 字符串里出现任一别名即认定录用。
# 注意顺序：具体刊名放在宽泛词前面，避免 "Nature Machine Intelligence" 被 "Nature" 抢先命中。
DEFAULT_TOP_VENUES = {
    "Nature Machine Intelligence": ["nature machine intelligence", "nat mach intell", "natmachintell"],
    "Nature Human Behaviour": ["nature human behaviour", "nature human behavior", "nat hum behav"],
    "NeurIPS": ["neurips", "nips", "neural information processing systems"],
    "ICLR": ["iclr", "international conference on learning representations"],
    "ICML": ["icml", "international conference on machine learning"],
    "AAAI": ["aaai", "aaai conference"],
    "IJCAI": ["ijcai"],
    "ACL": ["acl", "annual meeting of the association for computational linguistics"],
    "EMNLP": ["emnlp", "empirical methods in natural language processing"],
    "NAACL": ["naacl"],
    "COLM": ["colm", "conference on language modeling"],
    "TMLR": ["tmlr", "transactions on machine learning research"],
    "JMLR": ["jmlr", "journal of machine learning research"],
    "CVPR": ["cvpr", "computer vision and pattern recognition"],
    "ICCV": ["iccv", "international conference on computer vision"],
    "ECCV": ["eccv"],
    "ACM TOCHI": ["tochi", "transactions on computer-human interaction"],
    "PACM HCI": ["pacm hci", "proceedings of the acm on human-computer interaction", "pacmhci"],
    "CHI": ["chi conference", "human factors in computing systems", "acm chi"],
    "UIST": ["uist", "user interface software and technology"],
    "CSCW": ["cscw", "computer supported cooperative work"],
    "AIES": ["aies", "ai ethics and society"],
    "FAccT": ["facct", "fairness accountability transparency"],
    "ICWSM": ["icwsm", "web and social media"],
    "AIED": ["aied", "artificial intelligence in education"],
    "L@S": ["learning at scale", "l@s"],
    "PNAS": ["pnas", "proceedings of the national academy"],
    "Science": ["science"],
    "Nature": ["nature"],
}

# 含这些词直接扔：立场文/短文/研讨会摘要不是精读对象
EXCLUDE_PATTERNS = re.compile(
    r"\b(position paper|workshop|extended abstract|doctoral symposium|panel summary|"
    r"call for papers|preface|keynote abstract)\b",
    re.I,
)
MIN_ABSTRACT_CHARS = 150


def exclude_heuristics(papers: list[Paper]) -> list[Paper]:
    """排除式启发：摘要过短、position paper / workshop 文直接扔。

    顶刊源（journal:）即便摘要缺失也保留（RSS 有时只给引文串，标题是可信的）。
    """
    kept: list[Paper] = []
    for p in papers:
        text = f"{p.title} {p.abstract}"
        is_journal = p.source_id.startswith("journal:")
        if len(p.abstract or "") < MIN_ABSTRACT_CHARS and not is_journal:
            log.info("排除（摘要过短）: %s", p.title[:50])
            continue
        if EXCLUDE_PATTERNS.search(text):
            log.info("排除（非全文研究）: %s", p.title[:50])
            continue
        kept.append(p)
    return kept


def verify_venues(papers: list[Paper], top_venues: dict | None = None) -> None:
    """查证录用去向：Semantic Scholar 优先，DBLP 兜底。"""
    aliases = top_venues or DEFAULT_TOP_VENUES
    for p in papers:
        # 顶刊源：刊名即出处
        if p.source_id.startswith("journal:"):
            p.accepted_at = _match_top_venue(p.venue, aliases) or p.venue
            continue
        # ACM 会议源（CHI/UIST）：会议名即出处
        if p.source_id.startswith("conf:"):
            p.accepted_at = _match_top_venue(p.venue, aliases) or p.venue.split()[0]
            continue
        # OpenReview 来源本身就是按顶会 venueid 捞的已录用稿
        if p.source_id.startswith("openreview:"):
            p.accepted_at = _match_top_venue(p.venue, aliases) or p.venue.split()[0]
    tried = _from_semantic_scholar(papers, aliases)
    for p in papers:
        if p.source_id.startswith("arxiv:") and p not in tried and not p.accepted_at:
            _from_dblp(p, aliases)


def apply_venue_boost(papers: list[Paper]) -> None:
    """已录用顶会的论文在排序里加权（保持视野：只升序，不淘汰）。"""
    for p in papers:
        if p.accepted_at:
            p.score += 5.0


def apply_venue_labels(papers: list[Paper]) -> None:
    """把出处写清楚：录用到哪就标哪，顶刊标刊名，纯预印本才标 arXiv。"""
    for p in papers:
        if p.accepted_at:
            # 优先展示录用去向（带年份更清楚）
            year = (p.date or "")[:4]
            p.venue = f"{p.accepted_at} {year}" if year and year not in p.accepted_at else p.accepted_at
        elif p.source_id.startswith("journal:"):
            # 顶刊源：venue 已是期刊名，保留
            pass
        elif p.source_id.startswith("openreview:"):
            # OpenReview 未匹配到顶会别名时，venue 可能是 venueid，清理一下
            v = p.venue.replace(".cc", "").replace("/Conference", "").replace("/", " ").strip()
            p.venue = v or "OpenReview"
        elif p.source_id.startswith("arxiv:"):
            cat = p.venue.replace("arXiv ", "").strip() if p.venue.startswith("arXiv ") else ""
            p.venue = f"arXiv preprint ({cat})" if cat else "arXiv preprint"


# ---------- Semantic Scholar ----------


def _from_semantic_scholar(papers: list[Paper], aliases: dict) -> list[Paper]:
    """返回成功查询到的 arXiv 论文列表（其余交给 DBLP 兜底）。"""
    arxiv_papers = [p for p in papers if p.source_id.startswith("arxiv:")]
    if not arxiv_papers:
        return []
    ids = [p.source_id.replace("arxiv:", "arXiv:") for p in arxiv_papers]
    results = None
    for attempt in range(3):
        try:
            r = requests.post(
                S2_BATCH,
                params={"fields": S2_FIELDS},
                json={"ids": ids},
                headers={"User-Agent": "paper-radar/1.0"},
                timeout=30,
            )
            if r.status_code == 429:
                time.sleep(5 * (attempt + 1))
                continue
            r.raise_for_status()
            results = r.json()
            break
        except Exception as e:
            log.warning("Semantic Scholar 查询失败（%d/3）: %s", attempt + 1, e)
            time.sleep(3 * (attempt + 1))
    if results is None:
        return []

    answered: list[Paper] = []
    for p, data in zip(arxiv_papers, results):
        if not data:
            continue
        answered.append(p)
        p.citations = int(data.get("citationCount") or 0)
        pub = data.get("publicationVenue")
        pub_name = pub.get("name", "") if isinstance(pub, dict) else str(pub or "")
        venue_str = f"{data.get('venue') or ''} {pub_name}"
        hit = _match_top_venue(venue_str, aliases)
        if hit:
            p.accepted_at = hit
            log.info("录用查证: %s -> %s (引用 %d)", p.title[:40], hit, p.citations)
    return answered


# ---------- DBLP 兜底 ----------


def _from_dblp(p: Paper, aliases: dict) -> None:
    try:
        r = requests.get(
            DBLP_API,
            params={"q": f"t:{p.title[:80]}", "format": "json", "h": 3},
            headers={"User-Agent": "paper-radar/1.0"},
            timeout=20,
        )
        r.raise_for_status()
        hits = r.json().get("result", {}).get("hits", {}).get("hit", []) or []
    except Exception as e:
        log.warning("DBLP 查询失败 [%s]: %s", p.title[:30], e)
        return
    for h in hits:
        info = h.get("info", {})
        hit = _match_top_venue(str(info.get("venue", "")), aliases)
        if hit:
            p.accepted_at = hit
            log.info("DBLP 录用查证: %s -> %s", p.title[:40], hit)
            return


def _match_top_venue(venue_str: str, aliases: dict) -> str:
    v = (venue_str or "").lower()
    if not v:
        return ""
    for name, keys in aliases.items():
        for k in keys:
            if k.lower() in v:
                return name
    return ""
