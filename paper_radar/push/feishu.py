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
) -> bool:
    """发飞书群机器人日报卡片，按分类分组。"""
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
            stars = "⭐" * (p.quality or 3)
            # 出处优先写录用去向/刊名；纯预印本才落回 arXiv
            venue = _safe(p.venue or p.accepted_at or "")
            if p.accepted_at and p.accepted_at.lower() not in venue.lower():
                venue = f"✅ {_safe(p.accepted_at)}"
            takeaway = p.why_read or p.zh_summary or (p.abstract or "")[:100]
            line = (
                f"**[{_safe(p.title)}]({p.url})**\n"
                f"📍 {venue} · {stars} · {p.date}\n"
                f"{_safe(takeaway)}"
            )
            elements.append({"tag": "div", "text": {"tag": "lark_md", "content": line}})

    payload = {
        "msg_type": "interactive",
        "card": {
            "config": {"wide_screen_mode": True},
            "header": {
                "title": {
                    "tag": "plain_text",
                    "content": f"📚 Paper Radar | {date_str} · 新增 {len(papers)} 篇",
                },
                "template": "blue",
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
