from __future__ import annotations

import json
from pathlib import Path

MAX_SEEN = 20000  # 每天新增几百条 key，留足约一个月的记忆，防止老论文被当新论文重推


def load_seen(path: Path) -> list[str]:
    """返回按写入顺序排列的已见论文 ID 列表。"""
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []
    return [str(x) for x in data] if isinstance(data, list) else []


def save_seen(path: Path, ids: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # 去重且保序，只保留最近 MAX_SEEN 条，防止文件无限膨胀
    seen: set[str] = set()
    ordered: list[str] = []
    for i in ids:
        if i not in seen:
            seen.add(i)
            ordered.append(i)
    path.write_text(
        json.dumps(ordered[-MAX_SEEN:], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
