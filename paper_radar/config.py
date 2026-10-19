from __future__ import annotations

import os
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent

# 旧扁平 schema 的默认值（兼容层专用，见 _tracks_from_legacy）
LEGACY_DEFAULTS = {
    "arxiv": {"categories": ["cs.HC", "cs.AI", "cs.CL"], "max_results": 80},
    "openreview": {"venues": [], "max_per_venue": 40},
    "comm": {
        "max_papers_per_day": 4,
        "fresh_count": 2,
        "backfill_count": 2,
        "fresh_days": 30,
        "backfill_min_months": 3,
        "backfill_max_months": 18,
        "max_per_window": 40,
        "keywords": [],
        "journals": {},
    },
}

# windows 频道的默认展示分组
DEFAULT_GROUPS = [
    {"id": "fresh", "label": "📰 新文"},
    {"id": "backfill", "label": "📎 回补精选"},
]


def load_config(path: str | Path | None = None) -> dict:
    """读 config.yaml，返回 {"tracks": {名字: track dict}}。

    两种 schema：
    - 新：顶层 `tracks:`，每个键一个频道（sources / keywords / categories / push ...）
    - 旧：顶层 arxiv/openreview/journals/comm 等扁平键 —— 仍兼容，内部合成 ai + comm 两个频道
    """
    p = Path(path or os.environ.get("PAPER_RADAR_CONFIG", "config.yaml"))
    data: dict = {}
    if p.exists():
        data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}

    if data.get("tracks"):
        tracks = {
            name: _normalize_track(name, raw or {})
            for name, raw in (data.get("tracks") or {}).items()
        }
    else:
        tracks = _tracks_from_legacy(data)
    return {"tracks": tracks}


def get_track(cfg: dict, name: str) -> dict | None:
    """按名字取频道；不存在返回 None（调用方跳过即可，fork 删掉频道不会报错）。"""
    return (cfg.get("tracks") or {}).get(name)


def _normalize_track(name: str, raw: dict) -> dict:
    """补全单个频道的默认值。sources 里留空/省略的块 = 不启用。"""
    kind = (raw.get("kind") or "quota").strip()
    sources = raw.get("sources") or {}
    push = raw.get("push") or {}
    feishu = push.get("feishu") or {}
    notes = push.get("notes") or {}
    notion = push.get("notion") or {}
    zotero = push.get("zotero") or {}

    seen_file = str(raw.get("seen_file") or f"data/seen_{name}.json")
    if not os.path.isabs(seen_file):
        seen_file = str(REPO_ROOT / seen_file)

    track: dict = {
        "name": name,
        "kind": kind,
        "seen_file": seen_file,
        "sources": sources,
        "keywords": list(raw.get("keywords") or []),
        "categories": list(raw.get("categories") or []),
        "per_cat_min": int(raw.get("per_cat_min") or 2),
        "max_papers_per_day": int(raw.get("max_papers_per_day") or 10),
        "fresh_count": int(raw.get("fresh_count") or 2),
        "backfill_count": int(raw.get("backfill_count") or 2),
        "groups": list(raw.get("groups") or DEFAULT_GROUPS),
        "push": {
            "feishu": {
                "webhook_env": feishu.get("webhook_env") or f"FEISHU_{name.upper()}_WEBHOOK_URL",
                "color": feishu.get("color") or "blue",
                "title_prefix": feishu.get("title_prefix") or name,
                "date_suffix": feishu.get("date_suffix") or "",
            },
            "notes": {
                "kind": notes.get("kind") or name,
                "title": notes.get("title") or (feishu.get("title_prefix") or name),
            },
            "notion": {"kind": notion.get("kind") or name},
            "zotero": {"collection": zotero.get("collection") or f"{name}-daily"},
        },
    }
    return track


def _tracks_from_legacy(data: dict) -> dict:
    """旧扁平 schema → ai + comm 两个频道。取值与旧 load_config 逐字段一致。"""
    arxiv = {**LEGACY_DEFAULTS["arxiv"], **(data.get("arxiv") or {})}
    openreview = {**LEGACY_DEFAULTS["openreview"], **(data.get("openreview") or {})}
    comm = {**LEGACY_DEFAULTS["comm"], **(data.get("comm") or {})}
    top_keywords = list(data.get("keywords") or [])

    ai = _normalize_track(
        "ai",
        {
            "kind": "quota",
            "seen_file": "data/seen.json",
            "sources": {
                "arxiv": {"categories": list(arxiv.get("categories") or []), "max_results": int(arxiv.get("max_results") or 80)},
                "openreview": {"venues": list(openreview.get("venues") or []), "max_per_venue": int(openreview.get("max_per_venue") or 40)},
                "journals": {"feeds": dict(data.get("journals") or {}), "max_per_journal": int(data.get("max_per_journal") or 20)},
                "acm_confs": {"venues": dict(data.get("acm_confs") or {}), "max_per_venue": int(data.get("max_per_conf") or 25)},
            },
            "keywords": top_keywords,
            "categories": list(data.get("categories") or []),
            "per_cat_min": int(data.get("per_cat_min") or 2),
            "max_papers_per_day": int(data.get("max_papers_per_day") or 10),
            "push": {
                "feishu": {"webhook_env": "FEISHU_WEBHOOK_URL", "color": "blue", "title_prefix": "📚 Paper Radar"},
                "notes": {"kind": "ai", "title": "📚 Paper Radar"},
                "notion": {"kind": "ai"},
                "zotero": {"collection": "AI-daily"},
            },
        },
    )
    # 旧逻辑：comm.keywords 为空时回落顶层 keywords
    comm_keywords = list(comm.get("keywords") or top_keywords)
    comm_track = _normalize_track(
        "comm",
        {
            "kind": "windows",
            "seen_file": "data/seen_comm.json",
            "sources": {
                "comm_journals": {
                    "journals": dict(comm.get("journals") or {}),
                    "fresh_days": int(comm.get("fresh_days") or 30),
                    "backfill_min_months": int(comm.get("backfill_min_months") or 3),
                    "backfill_max_months": int(comm.get("backfill_max_months") or 18),
                    "max_per_window": int(comm.get("max_per_window") or 40),
                },
            },
            "keywords": comm_keywords,
            "fresh_count": int(comm.get("fresh_count") or 2),
            "backfill_count": int(comm.get("backfill_count") or 2),
            "groups": DEFAULT_GROUPS,
            "push": {
                "feishu": {
                    "webhook_env": "FEISHU_COMM_WEBHOOK_URL",
                    "color": "purple",
                    "title_prefix": "📡 传播精选",
                    "date_suffix": " · 传播",
                },
                "notes": {"kind": "comm", "title": "📡 传播精选"},
                "notion": {"kind": "comm"},
                "zotero": {"collection": "comm-daily"},
            },
        },
    )
    return {"ai": ai, "comm": comm_track}
