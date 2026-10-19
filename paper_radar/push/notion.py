from __future__ import annotations

import logging
import os
import re

import requests

from ..models import Paper

log = logging.getLogger(__name__)

API = "https://api.notion.com/v1"
NOTION_VERSION = "2022-06-28"


class NotionClient:
    """把日报追加到同一篇 Notion 文档末尾：每天一个日期块，顺着往下记。"""

    def __init__(self, api_key: str, page_id: str):
        self.api_key = api_key
        self.page_id = page_id
        self._headers = {
            "Authorization": f"Bearer {api_key}",
            "Notion-Version": NOTION_VERSION,
            "Content-Type": "application/json",
        }

    def save_digest(
        self,
        papers: list[Paper],
        date_str: str,
        title: str,
        insight: str = "",
        groups: list[dict] | None = None,
    ) -> bool:
        if not papers:
            return False
        children = _blocks_for_digest(papers, date_str=date_str, title=title, insight=insight, groups=groups)
        # 同一篇文档往下追加，不是每天新建页
        for i in range(0, len(children), 100):
            chunk = children[i : i + 100]
            r = requests.patch(
                f"{API}/blocks/{self.page_id}/children",
                headers=self._headers,
                json={"children": chunk},
                timeout=60,
            )
            if r.status_code >= 400:
                log.error("Notion 追加失败 %s: %s", r.status_code, r.text[:300])
                return False
        log.info("Notion 已追加 %d 块到同一文档", len(children))
        return True


def _blocks_for_digest(
    papers: list[Paper],
    date_str: str = "",
    title: str = "",
    insight: str = "",
    groups: list[dict] | None = None,
) -> list[dict]:
    blocks: list[dict] = []
    # 每天一个日期分节，接在同一文档末尾
    if date_str:
        blocks.append(_heading(2, f"{title} · {date_str}".strip(" ·")))
        blocks.append({"type": "divider", "divider": {}})
    if insight:
        blocks.append(_heading(3, "🧭 今日总结与研究启发"))
        blocks += _para_blocks(insight)

    grouped: dict[str, list[Paper]] = {}
    order: list[str] = []
    labels = {g["id"]: g.get("label", g["id"]) for g in (groups or [])}
    for g in groups or []:
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
        blocks.append(_heading(2, labels.get(cat, cat)))
        for p in items:
            blocks += _paper_blocks(p)
    return blocks


def _paper_blocks(p: Paper) -> list[dict]:
    url = p.url or ""
    title_text = [{"type": "text", "text": {"content": p.title[:2000]}}]
    if url:
        title_text[0]["text"]["link"] = {"url": url}

    children: list[dict] = [
        {
            "type": "toggle",
            "toggle": {
                "rich_text": title_text,
                "children": _paper_children(p),
            },
        }
    ]
    return children


def _paper_children(p: Paper) -> list[dict]:
    venue = p.accepted_at or p.venue or ""
    kids: list[dict] = [
        {
            "type": "paragraph",
            "paragraph": {
                "rich_text": [
                    {"type": "text", "text": {"content": f"📍 {venue} · {p.date}"}},
                ]
            },
        }
    ]
    if p.why_read:
        kids.append(_para(f"💡 {p.why_read}"))
    parts = _split_summary(p.zh_summary)
    for key in ("问题", "方法", "结论", "亮点"):
        val = parts.get(key)
        if val:
            kids.append(
                {
                    "type": "paragraph",
                    "paragraph": {
                        "rich_text": [
                            {"type": "text", "text": {"content": f"{key}："}, "annotations": {"bold": True}},
                            {"type": "text", "text": {"content": val}},
                        ]
                    },
                }
            )
    if not parts and p.zh_summary:
        kids += _para_blocks(p.zh_summary)
    kids.append(_para("我的笔记："))
    kids.append(
        {
            "type": "paragraph",
            "paragraph": {
                "rich_text": [{"type": "text", "text": {"content": "　"}, "annotations": {"color": "gray"}}]
            },
        }
    )
    return kids[:100]


def _split_summary(zh: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for key in ("问题", "方法", "结论", "亮点"):
        m = re.search(rf"\*\*{key}\*\*[：:]\s*(.+?)(?=\n\*\*|$)", zh or "", flags=re.S)
        if m:
            out[key] = m.group(1).strip()
    return out


def _heading(level: int, text: str) -> dict:
    tag = f"heading_{min(3, max(1, level))}"
    return {tag: {"rich_text": [{"type": "text", "text": {"content": text[:2000]}}]}}


def _para(text: str) -> dict:
    return {
        "type": "paragraph",
        "paragraph": {"rich_text": [{"type": "text", "text": {"content": text[:2000]}}]},
    }


def _para_blocks(text: str) -> list[dict]:
    return [_para(ln) for ln in (text or "").splitlines() if ln.strip()]


def notion_enabled() -> bool:
    return bool(os.environ.get("NOTION_API_KEY") and os.environ.get("NOTION_PARENT_PAGE_ID"))


def client_from_env() -> NotionClient | None:
    key = os.environ.get("NOTION_API_KEY") or ""
    # 写进同一篇文档：这个 id 就是目标页面本身
    page = os.environ.get("NOTION_PAGE_ID") or os.environ.get("NOTION_PARENT_PAGE_ID") or ""
    if not key or not page:
        return None
    return NotionClient(key, page)
