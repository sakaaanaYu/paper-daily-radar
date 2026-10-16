from __future__ import annotations

import json
import logging
import os
import re
import time

import requests

from .models import Paper

log = logging.getLogger(__name__)

# 常见服务商的 OpenAI 兼容地址（LLM_BASE_URL 用，也支持任何自建/本地服务）
PROVIDER_HINTS = """
# DeepSeek:  https://api.deepseek.com/v1        model=deepseek-chat
# Kimi:     https://api.moonshot.cn/v1         model=moonshot-v1-8k
# 通义千问:  https://dashscope.aliyuncs.com/compatible-mode/v1   model=qwen-plus
# 智谱 GLM: https://open.bigmodel.cn/api/paas/v4                 model=glm-4-flash
# OpenAI:   https://api.openai.com/v1          model=gpt-4o-mini
# Ollama:   http://localhost:11434/v1          model=qwen2.5
"""

BASE_SYSTEM = (
    "你是文献导读助手。根据论文标题和摘要输出严格 JSON 数组，不要输出任何其他文字。\n"
    '每个元素形如: {"id": 原id, "score": 1-5整数, "category": 分类id, '
    '"zh_summary": "中文导读", "why_read": "一句话看点，不超过40字"}\n'
    "\n"
    "zh_summary 要求（120-220字，用分号或短句把要点讲明白，不要复述摘要原文）：\n"
    "必须覆盖这四块（可以用①②③④或「问题/方法/结论/亮点」这样的小标签）：\n"
    "① 问题：论文要解决什么问题？为什么重要？\n"
    "② 方法：怎么做？关键设计/数据/模型是什么？\n"
    "③ 结论：主要发现或效果如何？\n"
    "④ 亮点：创新点、值得借鉴之处、局限或适用边界\n"
    "\n"
    "score 要拉开区分度，不要都给 3：\n"
    "5=顶会顶刊且贡献突出，方法扎实、结论有启发，值得精读\n"
    "4=工作扎实，有一到两个可借鉴的点\n"
    "3=相关，常规增量工作\n"
    "2=边缘相关，或方法/结论有明显局限\n"
    "1=不相关，或质量明显有问题\n"
)


def summarize(
    papers: list[Paper],
    batch_size: int = 8,
    categories: list[dict] | None = None,
) -> list[Paper]:
    """生成中文摘要、质量分、分类修正。

    优先 OpenAI 兼容接口（LLM_API_KEY），其次 Claude（ANTHROPIC_API_KEY）。
    categories 传入时，LLM 的 category 输出会修正关键词预分类结果。
    """
    if not papers:
        return papers
    if not os.environ.get("LLM_API_KEY") and not os.environ.get("ANTHROPIC_API_KEY"):
        log.info("未配置 LLM_API_KEY / ANTHROPIC_API_KEY，跳过导读生成")
        return papers

    system = BASE_SYSTEM
    valid_ids: set[str] | None = None
    if categories:
        cat_desc = "、".join(f'{c.get("id")}={c.get("label")}' for c in categories)
        system += f"\ncategory 只能取这些 id：{cat_desc}，或 other。"
        valid_ids = {c.get("id") for c in categories} | {"other"}

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
            text = _chat(system, prompt)
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
                # 混合分类：LLM 修正关键词预分类（仅接受合法 id）
                cat = str(item.get("category") or "").strip().lower()
                if cat and (valid_ids is None or cat in valid_ids):
                    p.category = cat
        except Exception as e:
            log.warning("摘要批次 %d 失败: %s", i // batch_size, e)
        # 批次间隔，降低触发服务商限流的概率
        if i + batch_size < len(papers):
            time.sleep(1.5)
    return papers


def _chat(system: str, user: str) -> str:
    """先走 OpenAI 兼容接口（DeepSeek/Kimi/Qwen/GLM/OpenAI/Ollama），再退回 Claude。"""
    if os.environ.get("LLM_API_KEY"):
        return _with_retry(lambda: _chat_openai_compatible(system, user), "LLM")
    return _with_retry(lambda: _chat_anthropic(system, user), "Claude")


def _with_retry(fn, label: str, tries: int = 4):
    """429/5xx/超时退避重试，避免一次限流就丢掉整批导读。"""
    last = None
    for attempt in range(tries):
        try:
            return fn()
        except Exception as e:
            last = e
            msg = str(e).lower()
            # 欠费/停服/密钥无效：重试也没用，直接抛
            if any(s in msg for s in ("insufficient balance", "suspended", "invalid api key", "authentication")):
                raise
            retryable = (
                isinstance(e, requests.HTTPError)
                and e.response is not None
                and e.response.status_code in (429, 500, 502, 503, 529)
            ) or isinstance(e, (requests.Timeout, requests.ConnectionError))
            if not retryable or attempt == tries - 1:
                raise
            wait = 2 ** attempt * 3  # 3s, 6s, 12s
            log.warning("%s 调用失败（%d/%d）：%s，%ds 后重试", label, attempt + 1, tries, e, wait)
            time.sleep(wait)
    raise last  # pragma: no cover


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
    if r.status_code == 429:
        # 服务商限流：带一点响应体，方便判断是 RPM 还是额度用尽
        raise requests.HTTPError(
            f"429 限流 ({base}): {r.text[:160]}", response=r
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
