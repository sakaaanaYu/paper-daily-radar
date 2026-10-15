from __future__ import annotations

import json
import logging
import os
import re

from .models import Paper

log = logging.getLogger(__name__)

SYSTEM = (
    "你是文献筛选助手。根据论文标题和摘要输出严格 JSON 数组，不要输出任何其他文字。\n"
    '每个元素形如: {"id": 原id, "score": 1-5整数质量分, '
    '"zh_summary": "中文摘要2-3句", "why_read": "一句话看点，不超过40字"}\n'
    "score 标准：5=高度相关且有明显贡献，4=相关且扎实，3=相关，2=边缘相关，1=不相关。"
)


def summarize(papers: list[Paper], batch_size: int = 8) -> list[Paper]:
    """用 Claude 生成中文摘要和质量分。没有 ANTHROPIC_API_KEY 就原样返回。"""
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key or not papers:
        return papers
    try:
        import anthropic
    except ImportError:
        log.warning("anthropic 未安装，跳过摘要")
        return papers

    client = anthropic.Anthropic(api_key=api_key)
    model = os.environ.get("CLAUDE_MODEL", "claude-sonnet-5-5")

    for i in range(0, len(papers), batch_size):
        batch = papers[i : i + batch_size]
        payload = [
            json.dumps(
                {
                    "id": p.source_id,
                    "title": p.title,
                    "abstract": (p.abstract or "")[:1200],
                },
                ensure_ascii=False,
            )
            for p in batch
        ]
        prompt = "请为以下论文打分并生成中文摘要：\n" + "\n".join(payload)
        try:
            resp = client.messages.create(
                model=model,
                max_tokens=2048,
                system=SYSTEM,
                messages=[{"role": "user", "content": prompt}],
            )
            items = _parse_json_array(resp.content[0].text)
            by_id = {p.source_id: p for p in batch}
            for item in items:
                p = by_id.get(item.get("id"))
                if not p:
                    continue
                try:
                    p.quality = max(1, min(5, int(item.get("score") or 3)))
                except (TypeError, ValueError):
                    p.quality = 3
                p.zh_summary = str(item.get("zh_summary") or "").strip()
                p.why_read = str(item.get("why_read") or "").strip()
        except Exception as e:
            log.warning("摘要批次 %d 失败: %s", i // batch_size, e)
    return papers


def _parse_json_array(text: str) -> list:
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", (text or "").strip(), flags=re.M)
    try:
        data = json.loads(text)
        return data if isinstance(data, list) else []
    except json.JSONDecodeError:
        m = re.search(r"\[.*\]", text, flags=re.S)
        if m:
            try:
                data = json.loads(m.group(0))
                return data if isinstance(data, list) else []
            except json.JSONDecodeError:
                pass
    log.warning("摘要输出无法解析为 JSON")
    return []
