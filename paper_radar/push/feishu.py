from __future__ import annotations

import logging

import requests

from ..classify import group_by_category
from ..models import Paper

log = logging.getLogger(__name__)


def send_digest(
    webhook_url: str,
    papers: list[Paper],
    date_str: str,
    categories: list[dict] | None = None,
    insight: str = "",
    color: str = "blue",
    title_prefix: str = "📚 Paper Radar",
) -> bool:
    """发飞书群机器人日报卡片，按分类分组；insight 为今日总结（可选）。

    color 是飞书卡片头颜色：AI 用 blue，传播用 purple，一眼能分清是哪个机器人。
    """
    if not webhook_url or not papers:
        return False

    groups = group_by_category(papers, categories or [])
    elements: list[dict] = []
    for gi, (label, items) in enumerate(groups):
        if gi > 0:
            elements.append({"tag": "hr"})
        elements.append(
            {
                "tag": "div",
                "text": {
                    "tag": "lark_md",
                    "content": f"**{label}** （{len(items)} 篇）",
                },
            }
        )
        for p in items:
            # 出处优先写录用去向/刊名；纯预印本才落回 arXiv
            venue = _safe(p.venue or p.accepted_at or "")
            if p.accepted_at and p.accepted_at.lower() not in venue.lower():
                venue = f"✅ {_safe(p.accepted_at)}"

            # 卡片放四段短导读（zh_short）；长导读在 Notion / notes 里。
            # 短版缺失时退回长版，四段结构相同，卡片不至于空着。
            parts = [f"**[{_safe(p.title)}]({p.url})**", f"📍 {venue} · {p.date}"]
            if p.why_read:
                parts.append(f"💡 {_safe(p.why_read)}")
            body = (p.zh_short or p.zh_summary or "").strip()
            if body:
                parts.append(_safe_multiline(body))
            elif not p.why_read:
                parts.append("_（导读未生成：配置 LLM_API_KEY 后自动产出）_")
            line = "\n".join(parts)
            elements.append({"tag": "div", "text": {"tag": "lark_md", "content": line}})

    if insight:
        elements.append({"tag": "hr"})
        elements.append(
            {
                "tag": "div",
                "text": {"tag": "lark_md", "content": f"**🧭 今日总结与研究启发**\n{_safe_multiline(insight)}"},
            }
        )

    payload = {
        "msg_type": "interactive",
        "card": {
            "config": {"wide_screen_mode": True},
            "header": {
                "title": {
                    "tag": "plain_text",
                    "content": f"{title_prefix} | {date_str} · 新增 {len(papers)} 篇",
                },
                "template": color,
            },
            "elements": elements,
        },
    }
    try:
        r = requests.post(webhook_url, json=payload, timeout=30)
        r.raise_for_status()
        data = r.json()
        # 飞书业务错误也可能返回 HTTP 200
        if data.get("code") not in (0, None):
            log.error("飞书返回错误: %s", data)
            return False
        log.info("飞书日报已发送: %d 篇 / %d 个分类", len(papers), len(groups))
        return True
    except Exception as e:
        log.error("飞书发送失败: %s", e)
        return False


def _safe(text: str) -> str:
    """避免 lark_md 的链接/强调语法被标题里的字符破坏。"""
    return (text or "").replace("[", "［").replace("]", "］").replace("\n", " ").strip()


def _safe_multiline(text: str) -> str:
    """保留段内换行（导读四要点/今日总结用），只抹掉破坏链接的括号。"""
    lines = [ln.strip() for ln in (text or "").split("\n")]
    out = []
    for ln in lines:
        if ln:
            out.append(ln.replace("[", "［").replace("]", "］"))
    return "\n".join(out)
