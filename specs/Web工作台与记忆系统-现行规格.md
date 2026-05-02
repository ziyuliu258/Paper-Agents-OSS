# Web 工作台与记忆系统现行规格

> 现行规格。描述 2026-04 当前仓库已经落地的 Web-only 架构、selector、WorkingMemory、Memory V2、报告结构模式、报告变体与作业生命周期语义。

## 1. 系统定位

Paper Agent 当前已经收口为 **Web-only 学术论文解读系统**。

输入：

- 自动模式：基于 topic / venue / institution / track 自动选 1 篇论文
- 手动模式：上传单个 PDF
- 自动模式下载阶段支持 HTML 论文页兜底；若 landing page 最终不是 PDF，会保留 HTML source 并走 HTML 提取链路

输出：

- job 级中英文解读报告
- job 级 selector / working memory / distilled summary artifacts
- Profile 级长期记忆写回
- Web 端可读可编辑的报告工作台 / 记忆工作台 / 图谱工作台
- Web 端浏览器本地 Settings 页面，用于填写 provider endpoint / API key / embedding backend / 模型别名 / 默认 run config

## 2. 当前总架构

```text
React Web UI
  └─ /api/* -> FastAPI
       └─ JobManager
            ├─ auto: PaperSelectorAgent
            │    ├─ venue-first or general search
            │    ├─ dedupe
            │    ├─ blended rerank
            │    ├─ PDF URL enrichment / HTML redirect follow
            │    ├─ AIC enrichment
            │    ├─ topic-fit gate
            │    └─ Top-1 selection + source download（PDF 优先，HTML 兜底）
            ├─ manual: 上传 PDF -> job fetch dir
            ├─ Phase 1: PaperProcessorAgent
            │    ├─ PDF: 图表识别 / 裁剪
            │    └─ HTML: 正文提取 / html_bundle
            └─ Phase 2: PaperInterpreterAgent
                 ├─ build_paper_notes
                 ├─ selected paper topic audit
                 ├─ auto profile match / auto create
                 ├─ WorkingMemory 初始化
                 ├─ retrieval bundles 注入
                 ├─ Group A / Group B 执行 T1-T7
                 ├─ report audit + conservative repair
                 ├─ distillation + promotion funnel
                 ├─ Memory V2 writeback
                 ├─ report.en.md / report.md 组装
                 ├─ 中文 memory artifacts 预生成
                 └─ report_refiner 生成 file-backed variants
```

## 3. 作业模式与生命周期

### 3.1 auto 与 manual

- `auto`：由 selector 负责选论文，只在 **source 可获取且通过 topic-fit gate** 的候选中做最终 Top-1 精选；优先使用真实 PDF，拿不到时允许保留 HTML source
- `manual`：用户上传 PDF，后端复制到 `data/fetch/jobs/{job_id}/`
- 若 topic 只有中文 name/query、缺少英文关键词，后端会在 selector 开始前自动补英文 search hints

job 还区分 `profile_mode`：

- `explicit`：用户显式绑定 profile
- `auto`：先不绑定 profile，等 `paper_notes` 完成后自动匹配或自动创建

`default` profile 只在用户显式选择时使用；自动路由默认忽略它。
当前自动路由会构建 profile 的中英双语 fingerprint，并结合 embedding 相似度与规则分数做跨语言匹配，不再只靠英文 token 命中。

### 3.2 取消 / 重试 / 重跑 / 强停

- **cancel**：请求取消当前任务，job 记录保留
- **retry**：仅适用于 `failed` job，**原地重试**，沿用原来的 `job_id`
- **rerun / regenerate**：创建新的 replacement job；新 job 成功后自动清理旧报告、旧目录和旧 job 记录
- **force-stop**：强制停止并 purge 当前 job，同时删除：
  - `results/jobs/{job_id}`
  - `data/fetch/jobs/{job_id}`
  - `data/cache/jobs/{job_id}`
  - 对应 job record / paper record
  - 该 job 写回到指定 profile 的 memory bundle

## 4. Selector 现状

### 4.1 候选来源

- ArXiv
- Semantic Scholar
- OpenReview
- OpenAlex
- DBLP

支持两类入口：

- **venue-first**：优先拉取 `preferred_venues` 对应论文清单
- **general search**：当后端 `preferred_venues` 为空时，按 topic name / query / keywords 检索

### 4.2 候选筛选

关键步骤：

1. 按 DOI / arXiv ID / 归一化标题去重
2. 依据 `track`、时间窗、引用门槛、机构偏好过滤
3. 使用 embedding + lexical overlap blended score 做 rerank
4. 通过 DOI landing page / HTML probe / meta tag 补全 source；若 HTML 页内存在真实 PDF 跳转，会优先跟随到 PDF
5. 仅把 **source 可获取** 的候选送入 AIC enrichment；若最终只有 HTML，则在下载后走 HTML 提取链路
6. 在 enriched 候选上追加 `topic_fit_judge`，产出 `fit_label / topic_fit_score / matched_aspects / mismatch_reasons`
7. 只有通过 fit 门槛的候选才允许进入最终 selector
8. 若 0 篇通过，则 selection 直接失败，不再强行挑 1 篇，并保留完整 diagnostics

### 4.3 Selection Memory

若用户显式选择 profile，或 auto 模式被 topics soft-route 到某个 profile，selector 会注入 `for_selector` retrieval bundle，帮助：

- 优先补足领域知识空白
- 对冲突中的 claim 寻找更直接证据
- 避免重复选取已经处理过的高度相似论文

## 5. Interpreter 现状

### 5.1 shared `paper_notes`

解释阶段先统一生成 `paper_notes`，包含：

- metadata
- paper_summary
- problem
- method_steps
- main_results
- limitations
- glossary_seed
- figure_highlights

这一步为后续所有 T1-T7 子任务提供共享上下文。

### 5.1.2 HTML source 处理

- 若 source 实际为 HTML，`PaperProcessorAgent` 不再尝试图表识别，而是抽取 `title / abstract / sections / plain_text`
- `build_paper_notes` 在 HTML 模式下改走 text-only LLM 输入，使用 `html_bundle` 而不是 PDF file input
- report audit 在 HTML 模式下不再把 section 数量误当成 PDF 页数去做越界判定

### 5.1.1 选中文章二次纠偏

- `paper_notes` 生成后会对选中的论文再做一次 topic audit
- 若 audit 仍判定偏题，则 job 会在进入解释主阶段前中止
- audit 结果会回写到 `selector_diagnostics.json`

### 5.2 分组执行

- **Group A**：`T1 -> T5 -> T6`
- **Group B**：`T2 -> T3 -> T4`
- **T7**：等待 A/B 完成后执行

其中：

- Group B 的 T2/T3/T4 使用结构化输出 schema
- T3 / T4 会显式继承上一个 section 的 `prior_context`
- Method section 会固定输出 `pipeline_overview / modules / training_objectives`

### 5.3 WorkingMemory

WorkingMemory 是 job 级短期记忆层，当前承担：

- `paper_notes`
- retrieval context
- task outputs
- observations
- open questions
- draft claims
- terminology map
- promotion candidates
- metrics

它既服务于长期记忆写回，也直接为 Report Viewer 提供可观测运行内解释过程。

### 5.4 Auto Profile Routing

- auto 模式下，selector 阶段只会做“软路由”，借用已有 profile 的检索记忆
- 真正的 profile 归属会在 `paper_notes` 完成后再决定
- 若最佳 profile 分数达到阈值且明显领先第二名，则自动归入该 profile
- 否则自动创建新 profile，名称使用“主领域 + 主任务”的短名，描述使用论文核心摘要压缩版

## 6. 长期记忆 Memory V2

### 6.1 分层对象

- entity
- claim
- evidence
- synthesis
- graph
- review
- revision

### 6.2 retrieval bundles

当前已正式使用：

- `for_selector`
- `for_interpreter`
- `for_review_conflict`
- `for_translation_style`

### 6.3 writeback

长期记忆写回前，系统会先从 WorkingMemory 中做 distillation，并把候选分成：

- `accepted`
- `review_required`
- `rejected`

写回时保留 provenance，支持：

- 按 `job_id` 删除 memory bundle
- 按 `paper_id` 删除 memory bundle
- 自动裁剪 orphaned claim / entity / synthesis / edge

### 6.3.1 Profile 间批量迁移

- Profile Detail 支持批量选择当前 profile 下的 paper/job bundle，并通过 `POST /api/profiles/{profile_id}/move-papers` 迁移到另一个 profile
- 迁移粒度按 `job_id` 对应的 provenance bundle 处理，不是只改 `jobs.profile_id`
- 后端会同步迁移：
  - `memory_writebacks`
  - 该 writeback 产生的 claims / evidence 归属
  - 该 writeback 产生的 synthesis / graph edges
  - 被这些 claims 引用的 entity 及其必要 relink / clone / merge
- 若目标 profile 已有同名 entity 或相同 paper-edge，系统会尽量复用或合并，避免重复对象
- 迁移完成后会分别重建 source / target profile 的 cognition 与长期记忆快照，确保旧 profile 清掉这些论文的影响，新 profile 吸收这些论文的影响

### 6.4 manual lock 与 review queue

- 用户手工修改过的对象会标记 `manual_locked`
- 后续 AI 更新不会直接覆盖，而是进入 review queue
- review resolve 支持 adopt suggested / mark reviewed / dismiss

## 7. 报告系统现状

### 7.0 Report Audit

- `run_all_tasks()` 完成后、memory writeback 和 assemble 前，会插入独立 `ReportAuditor`
- 审查分为：
  - groundedness audit：重点检查结构化 claims 是否带有效 evidence / page anchor
  - consistency audit：检查 T1/T5/T6/T7 是否与结构化 section 和关键数值一致
- 若发现高风险 groundedness 问题，会先做一次保守修复，再重审一次
- 审查结果落盘到 `results/jobs/{job_id}/report_audit.json`
- 若复审后仍有中高风险问题，job 仍可完成，但会带 `warning` 状态与 artifact 入口

### 7.1 报告组装

assembler 当前会生成：

- `report.en.md`
- `report.md`

并负责：

- figure 分类与嵌入
- 论文 metadata 提取
- repository URL 解析与校验
- `classic / pmrc` 两类结构成文

### 7.2 report structure modes

- `classic`
  - 背景
  - 方法
  - 实验
  - 消融
  - 局限
  - 总结
- `pmrc`
  - Problem and Motivation
  - Method and Key Mechanisms
  - Results, Comparisons, and Ablations
  - Conclusions, Limitations, and Takeaways

### 7.3 report variants

Report Viewer 不只展示原稿，还可以基于：

- 当前 report markdown
- `distilled_memory_summary.md`
- `working_memory.json`
- `report.en.md`

生成新的 **file-backed report variants**：

- Markdown 文件：`results/jobs/{job_id}/variants/{variant_id}.md`
- 元信息文件：`results/jobs/{job_id}/variants/{variant_id}.json`

这套 report refinement 支持：

- 保持结构不变
- 切换为 `classic` / `pmrc`
- `concise / balanced / detailed` 细节粒度调整
- 方法 / 实验 / narrative 的 grounded 重写

## 8. Job Artifact 现状

当前 job 级主要产物：

```text
results/jobs/{job_id}/report.md
results/jobs/{job_id}/report.en.md
results/jobs/{job_id}/working_memory.json
results/jobs/{job_id}/working_memory.zh.json
results/jobs/{job_id}/distilled_memory_summary.md
results/jobs/{job_id}/distilled_memory_summary.zh.md
results/jobs/{job_id}/selector_diagnostics.json
results/jobs/{job_id}/report_audit.json
results/jobs/{job_id}/variants/*.md
results/jobs/{job_id}/variants/*.json
results/jobs/{job_id}/assets/...
```

## 9. 配置现状

### 9.1 双层配置

- `config.yaml`：默认 run 参数，主要面向 topic / selection / models / report / storage
- 服务器 `.env`：运行时来源之一，主要可承载 provider endpoint、API key、embedding backend、R2、模型别名；可通过独立 `ENV_RUNTIME_ACCESS_GUARD=password` + `ENV_RUNTIME_PASSWORD_HASH` 启用访问保护
- 浏览器 localStorage：浏览器本地运行时配置，主要面向 provider endpoint、API key、embedding backend、R2、模型别名，以及浏览器自己的默认 run config
- `data/runtime_config.yaml`：服务器侧 runtime settings 接口存储；当前执行链路默认不回落依赖它
- 执行时当前支持两种运行时来源：
  - `.env`：服务器运行时配置
  - browser local override：当前浏览器 localStorage + 请求头 override
- 当 `ENV_RUNTIME_ACCESS_GUARD=off` 时，后端会忽略 browser override，统一只使用服务器 `.env`
- 当 `ENV_RUNTIME_ACCESS_GUARD=password` 时，后端会自动按“已解锁 `.env` 优先，否则回落 browser override”的规则选源；两边都不可用时才报错

### 9.2 Settings API 与安全语义

- `GET /api/config` / `PUT /api/config`：读写默认 run config
- `GET /api/config/runtime` / `PUT /api/config/runtime`：读写服务器默认 runtime settings
- `GET /api/config/runtime/access`：读取服务器 `.env` access guard 状态，以及 browser runtime 是否启用
- `POST /api/config/runtime/env-unlock/challenge` / `POST /api/config/runtime/env-unlock/verify`：服务器 `.env` 模式的可选解锁握手
- 浏览器本地 runtime settings 通过请求头传给后端，请求上下文和后台 task 会继承这份 override；但只有 `ENV_RUNTIME_ACCESS_GUARD=password` 时这些 override 才会参与实际运行时求值
- 若服务器 `.env` 已解锁，后端优先读取服务器 `.env`；若 guard 开启但当前浏览器未解锁，则后端回落到当前浏览器的 runtime settings
- 解锁时前端发送的是基于 challenge 的 derived proof，不是明文密码；成功后会得到一个短期 session token
- runtime secret 在 Settings UI 中只做本地遮罩展示；输入框留空表示“保留当前浏览器已存值”，只有显式 `clear` 才会真正清空本地值
- 当前实现是**浏览器本地隔离**方案：不同浏览器可使用不同 key / provider / model alias；若 server `.env` 未解锁且浏览器也没有可用 override，后端才会拒绝需要模型的请求

## 10. 前端工作台现状

### 10.1 RunPage

支持：

- auto / manual 切换
- profile 选择、创建与 Auto assign
- topic keyword 生成
- report structure 选择
- 实时日志
- working memory 中间快照
- retry draft 回填
- force-stop + purge

注意：当前 Web Run 页的 auto 模式要求：

- `preferred_venues` 至少 1 个
- `date_range_days >= 30`

### 10.2 ReportViewer

当前不是单纯 Markdown 查看器，而是统一报告工作台：

- Final report
- Working memory snapshot
- Distilled memory summary
- Selector diagnostics
- Report audit
- Report refinement variants

### 10.3 ProfileDetail / MemoryWorkspace

当前支持：

- brief / overview / curated digest
- linked paper activity
- 删除非 `default` profile（若存在活跃 job 会被阻止）
- Knowledge Base / Graph / Timeline / Reviews / History
- 整页统一中英切换
- 直接编辑 entity / claim / evidence / synthesis / edge

### 10.4 SettingsPage

当前支持：

- 配置 server `.env` 解锁与浏览器本地 fallback runtime settings
- 配置 OpenAI-compatible / Lite / embedding / Semantic Scholar / MinerU / R2 等 provider 凭据
- 设置 embedding model 与模型别名：`gpt_pro` / `gem_pro` / `gem_flash` / `gem_image` / `lite_model`
- 编辑默认 topic / selection / report / model-role 配置
- 查看当前浏览器里 secret 是否已配置，但不会明文泄露历史 key
- 若服务器启用了 `ENV_RUNTIME_ACCESS_GUARD=password`，会出现 `.env` 解锁输入区；未启用时会明确显示当前 server env access guard 未启用

## 11. 当前已知约束

- Run 页当前只处理 `topics[0]`
- Auto selector 的 general search 后端已支持，但当前 Web Run UI 默认锁定为 venue-constrained 入口
- 旧 job 不会自动补做新版本 localized artifacts 或 variants；要看到新规则效果，通常需要 rerun / regenerate
- 当前 report audit 以 groundedness / consistency 的保守规则审查为主，目标是降低明显错误和跑偏概率，不承诺“绝对正确”
- `Export PDF` 仍基于浏览器打印，不是后端二进制 PDF 渲染
- 浏览器本地 settings 依赖 `localStorage`，因此更适合“同一浏览器一个使用者”的轻量隔离；如果未来做 SaaS 化，仍需要真正的用户级 secret 隔离
