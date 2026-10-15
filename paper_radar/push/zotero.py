from __future__ import annotations

import html
import logging
import re

import requests

from ..models import Paper

log = logging.getLogger(__name__)

API = "https://api.zotero.org"


class ZoteroClient:
    """Zotero Web API：写入云端文献库，两台电脑同步拉取。"""

    def __init__(self, api_key: str, user_id: str | None = None):
        self.api_key = api_key
        self.user_id = user_id or self._resolve_user_id()
        log.info("Zotero 目标用户: %s", self.user_id)

    # ---------- 基础 ----------

    def _headers(self) -> dict:
        return {
            "Zotero-API-Key": self.api_key,
            "Zotero-API-Version": "3",
            "Content-Type": "application/json",
        }

    def _resolve_user_id(self) -> str:
        r = requests.get(f"{API}/keys/current", headers=self._headers(), timeout=30)
        r.raise_for_status()
        return str(r.json()["userID"])

    def _items_url(self) -> str:
        return f"{API}/users/{self.user_id}/items"

    # ---------- 入库 ----------

    def save_papers(self, papers: list[Paper]) -> None:
        if not papers:
            return
        items = [to_item(p) for p in papers]
        r = requests.post(self._items_url(), headers=self._headers(), json=items, timeout=60)
        r.raise_for_status()
        body = r.json()
        if body.get("failed"):
            log.warning("Zotero 部分条目失败: %s", body["failed"])

        quota_exhausted = False
        for idx_str, meta in (body.get("successful") or {}).items():
            idx = int(idx_str)
            if idx >= len(papers):
                continue
            p, key = papers[idx], meta["key"]
            try:
                self._add_note(key, p)
            except Exception as e:
                log.warning("笔记写入失败 [%s]: %s", p.title[:40], e)
            if p.pdf_url and not quota_exhausted:
                try:
                    result = self._attach_pdf(key, p.pdf_url)
                    if result == "quota":
                        quota_exhausted = True
                except Exception as e:
                    log.warning("PDF 挂载失败 [%s]: %s", p.title[:40], e)

        n = len(body.get("successful") or {})
        log.info("Zotero 已入库 %d 篇", n)

    def _add_note(self, parent_key: str, p: Paper) -> None:
        parts = []
        if p.why_read:
            parts.append(
                f"<p><strong>💡 一句话看点</strong></p><p>{html.escape(p.why_read)}</p>"
            )
        if p.zh_summary:
            parts.append(
                f"<p><strong>📝 中文摘要</strong></p><p>{html.escape(p.zh_summary)}</p>"
            )
        if not parts:
            return
        note = {"itemType": "note", "parentItem": parent_key, "note": "".join(parts)}
        r = requests.post(self._items_url(), headers=self._headers(), json=[note], timeout=30)
        r.raise_for_status()

    def _attach_pdf(self, parent_key: str, pdf_url: str) -> str:
        """返回 'ok' / 'skip' / 'quota'。"""
        # OpenReview 的 PDF 端点有浏览器人机挑战，程序化下载必然 403，直接跳过
        if "openreview.net" in pdf_url:
            log.info("OpenReview PDF 有反爬挑战，跳过自动挂载: %s", pdf_url)
            return "skip"
        r = requests.get(
            pdf_url,
            headers={
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
            },
            timeout=60,
        )
        r.raise_for_status()
        pdf = r.content
        if not pdf.startswith(b"%PDF"):
            log.warning("非 PDF 内容，跳过: %s", pdf_url)
            return "skip"

        att = {
            "itemType": "attachment",
            "linkMode": "imported_file",
            "parentItem": parent_key,
            "title": "Full Text PDF",
            "filename": "fulltext.pdf",
            "contentType": "application/pdf",
        }
        r = requests.post(self._items_url(), headers=self._headers(), json=[att], timeout=30)
        r.raise_for_status()
        successful = (r.json().get("successful") or {})
        if not successful:
            log.warning("附件条目创建失败: %s", r.text[:200])
            return "skip"
        att_key = next(iter(successful.values()))["key"]
        status = self._upload_file(att_key, pdf)
        if status == "quota":
            # 配额满时删掉空附件，避免留下孤儿条目
            self._delete_item(att_key)
        return status

    def _delete_item(self, item_key: str) -> None:
        try:
            requests.delete(f"{self._items_url()}/{item_key}", headers=self._headers(), timeout=30)
        except Exception as e:
            log.warning("清理空附件失败 %s: %s", item_key, e)

    def _upload_file(self, att_key: str, data: bytes) -> str:
        """Zotero 文件上传三步：POST 授权 → 传 S3 → 确认。"""
        import hashlib
        import time

        md5 = hashlib.md5(data).hexdigest()
        r = requests.post(
            f"{self._items_url()}/{att_key}/file",
            headers={
                **self._headers(),
                "If-None-Match": "*",
                "Content-Type": "application/x-www-form-urlencoded",
            },
            data={
                "md5": md5,
                "filename": "fulltext.pdf",
                "filesize": str(len(data)),
                "mtime": str(int(time.time() * 1000)),
            },
            timeout=30,
        )
        if r.status_code != 200:
            msg = r.text[:200]
            if r.status_code == 413 or "quota" in msg.lower():
                log.warning("Zotero 存储空间不足，PDF 跳过（条目已入库）。可清理 <https://www.zotero.org/settings/storage> 或升级 Zotero Storage")
                return "quota"
            log.warning("上传授权失败 %s: %s", r.status_code, msg)
            return "skip"
        info = r.json()

        form = dict(info.get("params") or {})
        form["prefix"] = info.get("prefix", "")
        form["suffix"] = info.get("suffix", "")
        up = requests.post(
            info["url"],
            data=form,
            files={"file": ("fulltext.pdf", data, "application/pdf")},
            timeout=120,
        )
        if up.status_code not in (200, 201, 204):
            log.warning("文件上传失败 %s: %s", up.status_code, up.text[:200])
            return "skip"

        token = info.get("uploadKey") or (up.headers.get("ETag") or "").strip('"')
        fin = requests.post(
            f"{self._items_url()}/{att_key}/file/upload",
            headers={**self._headers(), "Upload-Token": str(token)},
            timeout=30,
        )
        if fin.status_code not in (200, 204):
            log.warning("上传确认失败 %s: %s", fin.status_code, fin.text[:200])
            return "skip"
        return "ok"


def to_item(p: Paper) -> dict:
    creators = [to_creator(a) for a in (p.authors or [])[:25] if a]
    tags = [{"tag": "Paper Radar"}]
    if p.accepted_at:
        tags.append({"tag": f"accepted:{p.accepted_at.lower()}"})
    if p.category and p.category != "other":
        tags.append({"tag": f"cat:{p.category}"})
    for k in (p.keywords or [])[:6]:
        if k:
            tags.append({"tag": k})
    if p.quality:
        tags.append({"tag": f"score-{p.quality}"})

    base = {
        "title": p.title,
        "creators": creators,
        "abstractNote": p.abstract or "",
        "date": p.date,
        "url": p.url,
        "tags": tags,
    }
    if p.source_id.startswith("journal:"):
        # 顶刊文章：journalArticle + 期刊名
        parts = p.source_id.split(":")
        journal = p.venue or parts[1] if len(parts) > 1 else p.venue
        return {
            "itemType": "journalArticle",
            "publicationTitle": journal,
            "extra": p.source_id,
            **base,
        }
    if p.source_id.startswith("arxiv:") and not p.accepted_at:
        return {
            "itemType": "preprint",
            "repository": "arXiv",
            "extra": f"arXiv:{p.source_id.split(':', 1)[1]}",
            **base,
        }
    # OpenReview 或已查证录用 → 按会议论文
    extra = f"OpenReview:{p.source_id.split(':', 1)[1]}" if p.source_id.startswith("openreview:") else p.source_id
    if p.accepted_at:
        extra = f"{extra}\nAccepted: {p.accepted_at}"
    return {
        "itemType": "conferencePaper",
        "proceedingsTitle": p.venue or p.accepted_at or "Unknown venue",
        "extra": extra,
        **base,
    }


MAX_CREATOR_LEN = 100  # Zotero 同步对 creator name 有长度限制
_INSTITUTION_RE = re.compile(
    r"\b(university|institute|department|division|school|college|laboratory|lab\b|"
    r"center|centre|hospital|clinic|consortium|collaboration|team|group|academy|"
    r"program|faculty|medical|research)\b",
    re.I,
)


def to_creator(name: str) -> dict:
    """Zotero 作者格式：单名/机构名用 name 字段，其余拆 firstName/lastName。

    机构名和过长名截断后走 name 字段，避免 400/413。
    """
    name = (name or "").strip()
    if not name:
        return {"creatorType": "author", "name": "?"}
    if len(name) > MAX_CREATOR_LEN:
        name = name[: MAX_CREATOR_LEN - 1] + "…"

    # 机构 / 合作组 / 看起来像单位的长名 → name 字段
    if _INSTITUTION_RE.search(name) or ("," in name and len(name) > 40):
        return {"creatorType": "author", "name": name}

    if "," in name:
        last, first = [x.strip() for x in name.split(",", 1)]
        last, first = last[:MAX_CREATOR_LEN], first[:MAX_CREATOR_LEN]
        return {"creatorType": "author", "firstName": first or " ", "lastName": last}
    parts = name.split()
    if len(parts) == 1:
        return {"creatorType": "author", "name": name}
    return {
        "creatorType": "author",
        "firstName": " ".join(parts[:-1])[:MAX_CREATOR_LEN],
        "lastName": parts[-1][:MAX_CREATOR_LEN],
    }
