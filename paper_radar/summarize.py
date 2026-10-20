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
    '"zh_summary": "Notion长导读", "zh_short": "飞书短导读", '
    '"why_read": "一句话看点，不超过40字"}\n'
    "\n"
    "导读写给谁看：社科、传播、人机交互背景的研究者，不是计算机科班，看不懂 AI 黑话。\n"
    "目标：他不读原文，也能明白这篇论文干了什么、为什么值得关心。\n"
    "\n"
    "zh_short 与 zh_summary 都是四段结构（严格以 \\n 分段，段首标签固定；"
    "每段是一整段话，段内可多句，不要另起带 ** 的小标题）：\n"
    "**问题**：…\n"
    "**方法**：…\n"
    "**结论**：…\n"
    "**亮点**：…\n"
    "\n"
    "zh_short（飞书卡片用）：每段 2 句上下，四段合计 250–350 字。\n"
    "把研究问题、核心做法、主要发现、意义与边界四件事都讲清楚，"
    "不要压缩成一两句带过；机制细节和验证过程留给长版。\n"
    "\n"
    "zh_summary（Notion 归档用）：每段 2–4 句，四段合计 450–700 字。\n"
    "**问题**：这篇要解决什么、为什么难或为什么重要\n"
    "**方法**：它具体做了什么——先一句话交代研究对象，再讲输入输出和关键一步，最后讲怎么验证\n"
    "**结论**：主要发现是什么；数字要讲清说明了什么，有机制解释就写出来\n"
    "**亮点**：新在哪、能借鉴什么、边界或局限\n"
    "\n"
    "写法要求（zh_short、zh_summary、why_read 一体遵守，比字数更重要）：\n"
    "1. 严谨的书面语：像研究者给同行写的中文简介——准确、克制、清楚。\n"
    "   禁止论文腔空话（「本文提出了一种基于…的方法」「具有重要意义」）；\n"
    "   也禁止口语闲笔（「说白了」「顺手」「一口气」「其实」这类）。\n"
    "2. 术语用规范名称；首次出现时括注一句准确而简明的说明，例如：\n"
    "   「稀疏自编码器（将模型内部激活分解为多个可解释方向的方法）」。\n"
    "   不要用俏皮比方代替定义。\n"
    "3. 机制讲清：不能只罗列名词和数字；数字要说明含义，\n"
    "   例如「攻击成功率 91%，即十次中约九次被劫持」。\n"
    "4. 不硬编：摘要未写清的信息写「摘要未说明」，不要推测。\n"
    "5. 不要复述摘要原文，用严谨的中文重述。\n"
    "6. 文本里的引用一律用「」，不要用英文双引号，避免破坏 JSON。\n"
    "\n"
    "score 要拉开区分度，不要都给 3：\n"
    "5=顶会顶刊且贡献突出，方法扎实、结论有启发，值得精读\n"
    "4=工作扎实，有一到两个可借鉴的点\n"
    "3=相关，常规增量工作\n"
    "2=边缘相关，或方法/结论有明显局限\n"
    "1=不相关，或质量明显有问题\n"
)

INSIGHT_SYSTEM = (
    "你是科研选题顾问。读者是社科与计算机交叉背景的研究者，关心可信人工智能、人机交互、"
    "人机协作、AI 素养与社会影响，看这批论文是为了拓展视野、寻找可开展的研究方向。\n"
    "请根据「今日入选论文」写一份中文总结，严格两段结构，总长 250–400 字：\n"
    "\n"
    "**今日图景**：\n"
    "用 2–3 句话概括这批工作共同在推进什么、有哪些张力或空白（不要逐篇复述标题）。\n"
    "\n"
    "**研究启发**：\n"
    "给 2–4 条可展开的方向，每条一行，格式「方向名 — 具体切口/可做的研究」。\n"
    "要求：具体、可下手（方法迁移、跨领域应用、可做的实证或系统工作、尚未回答的问题）；"
    "允许与经典社科/教育/传播/心理测量结合；避免空话套话，不要写「进一步研究」这类废话。\n"
    "写法：严谨克制的书面语，像给同行写的今日研究简报；禁止论文腔空话，也禁止口语闲笔；\n"
    "术语首次出现括注一句准确而简明的说明。\n"
)


def summarize_daily_insight(papers: list[Paper]) -> str:
    """整卡末尾的今日总结 + 研究启发。失败时返回空串。"""
    if not papers or not (
        os.environ.get("LLM_API_KEY") or os.environ.get("ANTHROPIC_API_KEY")
    ):
        return ""
    lines = []
    for i, p in enumerate(papers, 1):
        lines.append(
            f"{i}. [{p.category}] {p.title} | {p.venue} | {(p.why_read or '')[:50]}"
        )
    prompt = "今日入选论文：\n" + "\n".join(lines)
    try:
        text = _with_retry(lambda: _chat(INSIGHT_SYSTEM, prompt), "LLM")
        return (text or "").strip()
    except Exception as e:
        log.warning("今日总结生成失败: %s", e)
        return ""


def summarize(
    papers: list[Paper],
    batch_size: int = 3,
    categories: list[dict] | None = None,
    update_category: bool = True,
) -> list[Paper]:
    """生成中文摘要、质量分、分类修正。

    优先 OpenAI 兼容接口（LLM_API_KEY），其次 Claude（ANTHROPIC_API_KEY）。
    categories 传入时，LLM 的 category 输出会修正关键词预分类结果。
    update_category=False 时保持原分类（传播链路的 fresh/backfill）。
    batch_size 默认 3：每篇要出短+长两版导读（合计约 700–1000 字），
    一批太多容易顶到输出上限被截断。
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
                {"id": p.source_id, "title": p.title, "abstract": (p.abstract or "")[:2000]},
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
                p.zh_short = str(item.get("zh_short") or "").strip()
                p.why_read = str(item.get("why_read") or "").strip()
                # 混合分类：LLM 修正关键词预分类（仅接受合法 id）
                cat = str(item.get("category") or "").strip().lower()
                if update_category and cat and (valid_ids is None or cat in valid_ids):
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
            "temperature": 0.4,
            # 每篇短+长两版导读，必须留够输出空间，否则 JSON 被截断、整批作废
            "max_tokens": 8192,
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
        max_tokens=8192,
        system=system,
        messages=[{"role": "user", "content": user}],
    )
    return resp.content[0].text


def _parse_json_array(text: str) -> list:
    raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", (text or "").strip(), flags=re.M)
    candidates = [raw, _repair_json(raw)]
    # 兜底：从第一个 [ 切到最后一个 ]（模型偶尔会在 JSON 前后加说明文字）
    if not raw.lstrip().startswith("["):
        m = re.search(r"\[.*\]", raw, flags=re.S)
        if m:
            candidates.append(_repair_json(m.group(0)))
    for cand in candidates:
        try:
            # strict=False：允许字符串里出现未转义的换行/控制符，
            # 便宜模型写多行 zh_summary 时常见，不放行整批就作废。
            data = json.loads(cand, strict=False)
            return data if isinstance(data, list) else []
        except json.JSONDecodeError:
            continue
    log.warning(
        "摘要输出无法解析为 JSON（len=%d），尾 200 字：%s",
        len(text or ""),
        (text or "")[-200:],
    )
    return []


def _repair_json(text: str) -> str:
    """修复模型 JSON 的常见毛病：字符串值里的裸引号/换行。

    判定规则：字符串内的 " 若后面第一个有效字符是 , } ] : 或结尾，
    视为字符串结束；否则是内容里的引号，转义掉。裸换行也转义。
    """
    out: list[str] = []
    in_str = False
    escaped = False
    n = len(text)
    for i, ch in enumerate(text):
        if not in_str:
            out.append(ch)
            if ch == '"':
                in_str = True
            continue
        if escaped:
            out.append(ch)
            escaped = False
        elif ch == "\\":
            out.append(ch)
            escaped = True
        elif ch == '"':
            j = i + 1
            while j < n and text[j] in " \t\r\n":
                j += 1
            nxt = text[j] if j < n else ""
            if nxt in (",", "}", "]", ":", ""):
                out.append(ch)
                in_str = False
            else:
                out.append('\\"')
        elif ch == "\n":
            out.append("\\n")
        elif ch == "\r":
            out.append("\\r")
        elif ch == "\t":
            out.append("\\t")
        elif ord(ch) < 0x20:
            out.append(f"\\u{ord(ch):04x}")
        else:
            out.append(ch)
    return "".join(out)
