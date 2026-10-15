from __future__ import annotations

from .models import Paper


def rank_and_filter(papers: list[Paper], keywords: list[str], limit: int) -> list[Paper]:
    """关键词打分：标题命中权重高，摘要命中次之。返回相关度最高的 limit 篇。"""
    if not papers:
        return []
    if not keywords:
        for p in papers[:limit]:
            p.score = 1.0
        return papers[:limit]

    scored: list[Paper] = []
    for p in papers:
        title_l = p.title.lower()
        abs_l = (p.abstract or "").lower()
        s = 0.0
        for kw in keywords:
            k = kw.lower().strip()
            if not k:
                continue
            if k in title_l:
                s += 3.0
            if k in abs_l:
                s += 1.0
        if s > 0:
            p.score = s
            scored.append(p)

    scored.sort(key=lambda x: (x.score, x.date), reverse=True)
    return scored[:limit]
