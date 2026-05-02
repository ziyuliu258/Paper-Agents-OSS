# Paper Agent Memory（记忆）创新规格

## 1. 目标

Paper Agent 当前已经具备 Memory V3（第三版记忆）：系统以 profile（研究档案）为隔离边界，把论文解读结果沉淀为 entity（实体）、claim（主张）、evidence（证据）、synthesis（综合认知）、graph（图谱）、review（审阅）、theme（主题）、gap（知识空白）、opportunity（研究机会）和 living survey（动态综述）。下一阶段的目标不是“存更多内容”，而是把这些长期记忆组织成一个**可审计、可演化、可教学的领域认知系统**。

最终希望达到的效果：用户在已有 Memory Profile（记忆研究档案）中累积阅读一定数量论文后，可以不从零读大量文献，也能快速理解当前领域的核心问题、方法谱系、证据边界、争议演化和研究机会。

核心目标：

| 目标 | 含义 |
|---|---|
| 形成认知 | agent（智能体）能把多篇论文沉淀成可复用的领域结构，而不是论文摘要堆叠 |
| 认知演化 | 新论文能强化、挑战、限定、取代或废弃旧认知 |
| 同步用户 | 用户能通过地图、综述、矩阵、机会和阅读路径快速建立领域理解 |
| 可审计 | 每个重要判断都能追溯到论文、主张、证据和审阅记录 |
| 可治理 | 错误、过期、证据薄弱和存在争议的记忆能被发现、审阅和修正 |

## 2. 当前基础与短板

### 2.1 当前真实链路

```text
PDF / HTML 论文来源
  -> paper_notes（论文事实笔记）
  -> selected paper topic audit（已选论文主题审查）
  -> auto profile assignment（自动研究档案分配）
  -> retrieve_for_interpreter（解释器检索长期记忆）
  -> T1-T7 解读 + WorkingMemory（工作记忆）
  -> report audit（报告审查）
  -> distillation（蒸馏）
  -> memory extraction（记忆抽取）
  -> MemoryManager.write_memories（长期记忆写回）
  -> claim relations / derived artifacts（主张关系 / 派生产物）
  -> ProfileDetail / MemoryWorkspace / LivingSurvey / ReportViewer
```

### 2.2 已有优势

| 优势 | 当前基础 |
|---|---|
| 结构化记忆 | 已有实体、主张、证据、综合认知、图谱、审阅和修订历史 |
| 来源追溯 | `memory_writebacks` 绑定 `profile_id + job_id + paper_id` |
| 认知派生 | 已有主题、空白、机会、简报、动态综述 |
| 关系推理 | 已有 `reinforces（强化）`、`extends（扩展）`、`contradicts（矛盾）` |
| 人工治理 | 已有 `manual_locked（人工锁定）`、review queue（审阅队列）和 revision history（修订历史） |
| 用户界面 | 已有 Profile Detail（研究档案详情）、Memory Workspace（记忆工作台）、Living Survey（动态综述）、Report Viewer（报告查看器） |

### 2.3 关键短板

| 短板 | 影响 |
|---|---|
| 缺少领域认知地图 | 用户看到许多对象，但不容易理解“领域是怎么组织的” |
| 动态综述不够可追溯 | 用户能读摘要，但不一定知道每段来自哪些证据 |
| 主张生命周期不显式 | 旧结论可能长期存在，难以看出是否已被挑战、限定或废弃 |
| 缺少证据矩阵 | 用户难以横向比较不同论文的方法、数据集、指标和结果 |
| 选择论文不够主动 | selector（论文选择器）偏相关性，缺少基于信息增益的阅读规划 |
| 用户认知没有进入系统 | 系统不知道用户已经理解、怀疑、收藏或追踪什么 |
| 交付验收需要更严格 | 容易出现 API 与前端不匹配、旧数据迁移报错、运行时 500、移动端不可用等问题 |

## 3. 核心不变量

这些约束必须长期保持，任何创新都不能破坏。

### 3.1 Pipeline（管道）不变量

| 不变量 | 要求 |
|---|---|
| `paper_notes` 优先 | `paper_notes（论文事实笔记）` 必须在长期 Memory（记忆）注入前生成 |
| 下载后主题审查 | 已选论文必须通过 post-download topic audit（下载后主题审查），才能进入记忆注入和写回 |
| selector memory 只做建议 | 选择器记忆不能绕过去重、来源可获取性、主题匹配闸门和下载后主题审查 |
| 自动档案分配分两阶段 | selector soft-route（选择器软路由）只做建议，paper_notes-based final assignment（基于论文事实笔记的最终分配）才是最终结果 |
| 默认档案不自动选中 | `default` profile（默认研究档案）只能显式指定，不能自动匹配 |
| 长期记忆不是当前证据 | T1-T7 中注入的长期记忆只作为上下文，不能替代当前论文证据 |
| 审查先于写回 | report audit（报告审查）必须在 memory writeback（记忆写回）前执行 |
| 无效主张不得写回 | 审查删除或没有证据的结构化主张不能进入长期事实层 |
| 写回必须可追溯 | 写回必须绑定 `profile_id + job_id + paper_id` |
| 人工锁定优先 | `manual_locked（人工锁定）` 对象不能被 AI 静默覆盖 |

### 3.2 数据不变量

| 不变量 | 要求 |
|---|---|
| profile 是隔离边界 | 所有记忆对象必须归属于某个 profile（研究档案） |
| writeback 是删除迁移单元 | job/paper 级删除和 profile 迁移必须按 writeback bundle（写回记忆包）处理 |
| 核心对象软删除 | 主要对象默认使用 `deleted_at` soft delete（软删除） |
| 派生不是事实源 | theme、gap、opportunity、brief、living survey 都是 derived view（派生视图） |
| 英文是事实源 | 英文是 source-of-truth（事实源文本），中文是展示和本地化缓存 |
| 删除链路纯本地 | 删除、迁移、强停、清理不能依赖 LLM（大语言模型）或翻译服务 |

### 3.3 前端不变量

| 页面 | 不变量 |
|---|---|
| Profile Detail（研究档案详情） | 只做概览、预览、来源管理和跳转，不承载原始记忆全量编辑 |
| Memory Workspace（记忆工作台） | 是完整 profile 记忆工作台，新增 profile 级能力优先挂在这里 |
| Living Survey（动态综述） | 是只读连续阅读页，不直接编辑记忆 |
| Report Viewer（报告查看器） | 只展示 job 级报告和 artifacts（产物），不直接编辑 profile 记忆 |
| Graph Canvas（图谱画布） | 只做可视化与聚焦，不做直接编辑 |
| 移动端 | 单列优先，按钮可换行，长 ID 可断行，图谱和表格必须有列表或卡片兜底 |

## 4. 创新方向

### 4.1 方向总览

| 方向 | 目标 | 优先级 | 主要交付物 |
|---|---|---:|---|
| Temporal Claim Lifecycle（时间化主张生命周期） | 表达主张如何被支持、挑战、限定、取代和废弃 | P0 | lifecycle 字段、状态规则、时间线展示 |
| Memory Health（记忆健康度） | 发现无证据、证据薄弱、争议、过期和孤立对象 | P0 | health 接口、健康卡片、问题跳转 |
| Living Survey with Provenance（带来源追溯的动态综述） | 让综述可读、可追溯、可解释变化 | P0 | 来源展开、近期变化、章节目录 |
| Writeback Validation（写回校验） | 防止无证据或审查删除的主张进入长期记忆 | P0 | 解析后校验、审查问题注入、降级策略 |
| Field Cognitive Map（领域认知地图） | 展示领域结构、主题簇、方法族、争议和入口路径 | P1 | field map 派生产物、地图 tab、详情预览 |
| Evidence Matrix（证据矩阵） | 横向比较任务、方法、数据集、指标、结果和边界 | P1 | evidence matrix 派生产物、表格/卡片视图 |
| Claim-Scope Aware Retrieval（主张适用范围感知检索） | 根据任务、数据集、指标和设置进行更精确检索 | P1 | scope 字段、scope 过滤、边界缺失识别 |
| Active Reading Curriculum（主动阅读课程） | 按信息增益推荐下一篇该读什么 | P2 | 阅读价值评分、selector 诊断、推荐阅读路径 |
| Opportunity Graph（研究机会图谱） | 把研究机会组织成可验证的假设和议程 | P2 | 机会图谱、验证计划、证伪信号 |
| User-AI Co-Cognition（用户与智能体共认知） | 将用户理解、怀疑、追踪和收藏写入协作状态 | P3 | 用户标记、学习状态、个性化阅读路径 |

### 4.2 Temporal Claim Lifecycle（时间化主张生命周期）

新增字段建议：

| 字段 | 含义 |
|---|---|
| `lifecycle_state` | 生命周期状态 |
| `lifecycle_reason_json` | 状态原因和规则依据 |
| `superseded_by_claim_id` | 被哪个新主张取代 |
| `last_lifecycle_update_at` | 最近生命周期更新时间 |

状态定义：

| 状态 | 含义 |
|---|---|
| `emerging（萌芽）` | 新出现，证据少或覆盖少 |
| `supported（被支持）` | 被多篇论文或多条证据支持 |
| `contested（争议中）` | 有矛盾关系或待审阅冲突 |
| `bounded（边界已限定）` | 适用条件被后续证据限制 |
| `superseded（被取代）` | 被更新主张或更强综合认知取代 |
| `deprecated（已废弃）` | 明显过期或被多次挑战，默认降权 |
| `needs_review（需要审阅）` | 自动规则不确定，需要人工裁决 |

落点：`utils/memory.py`、`utils/memory_claim_relations.py`、`server/schemas.py`、`MemoryWorkspacePage.tsx`、`LivingSurveyPage.tsx`。

首版边界：自动设置 `emerging（萌芽）`、`supported（被支持）`、`contested（争议中）`；`bounded（边界已限定）`、`superseded（被取代）`、`deprecated（已废弃）` 先进入 review queue（审阅队列）。

### 4.3 Memory Health（记忆健康度）

健康指标：

| 指标 | 含义 |
|---|---|
| `unsupported_claim_count（无证据主张数）` | 没有证据绑定的主张 |
| `thin_evidence_claim_count（薄弱证据主张数）` | 证据数量或质量不足 |
| `contested_claim_count（争议主张数）` | 存在矛盾或待审阅冲突 |
| `pending_review_count（待审阅数）` | 未处理审阅项 |
| `deprecated_claim_count（废弃主张数）` | 已废弃或疑似过期主张 |
| `scope_incomplete_claim_count（适用范围不完整主张数）` | 缺少关键适用范围 |
| `orphan_evidence_count（孤立证据数）` | 证据无法连接到有效主张 |
| `stale_artifact_count（过期派生产物数）` | 派生视图已过期 |

落点：新增 `get_memory_health(profile_id)` 或 `memory_health_snapshot:v1`，新增 `/workspace/health` 接口，在 Profile Detail 和 Memory Workspace 展示。

首版边界：不调用 LLM（大语言模型），只基于数据库和派生状态计算。

### 4.4 Living Survey with Provenance（带来源追溯的动态综述）

建议章节：

| 章节 | 内容 |
|---|---|
| 领域概览 | 当前 profile 覆盖多少论文、主题、主张和证据 |
| 认知地图入口 | 新人应先理解哪些主题和方法族 |
| 核心主题 | 主题、成熟度、代表主张 |
| 共识与争议 | 稳定结论和活跃矛盾 |
| 适用边界 | 哪些结论只在特定任务、数据集或指标下成立 |
| 证据矩阵亮点 | 关键结果和横向对比 |
| 开放问题与知识空白 | 尚未解决的问题 |
| 研究机会 | 可验证的下一步方向 |
| 最近认知变化 | 新增、强化、挑战、限定、废弃 |
| 推荐阅读路径 | 新人阅读顺序 |

每个重要 block（内容块）必须绑定至少一种来源：`claim_ids`、`evidence_ids`、`synthesis_ids`、`paper_ids` 或 `review_ids`。

落点：`utils/memory.py::_build_living_survey()`、`server/schemas.py`、`LivingSurveyPage.tsx`。

首版边界：Living Survey 仍是只读页面；修正内容必须跳转 Memory Workspace 的原始对象或审阅队列。

### 4.5 Field Cognitive Map（领域认知地图）

新增派生产物：`field_map_snapshot:v1`。

核心对象：

| 对象 | 含义 |
|---|---|
| `clusters（领域簇）` | 问题、方法族、基准、应用、理论等 |
| `links（簇关系）` | 依赖、竞争、扩展、迁移、矛盾、评估关系 |
| `entry_points（进入路径）` | 面向新人、实践者、研究者的阅读入口 |

构建规则：按实体锚点和主张实体重叠聚类，用论文数、主张数、证据数、关系数、近期变化估计成熟度，用矛盾和待审阅项标记争议。

落点：`utils/memory.py` 新增构建函数，`server/routers/memory_workspace.py` 新增 `/workspace/field-map`，`MemoryWorkspacePage.tsx` 新增 `field-map（领域地图）` tab。

首版边界：地图是派生视图，不直接编辑；点击领域簇只能跳转原始对象或审阅队列。

### 4.6 Evidence Matrix（证据矩阵）

扩展 `structured_signal_json（结构化信号 JSON）` 字段：

| 字段 | 含义 |
|---|---|
| `task（任务）` | 论文解决的问题或任务 |
| `method（方法）` | 使用的方法 |
| `dataset（数据集）` | 实验数据集 |
| `metric（指标）` | 评价指标 |
| `value（结果值）` | 结果数值或描述 |
| `baseline（基线）` | 对比基线 |
| `setting（设置）` | 实验设置 |
| `limitation（限制）` | 结果限制 |
| `scope_note（适用范围说明）` | 适用条件说明 |

新增派生产物：`evidence_matrix_snapshot:v1`。

落点：`utils/memory.py`、`server/schemas.py`、`MemoryWorkspacePage.tsx` 的 `knowledge（知识） -> matrix（证据矩阵）` 二级视图。

首版边界：只展示同任务、同数据集、同指标下相对可比的结果；缺少设置或适用范围时必须标记不完整。

### 4.7 Active Reading Curriculum（主动阅读课程）

候选论文阅读价值评分：

| 评分项 | 含义 |
|---|---|
| 主题相关性 | 与用户主题是否相关 |
| 相对记忆的新颖性 | 是否带来新实体、新方法或新主张 |
| 空白匹配 | 是否覆盖当前知识空白 |
| 冲突解决潜力 | 是否可能解决活跃矛盾 |
| 证据补强潜力 | 是否能补强证据薄弱主张 |
| 方法多样性 | 是否覆盖缺失方法族 |
| 新近性 | 是否反映最新趋势 |
| 来源可信度 | 发表场所、引用、来源质量 |

落点：`retrieve_for_selector()`、`reranker.py`、`selector.py`、`selector_diagnostics.json`、Report Viewer 选择器诊断面板。

首版边界：阅读价值只影响排序和解释，不能绕过主题匹配和来源可获取性闸门。

### 4.8 Opportunity Graph and Research Agenda（研究机会图谱与研究议程）

Opportunity（研究机会）增强字段：

| 字段 | 含义 |
|---|---|
| `hypothesis（假设）` | 可验证但未证实的研究假设 |
| `validation_plan（验证计划）` | 如何验证 |
| `falsification_signals（证伪信号）` | 什么结果会推翻它 |
| `required_evidence（缺失证据）` | 还需要哪些证据 |
| `risk_flags（风险标记）` | 伪相关、证据薄弱、边界不清等 |

新增事实源对象：`memory_research_tasks（研究任务）`。Opportunity 保持只读派生；用户采纳后才写入 research task（研究任务）。

首版边界：hypothesis（假设）不能自动写入 claim fact layer（主张事实层）。

### 4.9 User-AI Co-Cognition（用户与智能体共认知）

新增事实源对象：`memory_user_marks（用户标记）`。

标记类型：

| 标记 | 含义 |
|---|---|
| `understood（已理解）` | 用户已经理解 |
| `confusing（困惑）` | 用户仍然困惑 |
| `important（重要）` | 用户认为重要 |
| `skeptical（怀疑）` | 用户持怀疑态度 |
| `tracking（追踪中）` | 用户希望持续追踪 |
| `favorite（收藏）` | 用户收藏 |

首版边界：用户标记只影响学习状态和轻量偏好，不写入事实层，不作为证据。

## 5. 实施契约

### 5.1 后端契约

| 类型 | 要求 |
|---|---|
| 新增字段 | 优先使用 soft migration（软迁移），保证旧数据库可启动 |
| 新增事实源表 | 必须有 profile 隔离、时间字段、软删除、来源追溯或可解释来源 |
| 新增派生产物 | 使用 `memory_derived_artifacts`，必须有 artifact key（产物键）和 version（版本） |
| CRUD（增删改查） | 修改事实源后必须使相关派生视图 stale（过期） |
| 删除迁移 | 新对象必须定义在 job/paper/profile 删除和 profile move（迁移）时的处理方式 |
| 本地化 | 可读文本优先返回 `LocalizedText（本地化文本）` |

### 5.2 API 契约

新增只读派生接口：

```text
GET /api/profiles/{profile_id}/workspace/field-map
GET /api/profiles/{profile_id}/workspace/evidence-matrix
GET /api/profiles/{profile_id}/workspace/health
```

新增可编辑事实源接口：

```text
GET    /api/profiles/{profile_id}/workspace/research-tasks
POST   /api/profiles/{profile_id}/workspace/research-tasks
PUT    /api/profiles/{profile_id}/workspace/research-tasks/{task_id}
DELETE /api/profiles/{profile_id}/workspace/research-tasks/{task_id}

GET    /api/profiles/{profile_id}/workspace/user-marks
POST   /api/profiles/{profile_id}/workspace/user-marks
PUT    /api/profiles/{profile_id}/workspace/user-marks/{mark_id}
DELETE /api/profiles/{profile_id}/workspace/user-marks/{mark_id}
```

API 必须满足：

| 场景 | 预期 |
|---|---|
| 空 profile | 返回稳定空结构，不返回 500 |
| 非法 profile | 返回 404 或明确错误 |
| 派生产物过期 | 可重建或返回明确 stale（过期）状态 |
| 前后端类型 | 后端响应、Pydantic schema（数据模型）、`client.ts` 类型一致 |
| 错误处理 | 前端可展示错误，不白屏 |

### 5.3 前端契约

| 页面 | 新增能力落点 | 约束 |
|---|---|---|
| Profile Detail | health、field map、research tasks、survey 状态预览 | 不做复杂编辑 |
| Memory Workspace | field-map tab、matrix 视图、health 面板、research tasks | 懒加载，派生视图只读 |
| Living Survey | 目录、来源展开、近期变化、推荐阅读路径 | 只读叙事，编辑跳 Workspace |
| Report Viewer | 阅读价值诊断、写回校验、审查阻断信息 | 只展示 job 级 artifacts |

前端新增重数据必须按需加载；每个新增视图都要有 loading（加载中）、empty（空状态）、error（错误状态）和移动端布局。

### 5.4 Pipeline 契约

| 环节 | 要求 |
|---|---|
| Selector | 记忆只影响排序和解释，不做硬过滤 |
| Interpreter | 长期记忆只做上下文，不替代当前论文证据 |
| Audit | 审查结果必须进入写回前校验 |
| Writeback | 无证据主张过滤或进入审阅，不直接写入事实层 |
| Derived build | 派生失败不能阻断旧功能访问 |

## 6. 分阶段路线图

| 阶段 | 目标 | 主要内容 | 推荐验证 |
|---|---|---|---|
| Phase 0（阶段 0） | 基线保护 | 固化不变量，补足删除、迁移、写回、派生产物测试 | memory schema、delete、move 测试 |
| Phase 1（阶段 1） | 可信认知 | 生命周期、健康度、写回校验、综述来源追溯、scope 扩展 | memory 相关后端测试 + 前端构建 |
| Phase 2（阶段 2） | 用户认知同步 | 领域地图、证据矩阵、动态综述增强、详情页预览 | derived artifact 测试 + 人工页面验收 |
| Phase 3（阶段 3） | 主动阅读 | 阅读价值评分、选择器诊断、机会增强、研究任务 | selector 回归测试 |
| Phase 4（阶段 4） | 用户共认知 | 用户标记、学习状态、个性化阅读路径 | user mark CRUD + 页面验收 |
| Phase 5（阶段 5） | 科研机会图谱 | 假设生成、桥接路径、验证计划、研究任务状态流转 | opportunity/task 测试 + 人工审查 |

## 7. 交付验收与 Review（审查）流程

本节是交付前必须执行的检查流程，用于避免“后端能跑但前端不匹配”“页面能打开但运行时报 500”“旧 profile 数据迁移失败”等问题。

### 7.1 交付门禁

| 门禁 | 必须满足 |
|---|---|
| 数据层 | 旧数据库可启动，新字段/表不破坏已有 profile、job、paper 数据 |
| API 契约 | 后端 response（响应）、schema（结构）、前端类型完全一致 |
| 前端页面 | 新入口、新 tab、新面板无白屏、无无限加载、无未捕获异常 |
| 运行时 | 后端启动不报错，新增接口不返回明显 500 |
| 降级 | 派生视图构建失败不影响报告查看、原始记忆编辑、删除和迁移 |
| 来源追溯 | 新增认知结论能追溯到主张、证据、论文或综合认知 |
| 删除迁移 | 新增对象在 job/paper/profile 删除或迁移时处理明确 |
| 用户解释 | 页面能说明新指标、新状态和新机会的含义 |

### 7.2 阻断交付的问题

出现以下任一问题，不允许交付：

- 前端引用字段后端没有返回。
- 后端返回字段没有在 `server/schemas.py` 和 `client.ts` 中同步。
- 新增 tab 白屏、无限加载或只能靠刷新恢复。
- 新增派生视图失败导致 Profile Detail、Workspace 或 Report Viewer 旧功能不可用。
- 旧数据库启动时报迁移错误。
- 删除或迁移后新增对象残留，导致统计、图谱或综述异常。
- 假设或研究议程被写入主张事实层。
- 人工锁定对象被 AI 覆盖。
- 后端 500 被前端静默吞掉，用户看不到错误原因。

### 7.3 Review 流程

| 步骤 | 检查重点 |
|---|---|
| 需求范围审查 | 本次改动属于哪个阶段，是否只改当前目标，是否说明 source-of-truth 和 derived view 边界 |
| 数据结构审查 | 新字段、新表、软删除、profile 隔离、来源追溯、删除迁移处理是否完整 |
| API 契约审查 | 响应模型、实际返回、前端类型和页面读取字段是否一致 |
| 前端交互审查 | 加载、空状态、错误状态、移动端、派生项跳转原始对象是否完整 |
| Pipeline 审查 | 是否保持 paper_notes 优先、audit 先于 writeback、记忆不替代当前证据 |
| 运行时审查 | 后端启动、旧数据迁移、新接口、页面打开、派生失败降级是否正常 |

### 7.4 自动化验收命令

后端 Memory（记忆）相关建议：

```bash
conda run -n P-Agent pytest tests/test_memory_v3_schema_migration.py
conda run -n P-Agent pytest tests/test_memory_v3_claim_relations.py
conda run -n P-Agent pytest tests/test_memory_v3_opportunity_routes.py
conda run -n P-Agent pytest tests/test_profile_move.py
conda run -n P-Agent pytest tests/test_job_purge_and_memory_delete.py
```

Selector / Pipeline（选择器 / 管道）相关建议：

```bash
conda run -n P-Agent pytest tests/test_paper_selector_regressions.py
conda run -n P-Agent pytest tests/test_topic_enrichment.py
```

前端构建：

```bash
cd web
npm run build
```

运行时冒烟：

```bash
conda run -n P-Agent python run.py
```

说明：如果某次改动只涉及文档，不需要运行上述测试；如果涉及后端或前端实现，必须按影响范围选择对应验证。

## 8. 基于已有 Profile 的人工验收流程

当前 Paper-Agent 系统中已经有一些论文阅读记录和 Memory Profile（记忆研究档案）。人工验收应优先利用这些真实数据，因为它们最能暴露迁移、旧数据兼容、前后端契约和运行时问题。

### 8.1 验收数据选择

| 数据 | 选择方式 | 用途 |
|---|---|---|
| 空 profile | 新建一个临时 profile，或选择没有论文的 profile | 验证空状态 |
| 单篇论文 profile | 选择只有 1 篇论文写回的 profile | 验证初始认知 |
| 多篇论文 profile | 选择已有多篇论文和 memory 的 profile | 验证主题、关系、机会、综述、图谱 |
| 旧报告 job | 选择改动前已经完成的报告 | 验证历史 artifact 兼容 |

如果真实数据不适合破坏性验证，删除和迁移操作必须在临时 profile 或测试数据上执行。

### 8.2 快速验收路径（约 10 分钟）

| 步骤 | 路径 | 预期结果 |
|---|---|---|
| 1 | 打开 `/profiles` | profile 列表正常，无启动或加载错误 |
| 2 | 打开一个空 profile | 显示空状态，不报错 |
| 3 | 打开一个多篇论文 profile | 能看到简报、主题、空白、机会或健康度预览 |
| 4 | 进入 Memory Workspace | tab 可切换，新增视图不白屏、不无限加载 |
| 5 | 打开 Living Survey | 能读到领域概览，并能展开来源追溯 |
| 6 | 打开旧 Report Viewer | 旧报告和旧 artifacts 正常展示 |
| 7 | 点击派生项来源 | 能跳转到原始主张、证据、综合认知或审阅项 |
| 8 | 缩窄浏览器宽度 | 按钮换行，图谱或表格有列表/卡片兜底 |

快速通过标准：上述步骤没有白屏、没有 500、没有无限 loading，且用户能看懂新增视图代表什么。

### 8.3 完整验收路径（约 45-60 分钟）

#### Profile Detail（研究档案详情）

检查路径：`/profiles/{profile_id}`。

预期结果：

- 顶部 profile 名称、描述、统计正常。
- Memory Health（记忆健康度）预览显示问题数量或空状态。
- Field Map（领域地图）预览显示领域簇数量或空状态。
- Living Survey（动态综述）卡片显示生成状态、过期状态和入口。
- Linked Papers and Reports（关联论文与报告）仍能打开报告和 source（来源文件）。
- 删除 job/paper memory（任务/论文记忆）的文案仍明确说明不删除报告和 PDF/source。

#### Memory Workspace（记忆工作台）

检查路径：`/profiles/{profile_id}/workspace`。

预期结果：

- 初次进入不明显卡顿。
- `knowledge（知识）`、`field-map（领域地图）`、`themes（主题）`、`gaps（知识空白）`、`opportunities（研究机会）`、`graph（图谱）`、`timeline（时间线）`、`reviews（审阅）`、`history（历史）` 都能进入。
- 每个 tab 有加载、空状态或正常内容。
- 派生视图明确标注为派生结果，不允许直接编辑。
- 派生项可跳转到原始对象或审阅队列。
- 编辑原始对象后，相关派生视图会过期或刷新后更新。

#### Living Survey（动态综述）

检查路径：`/profiles/{profile_id}/survey`。

预期结果：

- 页面有目录。
- 领域概览、核心主题、争议、知识空白、研究机会、近期变化等章节正常展示。
- 重要内容块能展开来源追溯。
- 来源中至少能看到相关论文、主张、证据或综合认知。
- 页面不提供原始记忆编辑按钮。

#### Report Viewer（报告查看器）

检查路径：`/reports/job/{job_id}`。

预期结果：

- 原始报告正常显示。
- WorkingMemory（工作记忆）、distilled summary（蒸馏摘要）、selector diagnostics（选择器诊断）、audit（审查）面板不因新字段报错。
- 旧 job 也能打开。
- artifact 缺失时对应面板自动隐藏或提示，不白屏。

#### 删除、迁移、重建

检查动作应尽量使用临时数据：

| 动作 | 预期结果 |
|---|---|
| 删除 job memory | 当前 profile 相关主张、证据、综合认知和派生视图减少或过期；报告和 PDF 不删除 |
| 删除 paper memory | 只删除当前 profile 中该论文的记忆包，不影响其他 profile |
| 迁移 paper bundle | 源 profile 失去相关记忆，目标 profile 获得相关记忆，两边统计刷新 |
| Rebuild Cognition（重建认知） | 主题、空白、机会、健康度等派生视图更新，不改变原始报告 |

#### 自动运行新任务

检查动作：从 Run 页面运行一次 auto job（自动任务），可使用已有多篇论文 profile 或 `profile_mode=auto`。

预期结果：

- selector 能正常检索、去重、排序、筛选和选择论文。
- topic-fit gate（主题匹配闸门）仍生效。
- `paper_notes` 先生成，长期记忆后注入。
- report audit 先于 memory writeback。
- job 完成后 Report Viewer 能打开。
- 对应 profile 的 Memory Workspace 和 Living Survey 能看到新增变化或过期提示。

## 9. 用户预期结果与快速判断

### 9.1 用户应该看到的变化

| 页面 | 用户能看到什么 |
|---|---|
| Profile Detail | 不只是论文列表，还能看到健康度、地图预览、动态综述状态和研究机会概览 |
| Memory Workspace | 不只是原始对象列表，还能按知识、地图、矩阵、机会、图谱、时间线理解领域 |
| Living Survey | 像读动态综述一样理解领域，并能展开来源 |
| Report Viewer | 旧报告和新报告稳定可读，分析面板能解释选择、审查和写回情况 |

### 9.2 达到预期的信号

用户应能在不重读所有论文的情况下回答：

- 当前领域有哪些核心主题。
- 哪些主张被多篇论文支持。
- 哪些主张正在被挑战或需要审阅。
- 哪些结论只在特定数据集、任务或指标下成立。
- 最近新增论文让领域认知发生了什么变化。
- 哪些研究机会值得继续验证。
- 每个判断来自哪些论文和证据。

### 9.3 未达到预期的信号

出现以下情况说明交付仍不可靠：

- 有漂亮总结，但无法展开来源。
- 新增 tab 经常 loading 不结束。
- 派生视图和原始对象数量明显对不上。
- 删除或迁移后旧 profile 仍残留相关主张或图谱节点。
- Report Viewer 打不开旧任务。
- 空 profile 或单篇论文 profile 报错。
- 用户无法区分 fact（事实）、inference（推断）、hypothesis（假设）和 agenda（议程）。

## 10. 风险与缓解

| 风险 | 影响 | 缓解 |
|---|---|---|
| LLM 抽取污染长期记忆 | 无证据内容进入事实层 | 解析后校验、证据必需、审查问题注入抽取提示词 |
| 认知地图过度自信 | 用户误以为派生地图是客观真理 | 展示证据数、论文数、覆盖度、派生标记和来源 |
| 旧结论僵尸化 | 过期结论继续误导后续报告 | 生命周期、废弃降权、健康度提醒、审阅队列 |
| 主动阅读偏差 | 阅读推荐被旧记忆锁死 | 阅读价值只做建议，保留主题相关性、新颖性和多样性 |
| 用户标记污染事实层 | 用户偏好被误当证据 | 用户标记只影响学习状态和轻量偏好 |
| 研究机会伪创新 | 生成漂亮但无价值的假设 | 必须提供支持路径、缺失证据、验证计划、证伪信号和风险标记 |
| UI 复杂度膨胀 | 用户迷失在 tab 和卡片中 | Profile Detail 只做预览，Workspace 懒加载，Survey 叙事化 |

## 11. 非目标

当前阶段不做：

- 替换现有 Memory V3（第三版记忆）为通用 agent memory（智能体记忆）框架。
- 把 opportunity（研究机会）直接变成可编辑事实源。
- 让 LLM（大语言模型）自动覆盖 `manual_locked（人工锁定）` 对象。
- 在 graph canvas（图谱画布）内直接编辑节点和边。
- 让 hypothesis（假设）自动进入 claim fact layer（主张事实层）。
- 删除链路调用 LLM（大语言模型）或翻译。
- 为了新功能破坏 retry/rerun/delete/move/profile assignment（重试/重新运行/删除/迁移/研究档案分配）现有语义。

## 12. 外部参考

| 外部方向 | 可借鉴点 |
|---|---|
| ORKG（开放研究知识图谱） | 结构化学术知识图谱、贡献模板、来源追溯 |
| SciFact / MultiVerS（科学主张验证数据集/方法） | 主张、证据、立场验证 |
| Scite（智能引用立场分析） | 支持性和反驳性引用关系 |
| Elicit（文献综述工具） | 证据表格和系统综述工作流 |
| GraphRAG（图谱检索增强生成） | 社区摘要和全局图谱总结 |
| Graphiti（时间事实图谱） | 时间化事实图谱和事件来源追溯 |
| ASReview（主动学习综述筛选） | 主动学习式文献筛选 |
| Literature-Based Discovery（基于文献的发现） | 跨文献桥接假设 |
| MemGPT / CoALA / Reflexion / ExpeL（智能体记忆与反思方法） | 分层记忆、反思和经验写回 |

Paper Agent 的差异化定位是：面向科研阅读的 **claim-level temporal literature memory（主张级时间化文献记忆）**，不是通用聊天记忆或普通 PDF RAG（检索增强生成）。

## 13. 最终验收愿景

当用户在一个 profile（研究档案）中累计阅读一定量论文后，系统应能稳定回答：

- 这个领域的核心问题是什么？
- 主流方法分成哪些流派？
- 哪些结论已经稳定？
- 哪些结论仍有争议？
- 哪些结论只在特定数据集、任务或指标下成立？
- 最近几篇论文改变了什么认知？
- 哪些证据薄弱但影响很大？
- 新人应该按什么顺序读论文？
- 接下来最值得验证的研究机会是什么？
- 每个判断来自哪些论文、claim（主张）和 evidence（证据）？

只有当这些问题可以通过 Memory Workspace（记忆工作台）、Living Survey（动态综述）、Field Cognitive Map（领域认知地图）、Evidence Matrix（证据矩阵）和 Research Agenda（研究议程）被稳定回答时，Memory（记忆）才真正达成“帮助领域新人跳过大量前期文献阅读痛苦阶段”的目标。
