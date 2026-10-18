from __future__ import annotations

import logging
import os
from pathlib import Path

from .push.zotero import ZoteroClient
from .store import save_seen

log = logging.getLogger("paper_radar.reset")

ROOT = Path(__file__).resolve().parent.parent
SEEN_PATH = ROOT / "data" / "seen.json"
SEEN_COMM_PATH = ROOT / "data" / "seen_comm.json"


def run_reset() -> int:
    """清空调试记忆 + 清空 Zotero 里 Paper Radar 条目 + 建好两个日更集合。"""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    save_seen(SEEN_PATH, [])
    save_seen(SEEN_COMM_PATH, [])
    log.info("已清空 seen.json / seen_comm.json")

    key = os.environ.get("ZOTERO_API_KEY") or ""
    if not key:
        log.warning("未设置 ZOTERO_API_KEY，只清了本地记忆")
        return 0

    client = ZoteroClient(key, os.environ.get("ZOTERO_USER_ID") or None)
    deleted = client.clear_tagged("Paper Radar")
    for name in ("AI-daily", "comm-daily"):
        client.ensure_collection(name)
    log.info("重置完成：Zotero 删除 %d 条，集合 AI-daily / comm-daily 就绪", deleted)
    return 0


if __name__ == "__main__":
    raise SystemExit(run_reset())
