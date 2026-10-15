from __future__ import annotations

import json
import logging
import os
import re

import requests

from .models import Paper

log = logging.getLogger(__name__)

SYSTEM = (
    "你是文献筛选助手。根据论文标题和摘要输出严格 JSON 数组，不要输出任何其他文字。\n"
    '每个元素形如: {"id": 原id, "score": 1-5整数质量分, '
    '"zh_summary": "中文摘要2-3句", "why_read": "一句话看点，不超过40字"}\n'
    "score 标准：5=高度相关且有明显贡献，4=相关且扎实，3=相关，2=边缘相关，1=不相关。"
)

# 常见服务商的 OpenAI 兼容地址（LLM_BASE_URL 用，也支持任何自建/本地服务）
PROVIDER_HINTS = """
# DeepSeek:  https://api.deepseek.com/v1        model=deepseek-chat
# Kimi:     https://api.moonshot.cn/v1         model=moonshot-v1-8k
# 通义千问:  https://dashscope.aliyuncs.com/compatible-mode/v1   model=qwen-plus
# 智谱 GLM: https://open.bigmodel.cn/api/paas/v4                 model=glm-4-flash
# OpenAI:   https://api.openai.com/v1          model=gpt-4o-mini
# Ollama:   http://localhost:11434/v1          model=qwen2.5
"""


def summarize(papers: list[Paper], batch_size: int = 8) -> list[Paper]:
    """生成中文摘要和质量分。优先 OpenAI 兼容接口，其次 Claude；都没配就跳过。"""
    if not papers:
        return papers

    llm_key = os.environ.get("LLM_API_KEY", "")
    anthropic_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not llm_key and not anthropic_key:
        log.info("未配置 LLM_API_KEY / ANTHROPIC_API_KEY，跳过摘要")
        return papers

    for i in range(0, len(papers), batch_size):
        batch = papers[i : i + batch_size]
        payload = [
            json.dumps(
                {"id": p.source_id, "title": p.title, "abstract": (p.abstract or "")[:1200]},
                ensure_ascii=False,
            )
            for p in batch
        ]
        prompt = "请为以下论文打分并生成中文摘要：\n" + "\n".join(payload)
        try:
            text = _chat(SYSTEM, prompt)
            items = _parse_json_array(text)
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


def _chat(system: str, user: str) -> str:
    """先走 OpenAI 兼容接口（DeepSeek/Kimi/Qwen/GLM/OpenAI/Ollama），再退回 Claude。"""
    if os.environ.get("LLM_API_KEY"):
        return _chat_openai_compatible(system, user)
    return _chat_anthropic(system, user)


def _chat_openai_compatible(system: str, user: str) -> str:
    base = os.environ.get("LLM_BASE_URL", "https://api.deepseek.com/v1")
    key = os.environ["LLM_API_KEY"]
    model = os.environ.get("LLM_MODEL", "deepseek-chat")
    r = requests.post(
        f"{base.rstrip('/')}/chat/completions",
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        json={
            "model": model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": 0.2,
        },
        timeout=120,
    )
    r.raise_for_status()
    data = r.json()
    try:
        return data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as e:
        raise RuntimeError(f"LLM 响应格式异常: {str(data)[:200]}") from e


def _chat_anthropic(system: str, user: str) -> str:
    try:
        import anthropic
    except ImportError as e:
        raise RuntimeError("anthropic 未安装，且未配置 LLM_API_KEY") from e
    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    model = os.environ.get("CLAUDE_MODEL", "claude-sonnet-5-5")
    resp = client.messages.create(
        model=model,
        max_tokens=2048,
        system=system,
        messages=[{"role": "user", "content": user}],
    )
    return resp.content[0].text


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
