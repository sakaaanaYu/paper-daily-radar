# Paper Radar 📚

每天自动抓取 **AI 顶会 / 顶刊** 最新论文，筛出高质量文献，推两条线：

- **路径 1 — 飞书日报卡片**：中文 TL;DR（问题 / 方法 / 结论 / 亮点）+ 星级 + 真实出处，扫一眼决定要不要精读
- **路径 2 — Zotero 入库**：元数据 + 中文导读笔记 + PDF，多设备自动同步

```
arXiv · OpenReview · 顶刊 RSS · CHI/UIST (OpenAlex)
        │
        ▼
  跨源去重 → 质量启发 / 会议查证 → 分类保底选篇
        │
        ▼
  LLM 中文导读 + 星级打分（可插拔）
        │
        ├──→ 飞书日报卡片（按板块分组）
        └──→ Zotero 云端库（条目 + 笔记 + PDF）
```

核心思路：**挂不上 PDF 问题不大**——先看导读再决定精读；出处标注真实会议/期刊，不只写 arXiv。

## 数据源

| 来源 | 覆盖 | 说明 |
|---|---|---|
| OpenReview | ICLR / ICML / NeurIPS / TMLR | 投稿与录用稿 |
| arXiv RSS | cs.AI/LG/CL/HC/CR 等 | 当日新文 |
| 期刊 RSS | Nature Machine Intelligence · Nature Human Behaviour · Science · PNAS · ACM TOCHI · PACM HCI（CSCW/IUI） | 顶刊新文 |
| OpenAlex | CHI · UIST | ACM 会议不在 OpenReview，走 source id 抓，摘要完整 |

**出处标注**：录用稿显示 `NeurIPS 2025` / `CHI 2025` 等；期刊显示刊名；预印本标 `arXiv preprint (cs.AI)`。

## 默认板块（可在 `config.yaml` 改）

| | 板块 |
|---|---|
| ⚖️ | 可信人工智能 |
| 🧑‍💻 | 人机交互 |
| 🗣 | 语言模型与智能体 |
| 🔬 | AI4S 应用与科学 |

每天最多 `max_papers_per_day` 篇；每类保底 `per_cat_min` 篇，剩余名额按相关度补满，防止某类吃满名额。

## 一次性配置

### 1. 飞书机器人（5 分钟）

1. 任意飞书群 → 设置 → **群机器人** → 添加 → **自定义机器人**
2. 复制 webhook 地址

### 2. Zotero API（5 分钟）

1. <https://www.zotero.org/settings/keys> → **Create new private key**
2. 勾选：**Allow library access**、**Allow write access to your library**、**Allow write access to file storage**
3. 页面上能看到 **userID**（可不填，程序自动解析）

> 多设备同步前提：登录**同一个 zotero.org 账号**并开启文献库同步。
> PDF 大量入库时建议配好文件同步（Zotero Storage 或坚果云 WebDAV）。

### 3. GitHub Secrets / Variables

仓库 → Settings → Secrets and variables → **Actions**。

**Secrets**（加密存储，公开仓库也不会泄露）：

| Secret | 必填 | 说明 |
|---|---|---|
| `FEISHU_WEBHOOK_URL` | ✅ | 飞书机器人 webhook |
| `ZOTERO_API_KEY` | ✅ | 上一步的 key |
| `ZOTERO_USER_ID` | 可选 | 不填自动解析 |
| `LLM_API_KEY` | 可选* | 任意 OpenAI 兼容模型 |
| `ANTHROPIC_API_KEY` | 可选* | 用 Claude 则填这个 |

**Variables**（非敏感）：

| Variable | 示例 | 说明 |
|---|---|---|
| `LLM_BASE_URL` | `https://api.deepseek.com/v1` | OpenAI 兼容地址 |
| `LLM_MODEL` | `deepseek-chat` | 模型名 |
| `CLAUDE_MODEL` | `claude-sonnet-5-5` | 仅 Anthropic 路径用 |

\* 都不填也能跑，但没有中文导读和 LLM 打分——**建议至少配一个**。LLM 是插拔的：改两个变量即可切换服务商。

| 服务商 | LLM_BASE_URL | LLM_MODEL |
|---|---|---|
| DeepSeek | `https://api.deepseek.com/v1` | `deepseek-chat` |
| Kimi | `https://api.moonshot.cn/v1` | `moonshot-v1-8k` |
| 通义千问 | `https://dashscope.aliyuncs.com/compatible-mode/v1` | `qwen-plus` |
| 智谱 GLM | `https://open.bigmodel.cn/api/paas/v4` | `glm-4-flash` |
| OpenAI | `https://api.openai.com/v1` | `gpt-4o-mini` |
| 本地 Ollama | `http://localhost:11434/v1` | `qwen2.5` |

### 4. 首次运行

仓库 → **Actions** → Daily Paper Radar → **Run workflow**。

之后每天 07:35（北京时间，UTC 23:35）自动跑；`data/seen.json` 回写仓库做去重，**同一篇不会推两次**（跨源身份：出版方 DOI > arXiv id > 标题指纹；预印本见过、后来会议录用版不再推）。

## 本地试跑

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env   # 填好后
set -a; source .env; set +a
python -m paper_radar.main
```

## 自定义

编辑 `config.yaml`：

- `keywords`：相关度关键词（标题权重 > 摘要），留空则不过滤
- `categories`：板块 id / label / 关键词（id 进 LLM 输出，改了要同步）
- `per_cat_min`：每类保底篇数
- `arxiv.categories` / `openreview.venues` / `journals` / `acm_confs`：数据源增删

## 质量怎么保证

1. **启发排除**：过短摘要、空标题、明显非论文条目直接丢掉
2. **会议/期刊查证**：OpenReview venue 与 DBLP/出处对齐；命中顶会顶刊名的条目加权
3. **星级**（1–5）：出处档位 + 引用数 + 相关度启发，配置 LLM 后再由模型拉出区分度
4. **分类保底**：避免「全是 LLM」或「全是可信」挤掉视野

## 代码结构

```
paper_radar/
├── main.py            # 抓取 → 去重 → 筛选 → 导读 → 推送/入库
├── config.py          # 读 config.yaml
├── models.py          # Paper（含 doi，跨源身份）
├── dedupe.py          # DOI / arXiv id / 标题指纹 去重
├── rank.py            # 关键词相关度
├── classify.py        # 关键词预分类 + 每类保底选篇
├── quality.py         # 排除启发、出处查证、星级
├── summarize.py       # 可插拔 LLM：中文导读 + 打分
├── store.py           # seen.json 已读记录
├── sources/
│   ├── arxiv.py       # arXiv RSS
│   ├── openreview.py  # OpenReview API
│   ├── journals.py    # 顶刊 RSS + OpenAlex 摘要补全
│   └── acm_conf.py    # CHI/UIST（OpenAlex）
└── push/
    ├── feishu.py      # 飞书卡片（只显示导读，不贴 abstract）
    └── zotero.py      # 条目 + 笔记 + PDF
```

## 排查

| 现象 | 检查 |
|---|---|
| 飞书没收到 | Actions 日志「飞书发送失败」；webhook 是否过期/机器人被移出群 |
| Zotero 没入库 | 日志「Zotero 写入失败」；key 是否含 library:write |
| 卡片没有中文导读 | 未配 `LLM_API_KEY` / `ANTHROPIC_API_KEY`，卡片会提示「导读未生成」 |
| PDF 有条目没文件 | ① OpenReview PDF 有反爬，需浏览器手动下；② **Zotero 免费存储 300MB**，满了会跳过 |
| 一直推同一篇 | `data/seen.json` 有没有回写（Actions 需 `contents: write`） |
| 今日无新增 | 正常；或调大 `max_results` / 放宽 keywords |
| 缺 CHI/UIST | 确认 `acm_confs` 的 OpenAlex source id；或该时段 OpenAlex 尚无新文 |

## 开源与安全

- 本项目 MIT 协议，欢迎 fork / PR
- **密钥只放 GitHub Secrets 或本地 `.env`**（已 gitignore），不要写进 `config.yaml`
- Secrets 加密存储、日志打码；仓库设为 public **不会**把 Secrets 暴露出去，但 fork 的 Actions **拿不到**你的 Secrets
- `data/seen.json` 只是去重状态，不含隐私

## License

[MIT](LICENSE)
