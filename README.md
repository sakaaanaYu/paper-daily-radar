# Paper Radar 📚

每天自动抓取 arXiv + OpenReview 的最新论文，筛选出与你研究兴趣相关的高质量文献：

- **路径 1 — 飞书日报卡片**：标题 + 星级 + 一句话看点，早上扫一眼
- **路径 2 — Zotero 入库**：元数据 + 中文摘要笔记 + PDF，Mac/Windows 自动同步

```
arXiv + OpenReview ──→ 关键词筛选 ──→ Claude 中文摘要/打分 ──┬──→ 飞书日报卡片
                                                            └──→ Zotero 云端库（PDF + 笔记）
```

## 一次性配置

### 1. 飞书机器人（5 分钟）

1. 打开任意飞书群 → 设置 → **群机器人** → 添加机器人 → **自定义机器人**
2. 复制 webhook 地址

### 2. Zotero API（5 分钟）

1. 打开 <https://www.zotero.org/settings/keys> → **Create new private key**
2. 权限勾选：**Allow write access to your library** + **Allow write access to file storage**
3. 保存后页面上能看到 **userID**（也可以不填，程序会自动解析）

> 两台电脑同步前提：Mac/Windows 登录**同一个 zotero.org 账号**，且都开启「同步文献库」。
> PDF 大量入库时建议配好文件同步（Zotero Storage 或坚果云 WebDAV），否则附件只在一台机器可见。

### 3. GitHub Secrets

新建仓库（或推这个目录上去）→ Settings → Secrets and variables → **Actions**：

| Secret | 必填 | 说明 |
|---|---|---|
| `FEISHU_WEBHOOK_URL` | ✅ | 飞书机器人 webhook |
| `ZOTERO_API_KEY` | ✅ | 上一步生成的 key |
| `ZOTERO_USER_ID` | 可选 | 不填自动解析 |
| `LLM_API_KEY` | 可选* | 摘要模型 key（DeepSeek/Kimi/通义/智谱…任选） |
| `ANTHROPIC_API_KEY` | 可选* | 用 Claude 则填这个 |

\* 两个都不填也能跑，但就没有 TL;DR 和质量打分——**建议至少配一个**。

配套 **Variables**（Settings → Variables）：

| Variable | 示例 | 说明 |
|---|---|---|
| `LLM_BASE_URL` | `https://api.deepseek.com/v1` | 服务商兼容地址 |
| `LLM_MODEL` | `deepseek-chat` | 模型名 |

常见服务商（都是 OpenAI 兼容，改上面两个变量即可切换）：

| 服务商 | LLM_BASE_URL | LLM_MODEL |
|---|---|---|
| DeepSeek | `https://api.deepseek.com/v1` | `deepseek-chat` |
| Kimi | `https://api.moonshot.cn/v1` | `moonshot-v1-8k` |
| 通义千问 | `https://dashscope.aliyuncs.com/compatible-mode/v1` | `qwen-plus` |
| 智谱 GLM | `https://open.bigmodel.cn/api/paas/v4` | `glm-4-flash` |
| OpenAI | `https://api.openai.com/v1` | `gpt-4o-mini` |
| 本地 Ollama | `http://localhost:11434/v1` | `qwen2.5` |

### 4. 首次运行

仓库 → **Actions** → Daily Paper Radar → **Run workflow**，跑完看日志。

之后每天 07:35（北京时间）自动跑；`data/seen.json` 自动回写仓库做去重，不会重复推同一篇。

## 本地试跑

```bash
cd paper-radar
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# 填好 .env（参考 .env.example）后
set -a; source .env; set +a
python -m paper_radar.main
```

## 自定义

编辑 `config.yaml`：

- `keywords`：筛选关键词，命中**标题**记 3 分、摘要 1 分，取相关度最高的 `max_papers_per_day` 篇；留空则不做关键词过滤
- `arxiv.categories`：arXiv 分类
- `openreview.venues`：顶会 venueid，例如 `NeurIPS.cc/2026/Conference`（换成你关注的年份/会议）

## 代码结构

```
paper_radar/
├── main.py            # 抓取 → 筛选 → 摘要 → 推送/入库
├── config.py          # 读 config.yaml
├── models.py          # Paper 数据结构
├── rank.py            # 关键词相关度打分
├── summarize.py       # Claude 中文摘要 + 质量分（可选）
├── store.py           # 已见论文去重
├── sources/
│   ├── arxiv.py       # arXiv API
│   └── openreview.py  # OpenReview API
└── push/
    ├── feishu.py      # 飞书群机器人卡片
    └── zotero.py      # Zotero Web API（条目 + 笔记 + PDF）
```

## 排查

| 现象 | 检查 |
|---|---|
| 飞书没收到 | Actions 日志找「飞书发送失败」；webhook 是否过期/机器人被移出群 |
| Zotero 没入库 | 日志找「Zotero 写入失败」；key 是否含 library:write |
| PDF 有条目没文件 | 两个原因：① OpenReview 的 PDF 有反爬挑战，程序下不了，需浏览器打开条目链接手动下载；② **Zotero 免费存储只有 300MB**，满了会跳过上传（日志会提示），可清理 <https://www.zotero.org/settings/storage> 或升级 Zotero Storage |
| 一直推同一篇 | `data/seen.json` 有没有正常回写（Actions 权限需 contents: write） |
| 今日无新增 | 正常；或调大 `max_results` / 放宽 keywords |
