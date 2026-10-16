from __future__ import annotations

import re

from .models import Paper

# arXiv 自己的 DOI（10.48550/...）不标识出版去向，跨源匹配时不用
_ARXIV_DOI = re.compile(r"^10\.48550/", re.I)


def title_fingerprint(title: str) -> str:
    """标题指纹：小写、去标点、折叠空白。同一篇的 arXiv 版/会议版通常一致。"""
    t = (title or "").lower()
    t = re.sub(r"[^a-z0-9一-鿿]+", " ", t)
    return re.sub(r"\s+", " ", t).strip()[:120]


def paper_key(p: Paper) -> str:
    """出版方 DOI 优先，其次 arXiv id，最后标题指纹。"""
    doi = (p.doi or "").strip()
    if doi and not _ARXIV_DOI.match(doi):
        return f"doi:{doi.lower()}"
    if p.source_id.startswith("arxiv:"):
        return p.source_id
    fp = title_fingerprint(p.title)
    return f"title:{fp}" if fp else p.source_id


def identity_keys(p: Paper) -> list[str]:
    """一篇论文在 seen 里应记下的所有 key（DOI/arXiv id + 标题指纹）。

    标题指纹保证：先看到 arXiv 预印本、后来 CHI 录用版，不会再推一遍。
    """
    keys = [paper_key(p)]
    fp = title_fingerprint(p.title)
    if fp:
        keys.append(f"title:{fp}")
    # 去重保序
    out, seen = [], set()
    for k in keys:
        if k not in seen:
            seen.add(k)
            out.append(k)
    return out


def is_seen(p: Paper, seen_keys: set[str]) -> bool:
    return any(k in seen_keys for k in identity_keys(p))


def _source_rank(p: Paper) -> int:
    """出处硬度：会议/期刊 > OpenReview > arXiv 预印本。"""
    if p.source_id.startswith(("journal:", "conf:")):
        return 0
    if p.source_id.startswith("openreview:"):
        return 1
    return 2


def dedupe_papers(papers: list[Paper]) -> list[Paper]:
    """同一批里身份相同的只留一篇，优先留下处更硬的那条。"""
    best: dict[str, Paper] = {}
    order: list[str] = []
    for p in papers:
        key = paper_key(p)
        fp = title_fingerprint(p.title)
        group = f"title:{fp}" if fp else key
        if group not in best:
            best[group] = p
            order.append(group)
        elif _source_rank(p) < _source_rank(best[group]):
            best[group] = p
    return [best[g] for g in order]
