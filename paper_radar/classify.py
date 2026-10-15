from __future__ import annotations

from .models import Paper


def classify_keywords(papers: list[Paper], categories: list[dict]) -> None:
    """关键词预分类：标题/摘要命中哪个类的关键词最多就归哪类，同分取配置顺序靠前的。"""
    if not categories:
        return
    for p in papers:
        text = f"{p.title} {p.abstract}".lower()
        best_id, best_score = "other", 0
        for cat in categories:
            s = 0.0
            for kw in cat.get("keywords") or []:
                k = kw.lower().strip()
                if not k:
                    continue
                if k in p.title.lower():
                    s += 2.0
                if k in text:
                    s += 1.0
            if s > best_score:
                best_id, best_score = cat.get("id", "other"), s
        p.category = best_id


def select_with_quota(
    papers: list[Paper],
    categories: list[dict],
    limit: int,
    per_cat_min: int = 2,
) -> list[Paper]:
    """分类保底 + 按分数补满。

    先给每个类保底 per_cat_min 篇（该类内部按分数取），再全局按分数把剩余名额填满，
    避免某一类（比如顶刊扎堆的 llm）把当天 10 篇全吃掉。
    """
    if not papers:
        return []
    if not categories:
        return sorted(papers, key=lambda x: (x.score, x.date), reverse=True)[:limit]

    classify_keywords(papers, categories)
    by_cat: dict[str, list[Paper]] = {}
    for p in papers:
        by_cat.setdefault(p.category, []).append(p)

    def _rank(items: list[Paper]) -> list[Paper]:
        return sorted(items, key=lambda x: (x.score, x.date), reverse=True)

    selected: list[Paper] = []
    used: set[str] = set()

    # 1) 每类保底
    for cat in categories:
        for p in _rank(by_cat.get(cat.get("id"), []))[:per_cat_min]:
            if len(selected) >= limit:
                break
            if p.source_id not in used:
                selected.append(p)
                used.add(p.source_id)

    # 2) 全局按分数补满（含 other）
    for p in _rank(papers):
        if len(selected) >= limit:
            break
        if p.source_id not in used:
            selected.append(p)
            used.add(p.source_id)
    return selected


def category_label(categories: list[dict], cat_id: str) -> str:
    for cat in categories:
        if cat.get("id") == cat_id:
            return cat.get("label", cat_id)
    return "📌 其他"


def group_by_category(papers: list[Paper], categories: list[dict]) -> list[tuple[str, list[Paper]]]:
    """按配置顺序分组，未命中的归「其他」垫底。"""
    order = [c.get("id", "other") for c in categories]
    buckets: dict[str, list[Paper]] = {cid: [] for cid in order}
    buckets.setdefault("other", [])
    for p in papers:
        buckets.setdefault(p.category, []).append(p)

    groups = []
    for cid in order:
        if buckets.get(cid):
            groups.append((category_label(categories, cid), buckets[cid]))
    if buckets.get("other"):
        groups.append(("📌 其他", buckets["other"]))
    # 配置里没列到的意外分类
    for cid, items in buckets.items():
        if cid not in order and cid != "other" and items:
            groups.append((category_label(categories, cid), items))
    return groups
