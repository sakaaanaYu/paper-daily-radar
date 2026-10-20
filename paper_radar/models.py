from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Paper:
    """一篇论文的统一表示，各数据源都转成这个结构。"""

    source_id: str  # arxiv:2610.01234 或 openreview:abc123
    title: str
    abstract: str
    authors: list[str]
    date: str  # YYYY-MM-DD
    url: str
    venue: str  # "arXiv cs.AI" / "NeurIPS 2026"
    pdf_url: str = ""
    doi: str = ""  # 出版方 DOI（10.xxxx/...），用于跨源去重
    keywords: list[str] = field(default_factory=list)
    category: str = "other"  # 分类 id，见 config.yaml categories
    score: float = 0.0  # 关键词相关度
    quality: int = 0  # 1-5 质量分（LLM 生成，0=未评）
    zh_summary: str = ""  # 四段长导读（Notion / notes / Zotero）
    zh_short: str = ""  # 四段短导读（飞书卡片）
    why_read: str = ""
    accepted_at: str = ""  # 录用查证结果，如 "NeurIPS"（空=未查到录用）
    citations: int = 0  # Semantic Scholar 引用数
