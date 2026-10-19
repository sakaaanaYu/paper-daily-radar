from __future__ import annotations

import html
import logging
import re
import time

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

    def _request(self, method: str, url: str, timeout: int = 30, **kwargs) -> requests.Response:
        """只对 429 限流退避；428 是缺版本号，重试没用。"""
        last = None
        for attempt in range(5):
            last = requests.request(method, url, timeout=timeout, **kwargs)
            if last.status_code != 429:
                return last
            wait = int(last.headers.get("Retry-After") or (2 ** attempt * 2))
            log.warning("Zotero 限流 429，%ss 后重试", wait)
            time.sleep(wait)
        return last

    # ---------- 集合 ----------

    def ensure_collection(self, name: str) -> str:
        """按名字找集合，没有就建。返回 collection key。"""
        if not name:
            return ""
        r = self._request(
            "GET",
            f"{API}/users/{self.user_id}/collections",
            headers=self._headers(),
            params={"limit": 100},
        )
        r.raise_for_status()
        for c in r.json() or []:
            if (c.get("data") or {}).get("name") == name:
                return c["key"]
        # Zotero 写接口要求数组体
        r = self._request(
            "POST",
            f"{API}/users/{self.user_id}/collections",
            headers=self._headers(),
            json=[{"name": name}],
        )
        if r.status_code >= 400:
            log.error("创建集合失败 %s: %s", name, r.text[:300])
            r.raise_for_status()
        body = r.json()
        key = (body.get("successful") or {}).get("0", {}).get("key")
        if not key:
            raise RuntimeError(f"集合创建响应异常: {str(body)[:300]}")
        log.info("Zotero 创建集合 %s -> %s", name, key)
        return key

    def clear_tagged(self, tag: str = "Paper Radar") -> int:
        """删除打了指定标签的条目，返回删除数。

        Zotero 支持逗号拼 key 批量删；单条删会在限流下慢到不可用。
        """
        keys: list[str] = []
        versions: dict[str, str] = {}
        start = 0
        for _page in range(50):
            r = self._request(
                "GET",
                self._items_url(),
                headers=self._headers(),
                params={"tag": tag, "limit": 100, "start": start},
            )
            r.raise_for_status()
            batch = [it for it in (r.json() or []) if it.get("key")]
            if not batch:
                break
            for it in batch:
                k = it["key"]
                if k not in keys:
                    keys.append(k)
                    versions[k] = str(it.get("version") or "")
            if len(batch) < 100:
                break
            start += len(batch)

        deleted = 0
        for i in range(0, len(keys), 25):
            chunk = keys[i : i + 25]
            try:
                d = self._request(
                    "DELETE",
                    f"{self._items_url()}/{','.join(chunk)}",
                    headers=self._headers(),
                )
                if d.status_code in (200, 204):
                    deleted += len(chunk)
                    log.info("批量删除 %d 条 (%d/%d)", len(chunk), min(i + 25, len(keys)), len(keys))
                else:
                    log.warning("批量删除失败 %s: %s", d.status_code, d.text[:200])
                    # 回退单条，带 If-Unmodified-Since-Version（428 的真正解法）
                    for key in chunk:
                        try:
                            hdrs = dict(self._headers())
                            ver = versions.get(key) or ""
                            if ver:
                                hdrs["If-Unmodified-Since-Version"] = ver
                            one = self._request(
                                "DELETE", f"{self._items_url()}/{key}", headers=hdrs
                            )
                            if one.status_code in (200, 204):
                                deleted += 1
                            else:
                                log.warning("删除失败 %s: %s", key, one.status_code)
                        except Exception as e:
                            log.warning("删除失败 %s: %s", key, e)
                        time.sleep(0.3)
            except Exception as e:
                log.warning("批量删除异常: %s", e)
            time.sleep(1.5)
        log.info("Zotero 清除标签「%s」条目 %d/%d 条", tag, deleted, len(keys))
        return deleted
        log.info("Zotero 清除标签「%s」条目 %d 条（尝试 %d）", tag, deleted, len(tried))
        return deleted

    # ---------- 入库 ----------

    def save_papers(self, papers: list[Paper], collection: str = "") -> None:
        """collection 传集合名（如 AI-daily / comm-daily），自动建或复用。"""
        if not papers:
            return
        col_key = self.ensure_collection(collection) if collection else ""
        items = [to_item(p, collection_key=col_key) for p in papers]
        r = self._request("POST", self._items_url(), headers=self._headers(), json=items, timeout=60)
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
            # 四要点分段：换行保留为 <br>
            body = html.escape(p.zh_summary).replace("\n", "<br/>")
            parts.append(f"<p><strong>📝 中文导读</strong></p><p>{body}</p>")
        if not parts:
            return
        note = {"itemType": "note", "parentItem": parent_key, "note": "".join(parts)}
        r = self._request("POST", self._items_url(), headers=self._headers(), json=[note])
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
        r = self._request("POST", self._items_url(), headers=self._headers(), json=[att])
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


def to_item(p: Paper, collection_key: str = "") -> dict:
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
    if collection_key:
        base["collections"] = [collection_key]
    if p.source_id.startswith(("journal:", "comm:")):
        # 顶刊文章：journalArticle + 期刊名
        journal = p.venue or p.accepted_at or "Unknown journal"
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
