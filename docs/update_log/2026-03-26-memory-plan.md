# 2026-03-26 记忆机制演进计划

## 背景

当前项目已经有较完整的 **长期记忆 Memory V2**，能够按 `profile` 维度持久化：

- entity
- claim
- evidence
- synthesis
- graph
- review
- revision

但解释链路内部仍然缺少一个正式的 **短期记忆层**。目前 `PaperInterpreterAgent -> task_runner -> assembler -> translator` 之间主要依赖：

- `parsed_paper`
- `paper_notes`
- `task_results`
- `memory_context`

这些对象虽然能工作，但它们更像中间变量集合，而不是一个明确的“本次运行工作记忆（working memory）”。

结果是：

- task 之间共享信息的方式不够显式
- 临时观察、待确认问题、裁决备注没有统一归宿
- 长期记忆写回仍然偏“整篇总结后一次抽取”，缺少一次运行内的蒸馏层
- 长期记忆 retrieval 仍偏统一大块注入，而不是按用途召回

## 目标

本轮分两条线推进：

1. 增加 **短期记忆 WorkingMemory**
2. 对 **长期记忆 retrieval / distillation** 做结构化优化

总体原则：

- 短期记忆是 **job 级 / 单次 interpreter run 级**
- 长期记忆是 **profile 级 / 跨论文持久化**
- 先把临时分析放进短期记忆
- 再把高价值、证据充分、去重后的内容蒸馏写回长期记忆

## 方案概览

### 一、短期记忆 WorkingMemory

新增一个内存态对象，作用域限定在一次 `PaperInterpreterAgent.run()` 内。

它承担的职责：

- 保存本次运行的共享认知状态
- 作为 T1-T7 / 裁决 / assembler / translator 的共享工作台
- 承接长期记忆 retrieval 的结果
- 承接后续 distillation 的候选输入

第一版建议字段：

- `paper_notes`
- `retrieved_context`
- `task_outputs`
- `observations`
- `open_questions`
- `draft_claims`
- `evidence_cache`
- `terminology_map`
- `promotion_candidates`
- `adjudication_notes`
- `metrics`

第一版先不落库，只做内存态对象，降低改动风险。

### 二、长期记忆 retrieval 优化

不再把 profile memory 当成统一大块文本去注入，而是按用途拆 retrieval：

- `for_selector`
- `for_interpreter`
- `for_review_conflict`
- `for_translation_style`

本轮优先做：

- `for_interpreter`

返回固定 shape 的 retrieval bundle，例如：

- `high_level_digest`
- `priority_claims`
- `relevant_evidence`
- `active_conflicts`
- `related_papers`

并增加：

- salience ranking
- prompt budget
- reviewed/manual_locked/confidence/evidence_count 等排序特征

### 三、长期记忆 distillation 优化

增加一个 `distill_and_promote()` 阶段。

从 WorkingMemory 中筛选：

- 高价值 claim
- 有证据锚点的发现
- 跨论文有价值的 synthesis
- 稳定术语与风格偏好

过滤掉：

- 临时猜测
- 未核实结论
- 一次性失败信息
- 低价值重复项

Promotion 结果分为：

- `accepted`
- `review_required`
- `rejected`

## 实施顺序

### Phase 1：WorkingMemory MVP

新增：

- `modules/paper_interpreter/working_memory.py`

修改：

- `modules/paper_interpreter/agent.py`
- `modules/paper_interpreter/task_runner.py`

目标：

- interpreter 有统一短期记忆对象
- `paper_notes`、T1-T7、裁决结果能写入短期记忆
- 不改数据库 schema

### Phase 2：Interpreter Retrieval 收口

修改：

- `utils/memory.py`
- `modules/paper_interpreter/agent.py`

目标：

- 增加 `retrieve_for_interpreter()`
- retrieval 返回结构化 bundle
- 有 salience ranking 与 budget 控制

### Phase 3：Distillation + Promotion

新增：

- `modules/paper_interpreter/distillation.py`

修改：

- `modules/paper_interpreter/agent.py`
- `utils/memory.py`

目标：

- 长期记忆写回前先蒸馏
- 减少重复 / 低价值 / 证据不足的写回
- promotion 支持 accepted / review_required / rejected

## 本轮执行范围

本轮先执行 **Phase 1：WorkingMemory MVP**。

具体包括：

- 建立短期记忆对象
- 在 `PaperInterpreterAgent.run()` 中初始化并贯穿使用
- 在 `build_paper_notes()` 与 `run_all_tasks()` 中记录：
  - 共享笔记
  - task outputs
  - 关键 observation
  - 简单 open questions
  - draft claims
  - adjudication notes（先预留结构）

本轮暂不做：

- selector 侧短期记忆
- 数据库 schema 变更
- 完整 retrieval ranking 重构
- 完整 distillation 写回改造

## 验证标准

第一阶段完成后，应满足：

1. interpreter 内部存在统一短期记忆对象，而不是散乱局部变量
2. task 之间可以通过 WorkingMemory 共享关键中间状态
3. 不影响现有报告生成主链
4. 为后续 retrieval 收口和 distillation 奠定接口基础

## 当日进度更新

截至 2026-03-26 当前工作区，相关计划已经推进到超出原 Phase 1 MVP 的状态：

- 已新增 `WorkingMemory`，并贯穿 `PaperInterpreterAgent -> task_runner -> assembler -> translator`
- 已新增 interpreter / selector / review-conflict / translation-style 四类 retrieval bundle 与对应 render 方法
- 已新增 distillation 阶段，在长期记忆写回前生成 `accepted / review_required / rejected` promotion candidates
- 已把工作记忆产物落盘到 job 目录：
  - `results/jobs/{job_id}/working_memory.json`
  - `results/jobs/{job_id}/distilled_memory_summary.md`
- 已在 report summary / report detail API 中暴露记忆产物存在性与路径
- 已新增 report artifact 下载入口：
  - `/api/reports/jobs/{job_id}/artifacts/working-memory`
  - `/api/reports/jobs/{job_id}/artifacts/distilled-memory-summary`
- 已在 selector 侧把结构化 selection memory bundle 一并写入缓存产物，便于排查“为何选中这篇论文”
- 已为 OpenAI Responses 调用层补充 `invalid_params` 诊断日志与一次性重放保护，降低代理层偶发 400 对流水线的影响
- 已为 memory writeback extraction 增加 prompt budget trimming 与模型顺序路由：
  - 对 summary / review context / promotion candidates 做长度约束
  - 长 prompt 优先走 `gem_pro -> gpt_pro`
  - 在 `working_memory.metrics` 中记录 extraction prompt chars / candidate count 等诊断指标

### 真实运行验证补充

已使用 `uv` 对真实 PDF 再跑一轮 interpreter smoke：

- job: `real-interpreter-smoke-budgeted`
- 输出报告存在：`results/jobs/real-interpreter-smoke-budgeted/report.md`
- 工作记忆存在：`results/jobs/real-interpreter-smoke-budgeted/working_memory.json`
- 蒸馏摘要存在：`results/jobs/real-interpreter-smoke-budgeted/distilled_memory_summary.md`
- memory writeback extraction 在真实调用中记录为：
  - `extraction prompt=17030 chars`
  - `models=gem_pro -> gpt_pro`

说明 budget / routing 逻辑已在真实模型调用下生效，而不只是单元测试覆盖。

### Selector 真实运行验证补充

已使用 `uv` 对真实 selector 链路跑 smoke：

- profile memory 已成功注入 paper selection
- 返回结果内包含 `selector_diagnostics`
- 真实输出示例：
  - `candidate_count=40`
  - `ranked_count=5`
  - `selection_memory_bundle.high_level_digest=4`
  - `selection_memory_bundle.priority_claims=4`
  - `selection_memory_bundle.related_papers=6`
- 相关缓存产物已生成：
  - `data/cache/candidates_ranked.json`
  - `data/cache/selected_paper.json`

### Web 诊断界面补充

已完成以下可观测性增强：

1. `ReportViewer`
   - 可直接打开 `selector_diagnostics` / `working_memory` / `distilled_memory_summary`
   - 可视化 selector diagnostics 与 memory writeback diagnostics
2. `ReportsPage`
   - 顶部新增 run-level health cards
   - 历史卡片直接展示 selector 规模、writeback prompt 大小、accepted/review-required promotion 摘要
   - 支持按 `selector / working-memory / heavy-prompt / review-required` 过滤历史任务

## 当前结论

本计划原定义的三个阶段已经在当前仓库范围内执行完成：

- Phase 1：WorkingMemory MVP 已完成
- Phase 2：Interpreter Retrieval 收口已完成，并扩展到 selector / review / translation style
- Phase 3：Distillation + Promotion 已完成，并补充了 prompt budget trimming、artifact 可观测性和 Web 端诊断入口

## 对比原计划的实际落地

相对于最初只计划先做 `Phase 1: WorkingMemory MVP`，本轮实际交付已经明显超出原始范围：

### 1. 短期记忆不只是“内存对象”，而是可观测工作台

原计划里，WorkingMemory 只要求：

- interpreter 内有统一短期记忆对象
- task 之间能共享中间状态
- 不落库、不影响主链

实际落地已经扩展为：

- job 级 artifact 落盘：
  - `results/jobs/{job_id}/working_memory.json`
  - `results/jobs/{job_id}/distilled_memory_summary.md`
- report 页面直接内联展示：
  - recent observations
  - draft claims
  - open questions
  - promotion funnel
  - retrieval budget
- observation / markdown 内容支持正确渲染与长文本展开

### 2. retrieval 不只做 interpreter bundle，而是四类用途拆分

原计划里优先目标是 `retrieve_for_interpreter()`。

实际已经扩展到：

- `for_selector`
- `for_interpreter`
- `for_review_conflict`
- `for_translation_style`

并且 selector 侧也有独立的 diagnostics 与 selection memory bundle，可直接在 Web 历史页和报告页排查。

### 3. distillation 不只做 promotion，还做了受控压缩与双语产物

原计划里 distillation 主要目标是：

- accepted / review_required / rejected
- 降低重复 / 低价值写回

实际已经继续推进到：

- promotion funnel 前端可视化与筛选
- 受控 prompt budget trimming
- 写回前的 review conflict context 注入
- 中文预翻译 artifact 预生成
- 蒸馏摘要在写盘前先经过“按预算改写”，避免单纯后处理截断

### 4. 前端体验从“调试入口”升级为“用户可阅读工作台”

原计划末尾提到：

1. 以后可以在 `Reports / ReportViewer` 中直接展示 WorkingMemory 与 distilled summary
2. 以后再决定是否继续做 Web 诊断入口

这些现在都已经完成，而且超出了最初预期：

- `ReportViewer` 已经成为报告 + 短期记忆 + 蒸馏摘要 + selector diagnostics 的组合工作台
- `ProfileDetailPage` 与 `MemoryWorkspacePage` 已支持整页统一语言切换，而不是每条 memory 单独弹英文
- `ReportViewer` 默认优先展示已预生成的中文 working memory / distilled summary，并允许切到英文
- 报告页已增加直达图谱工作台入口，缩短“单篇报告 -> profile graph”链路

## 本轮新增交付（相对上一提交）

本次相对上一 Git 提交，新增或收口了以下内容：

1. 报告页工作台化
   - `ReportViewer` 内联展示 working memory / distilled summary / selector diagnostics
   - promotion funnel 支持 `all / accepted / review_required / rejected`
   - recent observations 支持 Markdown 渲染与展开
2. 记忆双语本地化闭环
   - working memory 中文 artifact 预生成
   - distilled summary 中文 artifact 预生成
   - 新增 localized artifact API
   - 报告完成时直接落盘，不再等用户点开页面热加载
3. 页面级统一语言切换
   - `ProfileDetailPage` 改为整页统一语言按钮
   - `MemoryWorkspacePage` 改为整页统一语言按钮
   - `LocalizedTextBlock` 改为受页面语言上下文控制
   - 图谱节点与详情面板跟随整页语言同步切换
4. 蒸馏策略修正
   - 不再依赖“生成后硬截断”来控制展示长度
   - 改为在写盘前通过模型按预算改写压缩
   - 同时保留完整 evidence refs 输入，避免信息先在上游丢失
5. 诊断与稳定性增强
   - selector / memory writeback / report history 的诊断摘要继续收口
   - `invalid_params` 等模型侧异常已有更明确诊断链路

## 计划状态更新

本轮计划可以正式标记为 **已完成并收工**。

后续若继续开新计划，更适合拆成独立增强主题，而不是继续挂在本文件下，例如：

1. 前端在 Reports / ReportViewer 中直接展示 WorkingMemory 与 distilled summary 入口
2. 把 selection memory bundle 进一步接到 Web 历史页或调试页
3. 视真实运行数据，再决定是否对长 prompt 做自动 budget trimming / model routing

更准确地说，新的后续方向应该是：

1. Memory Workspace 的批量编辑、批量审阅与更细粒度筛选
2. selector / interpreter / writeback 的成本与 latency 监控
3. 旧 job artifact 的离线重蒸馏与补翻译工具
4. 更强的 graph analytics（聚类、路径解释、社区视图）
