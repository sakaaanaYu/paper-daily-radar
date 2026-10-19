# Paper Radar 📚

每天自动抓取论文源，筛出高质量文献，生成中文导读，推到你用的地方。

输出**全部可选**，配几个推几个：

- **飞书日报卡片**：中文 TL;DR（问题 / 方法 / 结论 / 亮点）+ 星级 + 真实出处，扫一眼决定要不要精读
- **本地 Markdown 笔记**：`notes/YYYY-MM-DD-*.md`，导读 + 留白笔记区
- **Notion**：追加到指定文档，和笔记同结构
- **Zotero 入库**：元数据 + 中文导读笔记 + PDF，多设备自动同步

```
arXiv · OpenReview · 顶刊 RSS · ACM 会议 · OpenAlex 窗口期刊
        │
        ▼
  跨源去重 → 质量启发 / 会议查证 → 分类保底选篇   （或 新文+回补双窗口）
        │
        ▼
  LLM 中文导读 + 星级打分（可插拔）
        │
        ├──→ 飞书日报卡片（按板块分组）
        ├──→ notes/*.md + Notion
        └──→ Zotero 云端库（条目 + 笔记 + PDF）
```

核心思路：**挂不上 PDF 问题不大**——先看导读再决定精读；出处标注真实会议/期刊，不只写 arXiv。

默认装了两个「频道」（track）：`ai`（AI 顶会顶刊）和 `comm`（计算传播 / 科技传播）。fork 后改 `config.yaml` 就能换成你自己想看的——见下面[自定义你自己的雷达](#自定义你自己的雷达)。

## 数据源（5 种源类型，config 里开关）

| 来源 | 覆盖 | 说明 |
|---|---|---|
| OpenReview | ICLR / ICML / NeurIPS / TMLR | 投稿与录用稿 |
| arXiv RSS | cs.AI/LG/CL/HC/CR 等 | 当日新文 |
| 期刊 RSS | Nature Machine Intelligence · Nature Human Behaviour · Science · PNAS · ACM TOCHI · PACM HCI（CSCW/IUI） | 顶刊新文 |
| ACM 会议（OpenAlex） | CHI · UIST | 不在 OpenReview，走 source id 抓，摘要完整 |
| 窗口期刊（OpenAlex） | JCMR / NMS / CCR / ICS / Digital Journalism / Science Communication 等 12 刊 | 新文窗口 + 历史回补窗口，comm 频道用 |

**出处标注**：录用稿显示 `NeurIPS 2025` / `CHI 2025` 等；期刊显示刊名；预印本标 `arXiv preprint (cs.AI)`。

## 默认板块（可在 `config.yaml` 改）

`ai` 频道四板块，每类保底 `per_cat_min` 篇、总共最多 `max_papers_per_day` 篇，剩余名额按相关度补满，防止某类吃满：

| | 板块 |
|---|---|
| ⚖️ | 可信人工智能 |
| 🧑‍💻 | 人机交互 |
| 🗣 | 语言模型与智能体 |
| 🔬 | AI4S 应用与科学 |

`comm` 频道固定两组：📰 新文（近 `fresh_days` 天）+ 📎 回补精选（`backfill_min_months`–`backfill_max_months` 个月前，按质量分挑，避开同作者/同刊撞车）。

## Fork 后第一件事

fork 下来直接跑会踩两个坑，先做掉：

1. **清掉我的去重状态**——`data/seen*.json` 里存着我已经读过的几百条论文指纹，不清的话你头几天会一直空跑（一篇都不推）：

   ```bash
   python -m paper_radar.reset        # 清空 data/seen*.json
   # 或者直接删文件 / 提交一个空数组 [] 进去
   ```

2. **手动打开 Actions**——GitHub 会自动禁用 fork 仓库里的 scheduled workflow（防止别人借你的账号白嫖算力）。到 fork 后的仓库 → **Actions** → 提示「I understand my workflows, go ahead and enable them」→ 点启用。不打开的话，`schedule` 和云端 cron 触发的 `workflow_dispatch` 都跑不动。

然后照下面「一次性配置」配 Secrets，点一次 Run workflow 验证。

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
| `FEISHU_WEBHOOK_URL` | 推飞书则必填 | ai 频道的飞书机器人 webhook |
| `FEISHU_COMM_WEBHOOK_URL` | comm 频道推飞书则必填 | comm 频道的机器人（可与上者同一个） |
| `ZOTERO_API_KEY` | 推 Zotero 则必填 | 上一步的 key |
| `ZOTERO_USER_ID` | 可选 | 不填自动解析 |
| `NOTION_API_KEY` | Notion 沉淀则必填 | Notion Internal Integration token |
| `NOTION_PAGE_ID_AI` / `NOTION_PAGE_ID_COMM` | 同上 | 各频道追加写入的文档 id（也可只设 `NOTION_PAGE_ID` 通用回落） |
| `LLM_API_KEY` | 可选* | 任意 OpenAI 兼容模型 |
| `ANTHROPIC_API_KEY` | 可选* | 用 Claude 则填这个 |

**Variables**（非敏感）：

| Variable | 示例 | 说明 |
|---|---|---|
| `LLM_BASE_URL` | `https://api.deepseek.com/v1` | OpenAI 兼容地址 |
| `LLM_MODEL` | `deepseek-chat` | 模型名 |
| `CLAUDE_MODEL` | `claude-sonnet-5-5` | 仅 Anthropic 方式用 |

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

之后每天 **10:02**（北京时间，UTC `2 2 * * *`）自动跑；另有 10:47 / 12:20 / 14:35 三次备用触发——GitHub 的 schedule 会偶发漏跑或延迟，多点几次名提高命中率。任何一次跑完会往 `data/seen*.json` 写当日标记 `day:YYYY-MM-DD`，后续触发读到标记直接空跑，**不会重复推送**。`data/seen*.json` 回写仓库做去重，**同一篇不会推两次**（跨源身份：出版方 DOI > arXiv id > 标题指纹；预印本见过、后来会议录用版不再推）。

### 定时触发不可靠？加一层云端 cron（推荐）

GitHub Actions 的 `schedule` 在低活跃仓库上命中率不高（本仓库曾连续多天漏跑，日报靠手动触发）。治本办法是加一层**云端定时服务**到点调 GitHub API 手动触发——整个链路都在云上，**换电脑、关机都不影响**。

1. **建一个细粒度 PAT**（只给这一件事用，泄露面最小）：
   GitHub → Settings → Developer settings → [Fine-grained tokens](https://github.com/settings/personal-access-tokens/new) →
   Repository access 选 **Only select repositories** → 这个仓库 → Permissions 只给 **Actions: Read and write** → 生成后复制。

2. **配云端 cron**（任选其一，都有免费额度，跑在对方服务器上）：
   - [cron-job.org](https://cron-job.org)（推荐，免费无广告）
   - [EasyCron](https://www.easycron.com) / [SetCronJob](https://setcronjob.com) 等同类服务

   新建任务，时间填北京时间 **10:05**（比 GitHub 自己的 10:02 稍晚，主次分明），请求配成：

   ```
   Method:  POST
   URL:     https://api.github.com/repos/<你的用户名>/<你的仓库名>/actions/workflows/daily.yml/dispatches
   Headers: Authorization: Bearer <刚建的 PAT>
            Accept: application/vnd.github+json
            X-GitHub-Api-Version: 2022-11-28
   Body:    {"ref":"main"}
   ```

   改了仓库名/文件名要同步改 URL。想更稳可以再加一条 11:05 的，同样会因当日标记而幂等。

3. **验证**：云端 cron 页面一般有「Run now / 立即执行」按钮，点一下，然后到仓库 Actions 页应出现一条 `workflow_dispatch` 触发的 Daily Paper Radar——有就说明通了。

PAT 只存在那个 cron 服务里，不用进本仓库 Secrets（那是给 Actions 用的，外面的 cron 读不到）。泄露了随时到同一页面撤销重建。

## 本地试跑

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env   # 填好后
set -a; source .env; set +a
python -m paper_radar.main            # 跑 ai 频道
python -m paper_radar.run --track comm   # 跑任意频道；--all 跑全部
DRY_RUN=1 python -m paper_radar.main  # 干跑：不写 seen/Zotero，不推飞书（需清空 webhook 变量）
SMOKE_LLM=1 python -m paper_radar.main   # 只测 LLM 导读链路
```

## 自定义你自己的雷达

fork 后只改 `config.yaml` 就能定制：想看什么源、怎么分类、推到哪里。不用碰 Python。

### track（频道）是什么

一个 track = 一个独立频道：自己的论文源、关键词、分类体系、推送出口。默认两个：

| track | kind | 看什么 |
|---|---|---|
| `ai` | `quota` | AI 顶会顶刊，板块保底选篇（每类至少 `per_cat_min` 篇） |
| `comm` | `windows` | 计算传播 / 科技传播，新文 + 回补双窗口 |

两种 `kind` 的选篇逻辑不同，别混用：

| | `quota` | `windows` |
|---|---|---|
| 选篇 | 全部候选排相关度，按板块保底取 top | 新文池取 top N + 历史池按质量分回补 M |
| 质量启发 / 会议查证 | ✅ | ❌（回补池全靠打分排序） |
| seen 记录 | 本轮抓到的**全部**（明日不重复） | 只记**入选的**（回补池明天继续挑） |

### 加 / 删 / 改频道

**改现有频道**：直接改 `tracks.ai` 或 `tracks.comm` 下的字段——源、关键词、分类、推送、篇数。

**关掉一个频道**：把整个 `tracks.comm:` 块删掉（或注释掉）。CI 里对应的那行也删掉，不删也不报错——找不到 track 会安静跳过。

**加一个频道**（比如生物）：

1. `config.yaml` 的 `tracks:` 下抄一份，改名 `bio`，改 `sources` / `keywords` / `categories`（或 `groups`）/ `push`
2. `seen_file` 换个新文件，如 `data/seen_bio.json`
3. `push.notes.kind` / `push.notion.kind` 填 `bio`（决定笔记文件名和 Notion 环境变量）
4. 飞书要用独立机器人就设 `FEISHU_BIO_WEBHOOK_URL`，并在 `push.feishu.webhook_env` 里写这个变量名
5. Notion 要单独文档就设 `NOTION_PAGE_ID_BIO`（不设会回落 `NOTION_PAGE_ID`）
6. `.github/workflows/daily.yml` 里加一行 `python -m paper_radar.run --track bio`（或改用 `python -m paper_radar.run --all` 一次跑全部）

### 配置字段速查（每个 track）

```yaml
tracks:
  bio:
    kind: windows            # quota | windows
    seen_file: data/seen_bio.json

    sources:                 # 5 种源类型，用哪个写哪个，不用的删掉
      arxiv:       {categories: [q-bio, cs.LG], max_results: 80}
      openreview:  {venues: ["ICLR.cc/2026/Conference"], max_per_venue: 40}
      journals:    {feeds: {"Nature": "https://...rss"}, max_per_journal: 20}
      acm_confs:   {venues: {"CHI": "S4363607743"}, max_per_venue: 25}
      comm_journals:         # 仅 windows 用；journals 是 OpenAlex source id 映射
        journals: {"刊名": "S30594836"}
        fresh_days: 30
        backfill_min_months: 3
        backfill_max_months: 18
        max_per_window: 40

    keywords: ["LLM", ...]   # 相关度词，标题权重 > 摘要；留空不过滤

    categories:              # 仅 quota；id 进 LLM 输出别乱改，label 是显示名
      - {id: trust, label: "⚖️ 可信AI", keywords: ["trust", ...]}
    per_cat_min: 2
    max_papers_per_day: 10

    groups:                  # 仅 windows；卡片/笔记的分组显示名
      - {id: fresh, label: "📰 新文"}
      - {id: backfill, label: "📎 回补精选"}
    fresh_count: 2
    backfill_count: 2

    push:                    # 每个出口独立开关，不配就不推
      feishu:  {webhook_env: FEISHU_BIO_WEBHOOK_URL, color: green,
                title_prefix: "🧬 生物雷达", date_suffix: ""}
      notes:   {kind: bio, title: "🧬 生物雷达"}
      notion:  {kind: bio}
      zotero:  {collection: bio-daily}
```

旧的扁平写法（顶层 `arxiv:` / `comm:` 等）仍然兼容，会自动合成 `ai` + `comm` 两个 track。

### 刻意保持代码级的东西

- **质量启发**（顶会名单 `DEFAULT_TOP_VENUES`、排除规则）——不想让 fork 者误关掉质量闸门，要改改 `quality.py`
- **LLM 提示词**（导读四段式、打分口径）——和输出格式耦合，要改改 `summarize.py`

## 质量怎么保证

1. **启发排除**：过短摘要、空标题、明显非论文条目直接丢掉
2. **会议/期刊查证**：OpenReview venue 与 DBLP/出处对齐；命中顶会顶刊名的条目加权
3. **星级**（1–5）：出处档位 + 引用数 + 相关度启发，配置 LLM 后再由模型拉出区分度
4. **分类保底**：避免「全是 LLM」或「全是可信」挤掉视野

## 代码结构

```
paper_radar/
├── main.py            # 入口：跑 ai 频道（或 PAPER_RADAR_TRACK 指定）；SMOKE_LLM 自测
├── comm.py            # 入口：跑 comm 频道
├── run.py             # CLI：--track NAME（可重复）或 --all
├── pipeline.py        # 两条流水线（quota / windows）+ 共享推送与 seen 标记
├── config.py          # 读 config.yaml（tracks schema + 旧扁平写法兼容）
├── models.py          # Paper（含 doi，跨源身份）
├── dedupe.py          # DOI / arXiv id / 标题指纹 去重
├── rank.py            # 关键词相关度
├── classify.py        # 关键词预分类 + 每类保底选篇
├── quality.py         # 排除启发、出处查证、星级（刻意不配置化）
├── summarize.py       # 可插拔 LLM：中文导读 + 打分（提示词在此）
├── notes.py           # 本地 Markdown 日报（labels 来自 config groups）
├── store.py           # seen*.json 已读记录 + 当日已跑标记
├── reset.py           # 重置 seen（调试用）
├── sources/
│   ├── arxiv.py       # arXiv RSS
│   ├── openreview.py  # OpenReview API
│   ├── journals.py    # 顶刊 RSS + OpenAlex 摘要补全
│   ├── acm_conf.py    # CHI/UIST（OpenAlex）
│   └── comm_journals.py  # 窗口期刊（OpenAlex）：新文 + 回补双窗口
└── push/
    ├── feishu.py      # 飞书卡片（只显示导读，不贴 abstract）
    ├── notion.py      # Notion 文档追加（NOTION_PAGE_ID_* 按 kind 解析）
    └── zotero.py      # 条目 + 笔记 + PDF
```

## 排查

| 现象 | 检查 |
|---|---|
| 飞书没收到 | Actions 日志「飞书发送失败」；webhook 是否过期/机器人被移出群 |
| Zotero 没入库 | 日志「Zotero 写入失败」；key 是否含 library:write |
| 卡片没有中文导读 | 未配 `LLM_API_KEY` / `ANTHROPIC_API_KEY`，卡片会提示「导读未生成」 |
| PDF 有条目没文件 | ① OpenReview PDF 有反爬，需浏览器手动下；② **Zotero 免费存储 300MB**，满了会跳过 |
| 一直推同一篇 | `data/seen*.json` 有没有回写（Actions 需 `contents: write`） |
| 今日无新增 | 正常；或调大 `max_results` / 放宽 keywords |
| 缺 CHI/UIST | 确认 `acm_confs` 的 OpenAlex source id；或该时段 OpenAlex 尚无新文 |
| 某个频道完全没动静 | 看 Actions 日志「配置里没有 track」——config 里删了但 CI 还在跑它会安静跳过 |
| 只想本地试推一个频道 | `python -m paper_radar.run --track comm`；不想真推就把 `FEISHU_*_WEBHOOK_URL` 留空 |

## 开源与安全

- 本项目 MIT 协议，欢迎 fork / PR
- **密钥只放 GitHub Secrets 或本地 `.env`**（已 gitignore），不要写进 `config.yaml`
- Secrets 加密存储、日志打码；仓库设为 public **不会**把 Secrets 暴露出去，但 fork 的 Actions **拿不到**你的 Secrets
- `data/seen*.json` 只是去重状态（含当日已跑标记），不含隐私

## License

[MIT](LICENSE)
