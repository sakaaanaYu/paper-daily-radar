from __future__ import annotations

import logging

import requests

from ..models import Paper

log = logging.getLogger(__name__)


def send_digest(webhook_url: str, papers: list[Paper], date_str: str) -> bool:
    """发飞书群机器人日报卡片。没有配置 webhook 或无论文则跳过。"""
    if not webhook_url or not papers:
        return False

    elements = []
    for i, p in enumerate(papers):
        stars = "⭐" * (p.quality or 3)
        takeaway = p.why_read or p.zh_summary or (p.abstract or "")[:100]
        line = (
            f"**[{_safe(p.title)}]({p.url})**\n"
            f"{stars} · {_safe(p.venue)} · {p.date}\n"
            f"{_safe(takeaway)}"
        )
        elements.append({"tag": "div", "text": {"tag": "lark_md", "content": line}})
        if i < len(papers) - 1:
            elements.append({"tag": "hr"})

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
        log.info("飞书日报已发送: %d 篇", len(papers))
        return True
    except Exception as e:
        log.error("飞书发送失败: %s", e)
        return False


def _safe(text: str) -> str:
    """避免 lark_md 的链接/强调语法被标题里的字符破坏。"""
    return (text or "").replace("[", "［").replace("]", "］").replace("\n", " ").strip()
