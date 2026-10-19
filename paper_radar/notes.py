from __future__ import annotations

import logging
import re
from pathlib import Path

from .models import Paper

log = logging.getLogger(__name__)

NOTES_DIR = Path(__file__).resolve().parent.parent / "notes"

CATEGORY_LABELS = {
    "trust": "⚖️ 可信人工智能",
    "hci": "🧑‍💻 人机交互",
    "llm": "🗣 语言模型与智能体",
    "ai4s": "🔬 AI4S应用与科学",
    "fresh": "📰 新文",
    "backfill": "📎 回补精选",
    "other": "其他",
}


def _label(cat: str) -> str:
    return CATEGORY_LABELS.get(cat or "", cat or "其他")


def _split_summary(zh: str) -> dict[str, str]:
    """把 **问题**/**方法**/**结论**/**亮点** 四行拆开，方便在 Notion 里分字段写笔记。"""
    out: dict[str, str] = {}
    for key in ("问题", "方法", "结论", "亮点"):
        m = re.search(rf"\*\*{key}\*\*[：:]\s*(.+?)(?=\n\*\*|$)", zh or "", flags=re.S)
        if m:
            out[key] = m.group(1).strip()
    return out


def render_markdown(
    papers: list[Paper],
    date_str: str,
    title: str,
    insight: str = "",
    groups: list[dict] | None = None,
) -> str:
    """一页 Markdown 日报：导读 + 空白笔记区，方便二次整理。"""
    lines: list[str] = [
        f"# {title} · {date_str}",
        "",
        f"> 共 {len(papers)} 篇 · 由 Paper Radar 自动整理，「我的笔记」留白供你补充。",
        "",
    ]
    if insight:
        lines += ["## 🧭 今日总结与研究启发", ""]
        for ln in (insight or "").splitlines():
            if ln.strip():
                lines.append(ln.strip())
        lines.append("")

    # 按分类分组展示
    grouped: dict[str, list[Paper]] = {}
    order: list[str] = []
    if groups:
        for g in groups:
            grouped[g["id"]] = []
            order.append(g["id"])
    for p in papers:
        grouped.setdefault(p.category, []).append(p)
        if p.category not in order:
            order.append(p.category)

    for cat in order:
        items = grouped.get(cat) or []
        if not items:
            continue
        lines += [f"## {_label(cat)}", ""]
        for p in items:
            lines += _paper_md(p)
    return "\n".join(lines).rstrip() + "\n"


def _paper_md(p: Paper) -> list[str]:
    venue = p.accepted_at or p.venue or ""
    parts = _split_summary(p.zh_summary)
    out = [
        f"### {p.title}",
        "",
        f"- **出处**：{venue} · {p.date}",
        f"- **链接**：{p.url}" + (f" · [PDF]({p.pdf_url})" if p.pdf_url else ""),
    ]
    if p.doi:
        out.append(f"- **DOI**：{p.doi}")
    if p.quality:
        out.append(f"- **质量分**：{p.quality}/5")
    if p.why_read:
        out.append(f"- **一句话看点**：{p.why_read}")
    out.append("")
    for key in ("问题", "方法", "结论", "亮点"):
        val = parts.get(key) or ""
        if val:
            out.append(f"**{key}**：{val}")
            out.append("")
    if not parts and p.zh_summary:
        out += [p.zh_summary, ""]
    out += [
        "**我的笔记**：",
        "",
        "<!-- 在这里写你的想法、可迁移的点、待查文献 -->",
        "",
        "---",
        "",
    ]
    return out


def save_notes(papers: list[Paper], date_str: str, kind: str, title: str, insight: str = "", groups: list[dict] | None = None) -> Path | None:
    """写到 notes/YYYY-MM-DD-{kind}.md。"""
    if not papers:
        return None
    NOTES_DIR.mkdir(parents=True, exist_ok=True)
    path = NOTES_DIR / f"{date_str}-{kind}.md"
    md = render_markdown(papers, date_str, title, insight=insight, groups=groups)
    path.write_text(md, encoding="utf-8")
    log.info("Markdown 笔记已写入 %s", path.name)
    return path
