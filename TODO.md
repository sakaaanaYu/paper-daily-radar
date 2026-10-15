# 任务清单

## 1. 改 API：摘要模块支持任意模型
- [x] 通用 OpenAI 兼容适配器（DeepSeek / Kimi / 通义 / 智谱 / OpenAI / 本地 Ollama）
- [x] 保留 Claude（Anthropic）路径
- [x] 环境变量切换：`LLM_BASE_URL` / `LLM_API_KEY` / `LLM_MODEL`
- [ ] 用户选定 provider 并配置 Secret 验证效果

## 2. 卡片分类
- [x] 设计论文分类体系（5 领域：HCI素养/学习教育/媒体多模态/GenAI技术/认知心理）
- [x] 混合分类：关键词预分类 + LLM 修正
- [x] 飞书卡片按分类分组展示
- [x] Zotero 条目打 cat:* 标签
- [ ] 用户配置 LLM key 后验证 LLM 修正效果

## 3. 论文质量保障（讨论）
- [ ] arXiv 良莠不齐的应对策略
- [ ] 定出可落地的质量门槛
