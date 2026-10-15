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

## 3. 论文质量保障
- [x] 排除式启发：摘要过短 / position paper / workshop 文直接扔
- [x] 录用查证：Semantic Scholar 批量查询 + DBLP 兜底
- [x] OpenReview 来源按 venueid 直接认定录用
- [x] 顶会顶刊别名表（NeurIPS/ICLR/ICML/ACL/CHI/…）
- [x] 顶会录用加权 +5（保持视野：只升序不淘汰）
- [x] CI 跑通：256 抓取 / 10 篇入选 / 飞书分组卡片 / Zotero 入库

## 待办
- [ ] 用户配置 LLM key（任选 DeepSeek/Kimi/Qwen/GLM/OpenAI/Ollama）
- [ ] （可选）清理 Zotero 存储 304MB/300MB 后恢复 PDF 自动挂载
- [ ] （可选）删除 Zotero 里残留的 test.pdf 空附件
- [ ] （二期）Zotero 标签反馈回路，优化关键词与分类
