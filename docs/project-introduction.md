# Paper Agent 项目介绍与核心特色

## 1. 项目简介

Paper Agent 是一个面向学术论文自动深度解读的 Web-only 系统。用户既可以给定研究主题，让系统自动检索并选择最值得阅读的论文，也可以手动上传 PDF 直接进入解读流程。系统随后会完成论文筛选、PDF 处理、结构化解读、报告生成、长期记忆写回和报告微调。

它的目标不是“一次性生成一段摘要”，而是把论文阅读过程做成一个可重复运行、可检查中间状态、可积累长期知识的人机协作研究工作台。

## 2. 为什么它不只是“无脑调用 API”

如果只是把 PDF 直接扔给大模型，然后输出一篇总结，那更像是一个 LLM wrapper，而不是 agent。Paper Agent 的不同点在于，模型只负责高歧义决策，系统本身负责状态管理、工具调用、任务编排、记忆治理和失败恢复。

从控制闭环上看，它更接近一个受约束的 agentic system：

- Observe：从多个学术源检索候选论文，读取 PDF，抽取图表，生成共享的 `paper_notes`。
- Decide：对候选论文做去重、重排、可下载性筛查和 Top-1 选择；对结构化解读结果做双模型裁决；对长期记忆写回做蒸馏和门控。
- Act：下载 PDF、裁剪图表、执行 T1-T7 解读任务、组装 Markdown 报告、生成 refinement 变体。
- Remember：维护 job 级短期 `WorkingMemory`，以及 profile 级长期 `Memory V2`。
- Review：通过 conflict queue、`manual_locked`、review item 和 revision history 保留人工裁决入口，避免 AI 直接覆盖人工知识。

因此，更准确的说法不是“通用 autonomous agent”，而是一个面向论文阅读场景、边界清晰、状态完整、可审计的 `agentic workflow / research agent system`。

## 3. 系统结构概览

```text
Web UI
  -> JobManager
    -> PaperSelectorAgent
       -> 多源检索
       -> dedupe / rerank
       -> PDF URL enrichment
       -> AIC enrichment
       -> Top-1 selection + PDF download
    -> PaperProcessorAgent
       -> 图表识别
       -> bbox 裁剪
       -> fallback 提取
    -> PaperInterpreterAgent
       -> build_paper_notes
       -> memory injection
       -> T1-T7 task graph
       -> WorkingMemory update
       -> distillation + promotion
       -> report assembly
       -> translation + refinement variants
```

## 4. 核心设计思路

- 把确定性强、可编排的流程固化在代码里，而不是交给单个 prompt 即兴发挥。
- 把真正高歧义、需要语义判断的环节交给模型，例如选论文、结构化段落抽取、冲突裁决和记忆蒸馏。
- 为每一步保留中间工件和状态，而不是只保留最终结果。
- 让 AI 和人工共同治理长期知识，而不是让模型直接覆盖已有结论。

## 5. 核心特色

### 5.1 面向论文场景的多阶段 Agent Pipeline

- 系统不是单次调用模型，而是显式拆成 `PaperSelectorAgent`、`PaperProcessorAgent` 和 `PaperInterpreterAgent` 三个阶段。
- auto 模式下，系统会先“找论文并决定读哪篇”；manual 模式下，系统则直接接管用户上传的 PDF。
- 每个阶段都有清晰输入、输出和中间工件，适合调试、复跑和扩展。

### 5.2 Memory-aware 的论文筛选

- 选论文不是简单按关键词匹配，而是结合多源候选、去重、embedding + lexical rerank、PDF 可下载性筛查和 AIC 信息补全。
- selector 还能读取 profile 级研究记忆，优先选择能补足知识空白、推进争议裁决或补全演化链条的论文。
- 这意味着系统读“下一篇什么论文”并不是随机的，而是带有研究目标感的。

### 5.3 工具增强的 PDF 处理链路

- 系统不只读纯文本，还会先做图表识别与资产抽取。
- 图表处理链路使用 LLM 识别 figure/table，再结合 PyMuPDF 进行 bbox 裁剪，并在失败时自动 fallback 到嵌入图像提取或整页快照。
- 这使最终报告能真正引用论文中的图表，而不是只做脱离视觉证据的文本摘要。

### 5.4 共享 `paper_notes` + T1-T7 任务图

- 解释阶段不是一个超长 prompt 从头写到尾，而是先生成可复用的 `paper_notes`，再把后续任务建立在同一份共享结构化上下文之上。
- T1-T7 并不是线性死流程，而是按 Group A 和 Group B 分组执行，再由 T7 汇总。
- 这种任务图设计比“一次性大总结”更稳，也更容易定位错误来源。

### 5.5 双模型裁决与保守回退

- 对背景、方法、实验等结构化高价值段落，系统会并发请求两个模型，再做 adjudication。
- 如果双模型都成功，就进行裁决；如果部分失败，就降级保留可解析结果；如果 LLM 选择失败，还会退回到可下载候选中的最高语义分论文。
- 这不是追求“更像人”，而是工程上主动控制幻觉和脆弱性。

### 5.6 Job 级 WorkingMemory

- 系统维护 job 级短期记忆，记录 observations、open questions、draft claims、evidence refs、promotion candidates 和 metrics。
- 这层记忆让系统在一次运行内部能够持续积累“当前已经知道什么、还缺什么、哪些结论值得提升到长期记忆”。
- 它本质上是一层可序列化、可检查、可持久化的执行状态，而不是仅依赖对话上下文。

### 5.7 Profile 级 Memory V2

- 长期记忆以 profile 为隔离边界，包含 entity、claim、evidence、synthesis、graph、review 和 revision 等层次。
- 系统提供 selector、interpreter、review conflict、translation style 四类 retrieval bundle，让不同 agent 在不同任务阶段读取不同形态的记忆。
- 这使系统具备跨论文累积知识、形成研究主题脉络和维护概念图谱的能力。

### 5.8 蒸馏、提升与人工治理

- 短期记忆不会被无条件写回长期记忆，系统会先做 distillation，把候选信息分成 `accepted`、`review_required` 和 `rejected`。
- 如果新论文的结果与人工编辑过的长期记忆冲突，系统不会直接覆盖，而是进入 review queue。
- `manual_locked`、revision history 和 provenance 共同保证了长期知识库的稳定性和可追踪性。

### 5.9 可审计的中间工件

- 系统会保存 `selector_diagnostics.json`、`working_memory.json`、`distilled_memory_summary.md`、localized artifacts 和 report variants。
- 这意味着用户不仅能看到最终报告，还能看到系统是如何选论文、如何形成结论、哪些信息被保守处理、哪些信息准备写回长期记忆。
- 对 agent 系统来说，可解释性和可审计性比“单次生成效果好不好”更重要。

### 5.10 报告生成不是终点，而是可继续操作的工作流

- 最终报告由 assembler 生成，并支持中英双语产物、本地化缓存和 grounded report refinement。
- refinement 不是直接重写，而是先做计划，再结合 distilled summary、working memory excerpt 和原始报告生成新的 variant。
- 这让报告成为一个可继续编辑、可微调、可保留版本历史的研究对象。

### 5.11 面向真实运行环境的作业控制

- 系统支持 cancel、retry、rerun、force-stop、stale job recovery 和 WebSocket 实时日志。
- 每个 job 都有独立的 PDF、报告、assets、working memory 和 variants 目录，确保隔离性和可回溯性。
- 这说明项目关注的不只是“能不能生成”，而是“能不能稳定运行、失败后能不能恢复、结果能不能追踪”。

### 5.12 Web 工作台而不是脚本演示

- 项目已经具备 Dashboard、Run、Reports、Papers、Profiles 和 Memory Workspace 等完整页面。
- 用户可以运行任务、看实时日志、浏览历史报告、查看记忆图谱、审阅冲突、删除 writeback、重建 profile cognition。
- 这让它从一个实验性脚本，变成了一个可持续使用的研究工作台。

## 6. 与常见“论文总结器”的本质区别

- 常见做法是：输入 PDF，输出摘要，过程不可见，状态不可复用。
- Paper Agent 的做法是：先选论文，再处理 PDF，再做结构化解读，再维护短期和长期记忆，再生成报告和可微调变体。
- 常见做法把模型当“答案生成器”；Paper Agent 把模型放进一个受约束的执行系统里，让它成为整个研究流程中的语义决策部件。

## 7. 一句话总结

Paper Agent 不是把论文扔给模型总结一次的 API 壳，而是一个围绕“自动选论文、深度解读论文、积累研究记忆、支持人工治理”构建的有状态、可审计、可恢复的 research agent system。

## 8. 面试场景可直接使用的回答

> 如果只是上传 PDF 然后调一次模型生成总结，我不会把它叫 agent。Paper Agent 的不同点在于，它把论文阅读拆成了多个受约束的 agent 阶段：先跨多个学术源检索和筛选论文，再处理 PDF 图表，再基于共享 `paper_notes` 和记忆上下文执行 T1-T7 结构化解读，同时维护 job 级 WorkingMemory 和 profile 级 Memory V2，并在写回长期记忆前做蒸馏、冲突审查和人工软锁治理。API 只是模型入口，真正决定它像不像 agent 的，是它有没有形成 observe、decide、act、memory、review 的闭环。这个项目的重点就在这里。

## 9. 与经典 Agents 考点的逐项映射

这一节按常见面经里的 Agents 知识点，逐项对应到 Paper Agent 的现有设计，并明确哪些已经具备、哪些只是部分具备、哪些目前还没有完整实现。

### 9.1 总体判断

| 考点 | 当前状态 | 结论 |
|------|----------|------|
| 自主性 | 已有，但有边界 | 属于受约束的场景化自主执行，不是通用 autonomous agent |
| 循环执行 | 部分具备 | 有多阶段执行、失败回退、裁决和 refinement，但不是标准开放式 ReAct loop |
| Brain / Core Engine | 已有 | `JobManager + 各阶段 Agent + utils/llm` 共同构成核心执行引擎 |
| Planning Module | 已有，但偏工程化 | 主要是任务图、分组编排、双模型裁决、局部 planning，不是统一 Planner |
| Memory Module | 已有 | `WorkingMemory + Memory V2` 结构完整 |
| Tool Use Module | 已有 | 多源学术检索、PDF 解析、图像裁剪、数据库、Web 搜索、R2 上传等都属于工具层 |
| 标准 ReAct | 部分具备 | 有“观察-行动-再处理”的局部闭环，但没有统一的 `Action -> Observation` executor |
| ToT / GoT | 基本没有 | 还没有显式多分支搜索和图式规划 |
| 任务分解式规划 | 已有 | 三阶段 pipeline + T1-T7 任务图已经很接近 plan-and-execute |
| 经典向量 RAG | 部分具备 | 当前更像结构化/混合式 retrieval，不是完整向量库 chunk RAG |

### 9.2 Agents 的定义 / 特征 / 组成

#### 1. 自主性

项目是有自主性的，但属于“有边界的自主性”，不是无限开放式的自主性。

- auto 模式下，系统会自己决定去哪些学术源抓候选论文，如何去重、重排、筛掉不可下载候选，并最终选出 Top-1 论文。
- 进入解释阶段后，系统会自己构建 `paper_notes`、注入长期记忆、执行 T1-T7 任务图、蒸馏结论并决定哪些内容可以写回长期记忆。
- 报告微调时，系统也会先生成 refinement plan，再按 plan 重写报告。

结论：这个项目具备“围绕目标自动推进流程”的自主性，但自主范围被代码严格限定在论文筛选、解读和记忆治理这一垂直场景内。

如果要进一步增强：

- 引入一个更显式的 `Policy / Planner` 层，让系统根据当前证据充分度决定“是否跳过某一步、是否重试某一步、是否追加某一步”。
- 把“读取证据不足 -> 重新抽取 -> 再判断”的闭环做成统一状态机，而不是只依赖当前的静态任务图。

#### 2. 循环执行

项目目前是“部分具备循环执行”，但不是教科书式的开放式 ReAct 循环。

已有的循环性体现在：

- selector 阶段会经历候选抓取 -> 去重 -> rerank -> PDF URL enrichment -> selectable filter -> LLM 选择 / fallback。
- processor 阶段会经历图表识别 -> bbox 裁剪 -> 失败 fallback 到嵌入图提取 -> 再失败 fallback 到整页快照。
- interpreter 阶段存在双模型并发 -> adjudication -> 失败降级。
- report refinement 阶段存在 planning -> observation construction -> rewrite。
- 运行层面支持 retry、rerun、force-stop、stale job recovery。

但它还不是标准 ReAct 的原因在于：

- 没有统一的“模型输出 Action，外部执行器返回 Observation，再进入下一轮”的通用循环壳。
- 多数循环是代码预先写好的工程回路，而不是 LLM 主导的开放式迭代。

如果要进一步增强：

- 引入统一 `ActionSpec` 和 `ObservationLog`，让 LLM 可以在有限工具集合内显式决定下一步行动。
- 为每个 section 增加“证据不足自动补检索”的 bounded loop，例如最多 2 轮：抽取 -> 检查 evidence anchors -> 不足则补抽取。

#### 3. Brain / Core Engine

这个项目是有 Brain / Core Engine 的，只不过不是单一模块，而是“调度层 + 阶段 Agent + LLM 统一调用层”的组合。

- `JobManager` 负责全局作业生命周期和阶段切换。
- `PaperSelectorAgent`、`PaperProcessorAgent`、`PaperInterpreterAgent` 分别负责筛选、处理和解释。
- `utils/llm.py` 封装了统一模型调用、重试、deadline、fallback 和 tool support。

因此，项目的“Brain”不是一个巨大的 monolithic planner，而是一个受约束的 orchestration core。

如果要进一步增强：

- 单独抽象 `AgentRuntime` 或 `PolicyEngine`，把“当前状态 -> 可选动作 -> 动作选择 -> 状态更新”显式建模。
- 为关键决策保留 `decision_rationale`，提高可解释性。

### 9.3 Planning Module 映射

#### 1. LLM 内置搜索能力

这部分项目是“部分具备”。

- 主体检索并不依赖 LLM 自己搜索，而是由代码直接对接 ArXiv、Semantic Scholar、DBLP、OpenAlex、OpenReview 等源。
- 这其实是更工程化、更稳定的做法，因为检索范围和字段质量可控。
- 目前少量场景已经使用了模型工具能力，例如代码仓库验证会调用 `web_search_preview`。

结论：项目有搜索能力，但主要是 coded retrieval，不是把搜索权完全交给 LLM。

如果要进一步增强：

- 为 selector / interpreter 暴露统一的搜索工具接口，如 `search_paper`, `search_repo`, `lookup_citation`, `find_related_work`。
- 允许 planner 在有限预算内决定“是否调用工具、调用哪个工具、何时停止搜索”。

#### 2. CoT 机制

这部分项目是“隐式具备，但不是显式暴露 CoT”。

- 项目没有把模型的原始 chain-of-thought 存下来，也没有依赖公开的长思维链文本。
- 取而代之的是结构化中间状态，例如 `paper_notes`、`task_outputs`、`observations`、`open_questions`、`draft_claims`。
- 从工程角度看，这其实更好，因为它把“思维痕迹”变成了可序列化的结构化 scratch pad，而不是不可控的自然语言内心独白。

结论：有“分步思考”的效果，但不是标准面试语境下的显式 CoT 输出。

如果要进一步增强：

- 为每个关键步骤增加简短 `rationale` 字段，而不是保存完整 CoT。
- 增加 `self-critique` 或 `confidence note`，让系统对自己的结构化结论给出保守性说明。

#### 3. ReAct 策略

这部分项目是“部分具备，而且是工程化 ReAct，而不是标准格式化 ReAct”。

已有对应关系如下：

- Thought 的替代物：`paper_notes`、WorkingMemory、refinement plan、review context。
- Action 的替代物：抓候选、补 PDF、识别图表、裁剪图片、执行结构化 section extraction、生成 refinement variant、调用 web search。
- Observation 的替代物：候选列表、图表识别结果、双模型成功/失败结果、evidence refs、review bundle、tool observations。

其中最接近 ReAct 的部分是：

- 代码仓库验证：模型结合 `web_search_preview` 做验证。
- report refinement：先出 bounded plan，再结合观察块重写。
- 双模型裁决：先得到多个候选，再用 adjudication 合并。

但项目还缺少标准 ReAct 的两个核心特征：

- 没有统一 `Action:[tool_name, tool_input]` 语法。
- 没有一个通用执行器负责解析动作并把 Observation 反馈回下一轮。

如果要进一步增强：

- 建立通用 `ReActExecutor`，支持固定工具集，如 `retrieve_memory`、`find_section_evidence`、`inspect_figure`、`search_related_paper`、`rewrite_section`。
- 把每一轮 Thought/Action/Observation 摘要写入 `WorkingMemory`，保留 agent 轨迹。
- 设置 `max_steps` 和 `halt_condition`，保证循环可控。

#### 4. 其他算法模块

这部分项目是明确具备的，而且是它区别于“纯 prompt 工程”的重要原因。

项目中已经包含：

- 候选论文的 dedupe。
- embedding + lexical rerank。
- salience ranking。
- heuristic distillation。
- conflict overlap 检查。
- manual lock 与 review queue。
- 多模型优先级和超时降级。

这些都说明项目不是把所有决策都外包给 LLM，而是在 LLM 之外加入了算法约束层。

### 9.4 ReAct、CoT 与规划范式的对应关系

#### 1. CoT

- 有，但更偏“隐式 CoT + 结构化 scratch pad”。
- `paper_notes` 和 `WorkingMemory` 本质上承担了分步思考的中间状态承载功能。

#### 2. ReAct + CoT

- 部分有。
- 项目里已经存在“观察结果驱动下一步”的局部闭环，但这些闭环目前主要由代码编排，不是统一的 LLM 主导 ReAct shell。

#### 3. ToT

- 当前基本没有。
- selector 和 refinement 虽然会比较多个候选结果，但还没有显式地构造多分支思维树、逐层扩展和打分搜索。

如果要增加 ToT：

- 可以在选论文阶段生成多个候选 selection plans，例如“补空白优先”“争议裁决优先”“新方法演化优先”，分别打分后再选。
- 可以在 report refinement 阶段生成多个 rewrite plan，然后基于 groundedness、结构一致性和压缩率再做 rerank。

#### 4. GoT

- 当前基本没有。
- 虽然项目已有 `Memory Graph`，但它主要用于知识表示和可视化，而不是作为 LLM 规划过程中的动态图式推理图。

如果要增加 GoT：

- 可以把多个 section-level 假设或多篇论文的结论节点构造成 reasoning graph。
- 允许不同分支的中间结论 merge，再做冲突裁决或高层 synthesis。

#### 5. 基于任务分解的规划

这部分项目是明显具备的，而且是当前最成熟的 planning 形态。

- 三阶段 pipeline：selector -> processor -> interpreter。
- interpreter 内部进一步拆成 `build_paper_notes`、memory injection、T1-T7、distillation、assembler、translator、refiner。
- T1-T7 又分成 Group A 和 Group B 并行执行。

这本质上就是一个典型的 plan-and-execute 结构，只是计划主要由工程师预先定义，而不是运行时完全动态生成。

如果要进一步增强：

- 根据论文类型动态生成任务图，例如 benchmark-heavy 论文优先强化实验抽取，theory-heavy 论文优先强化 problem/formalism 分析。
- 增加一个 lightweight planner，决定某篇论文到底应该走哪种解释模板，而不是统一走固定 T1-T7。

### 9.5 Memory 模块映射

#### 1. 短期记忆

这部分项目是明确具备的，而且实现得比较工程化。

对应设计如下：

- `WorkingMemory` 记录 retrieval footprint、observations、open questions、draft claims、promotion candidates、terminology map 和 metrics。
- `paper_notes` 可以视为共享的任务上下文缓存。
- `working_memory.json` 会持久化保存，使短期状态可检查、可调试、可恢复。
- `distilled_memory_summary.md` 可以看成一种压缩后的短期记忆摘要。

从经典概念的映射看：

- LLM Context Window：有，shared context block 会把 `paper_notes + memory_context` 注入 prompt。
- Buffer：有，但不是 LangChain 式对话 buffer，而是 job-scoped 结构化状态。
- Scratch Pad：有，`WorkingMemory` 本质上就是结构化 scratch pad。

如果要进一步增强：

- 增加 rolling summary，在 job 过长时自动把旧 observations 压缩为更短的 summary block。
- 支持从持久化 `working_memory.json` 恢复执行，实现更强的中断续跑能力。

#### 2. 长期记忆

这部分项目也是明确具备的，而且比很多 demo 更完整。

对应设计如下：

- Profile 是长期记忆隔离边界。
- 记忆对象不是简单文本块，而是 `entity / claim / evidence / synthesis / graph / review / revision` 多层结构。
- 检索不是单一接口，而是分成 `for_selector`、`for_interpreter`、`for_review_conflict`、`for_translation_style` 四类 retrieval bundle。
- 写回有 provenance、manual lock、review queue 和 revision history。

这说明项目的长期记忆已经从“存文本”升级成“存结构化知识和治理状态”。

如果要进一步增强：

- 为 claim、evidence、synthesis 增加 embedding 字段，形成 hybrid retrieval。
- 增加时间衰减和记忆遗忘策略，避免知识库无限膨胀。
- 增加基于 citation / venue / year 的检索过滤器，让长期记忆更像 research memory，而不是普通知识缓存。

### 9.6 RAG 映射

#### 1. 当前项目是否有 RAG

有，但更准确地说是“结构化/混合式 RAG”，而不是最教科书式的“文档切块 + 向量库 + top_k”。

当前项目中的 RAG 主要体现在：

- 解释阶段先从长期记忆中检索 digest、claims、evidence、active conflicts、style preferences。
- selector 阶段也会从 profile memory 中检索与主题相关的高层知识和历史结论，用来辅助选论文。
- 检索结果会被渲染成 prompt context，再参与后续生成。

这已经符合“Retrieve -> Augment -> Generate”的核心思想。

#### 2. 当前项目的 RAG 和经典向量 RAG 的差别

当前项目更偏：

- 结构化数据库 + SQL/规则检索。
- lexical ranking。
- 少量 embedding 主要用于 selector rerank，而不是贯穿长期记忆检索。
- graph / review / provenance 等知识治理优先于纯语义相似度召回。

所以它的优势是：

- 可解释性强。
- 检索对象粒度更清晰。
- 更适合论文知识库这种强结构化场景。

它的不足是：

- 还没有完整的向量化长期记忆召回链路。
- 还没有标准的 chunk-level 文档索引和 cross-encoder rerank。
- 对“语义相近但关键词差异较大”的召回能力不如成熟的 hybrid RAG。

#### 3. 如果要把它补成更标准的 RAG

可以按下面的路线补：

- 对历史论文的 section、claim、evidence、paper_notes 摘要做 chunk 化和 embedding。
- 建立 hybrid retrieval：`BM25 / lexical + vector similarity + structured priors` 联合打分。
- 在检索后增加一个 cross-encoder reranker，对 top_k 结果进一步精排。
- 在报告中把 retrieved evidence 的来源显式标出来，形成可追溯引用链。
- 对当前正在处理的 PDF 也建立 section-level chunk index，让解释阶段能做“局部再检索”，而不是只依赖一次性 `paper_notes` 抽取。

### 9.7 这个项目最像哪一类 Agent

如果用经典 Agents 术语来定义，Paper Agent 最像下面这类系统：

- 一个面向论文阅读场景的 `bounded agentic workflow`。
- 一个 `plan-and-execute + memory-augmented + tool-using` 的 research agent system。
- 一个已经具备短期记忆、长期记忆、任务分解和工具调用，但尚未完全演化成通用 ReAct / ToT / GoT agent shell 的项目。

换句话说，它已经明显超过“LLM API 套壳总结器”，但也还没有走到“通用自主智能体框架”的终点。

### 9.8 如果要继续往更标准的 Agent 方向演进，最值得补的 4 件事

#### 1. 增加统一 ReAct 执行器

- 统一动作格式。
- 统一 observation 回写。
- 统一 step budget / halt 条件。

这样项目就会从“多处局部循环”升级成“有统一 agent runtime 的循环系统”。

#### 2. 增加 hybrid RAG

- 给长期记忆和历史论文 chunk 增加 embedding。
- 把 lexical、vector、graph、manual lock、provenance 结合起来做混合检索。

这样能显著增强跨论文语义召回能力。

#### 3. 增加动态 planner

- 根据论文类型和证据充足度动态决定任务图。
- 遇到信息缺口时自动触发 targeted re-extraction，而不是固定走完 T1-T7。

这样系统会更像真正能适应输入变化的规划器。

#### 4. 在高歧义阶段引入多分支搜索

- 例如 selector、memory writeback、report refinement 这些环节，都可以尝试 ToT 风格多方案搜索与打分。

这样能提升复杂决策的鲁棒性。

## 10. 考点到代码实现的速查映射

这一节适合在面试前快速过一遍，把抽象概念和项目里的真实落点对应起来，避免说得太虚。

| 面试考点 | 项目中的对应设计 | 关键代码位置 |
|---------|------------------|--------------|
| 自主执行 | `JobManager` 驱动完整 job 生命周期，auto 模式下自动选论文并执行全流程 | `server/job_manager.py` |
| 任务分解 | 三阶段 pipeline，解释阶段再拆成 `build_paper_notes + T1-T7 + distillation + assembler` | `modules/paper_interpreter/agent.py` `modules/paper_interpreter/task_runner.py` |
| 规划 | Group A / Group B 分组执行，结构化 section 的双模型裁决，report refinement 先 plan 再 rewrite | `modules/paper_interpreter/task_runner.py` `modules/paper_interpreter/dual_model.py` `modules/paper_interpreter/report_refiner.py` |
| 短期记忆 | `WorkingMemory` 记录 observations、questions、claims、promotion candidates、metrics | `modules/paper_interpreter/working_memory.py` |
| 长期记忆 | Memory V2，按 profile 隔离，包含 entity / claim / evidence / synthesis / review / revision | `utils/memory.py` |
| RAG / 检索增强 | selector / interpreter / review / translation 四类 retrieval bundle + prompt 注入 | `utils/memory.py` `modules/paper_interpreter/agent.py` `modules/paper_selector/agent.py` |
| 工具调用 | 学术源检索、PDF probe、图表抽取、图像裁剪、Web 搜索、R2 上传 | `modules/paper_selector/fetcher.py` `utils/pdf_sources.py` `modules/paper_processor/agent.py` `modules/paper_interpreter/assembler.py` `utils/llm.py` |
| rerank | embedding + lexical 混合重排 | `modules/paper_selector/reranker.py` `utils/embedding.py` |
| ReAct-like | 代码仓库校验、report refinement、双模型 adjudication 都有“观察后再行动”的局部闭环 | `modules/paper_interpreter/assembler.py` `modules/paper_interpreter/report_refiner.py` `modules/paper_interpreter/task_runner.py` |
| 记忆治理 | distillation、accepted/review_required/rejected、manual lock、review queue、provenance | `modules/paper_interpreter/distillation.py` `utils/memory.py` |
| 可解释性 / 可审计 | 保存 `selector_diagnostics.json`、`working_memory.json`、`distilled_memory_summary.md`、variants | `server/job_manager.py` `modules/paper_interpreter/agent.py` `modules/paper_interpreter/report_refiner.py` |
| 可恢复执行 | retry / rerun / force-stop / stale job recovery | `server/job_manager.py` `server/routers/jobs.py` |

### 10.1 最值得你记住的几条“证据链”

如果面试官不满足于概念解释，你可以直接落到这些证据链：

- “它有短期记忆”：
  `WorkingMemory` 不是一句口号，而是真实结构，里面有 observations、open questions、draft claims、promotion candidates 和 metrics。
- “它有长期记忆”：
  `utils/memory.py` 不是存几段文本，而是维护 entity、claim、evidence、synthesis、review、revision 和 provenance。
- “它有规划和编排”：
  `task_runner.py` 不是单次总结，而是先 `paper_notes`，再 Group A / Group B，再 T7 汇总。
- “它有工具调用”：
  selector 会做多源检索和 PDF URL enrichment，processor 会做图表识别和裁剪，assembler 甚至会在特定场景下调用 web search。
- “它有纠错机制”：
  结构化段落支持双模型并发和 adjudication，记忆写回前先 distill，人工编辑过的知识不会被 AI 直接覆盖。

## 11. 面试追问怎么答

这一节不是项目设计本身，而是你在面试中最容易被追问的几个点，以及更稳的回答方式。

### 11.1 “这不就是 workflow 吗，为什么叫 agent？”

建议回答：

> 它当然是 workflow，但不是普通 workflow，而是带有 agent 特征的 workflow。普通 workflow 只做预设步骤串联；我这个系统在关键节点上要做语义决策，比如选哪篇论文、哪些结论可信、哪些记忆可以提升到长期层、哪些冲突必须进入 review queue。这些不是 if-else 能完全写死的，所以我把它定义成 bounded agentic workflow，而不是纯脚本 pipeline。

这个回答的好处是：

- 不会把自己吹成“通用智能体”。
- 又能明确指出和普通 ETL / pipeline 的差异。

### 11.2 “你这个有真正的 planning 吗？”

建议回答：

> 有，但不是通用 planner，而是场景化 planning。当前主要是任务分解式规划和局部 planning：三阶段 pipeline、T1-T7 任务图、Group A/B 并行、双模型裁决、report refinement 的 plan-then-rewrite。它还没有演进成 ToT/GoT 那样的显式多分支搜索规划器，这也是我后续可以继续做的方向。

这样回答的关键是：

- 承认边界。
- 强调“已有 planning”不是零。
- 顺手引出未来演进空间。

### 11.3 “你这个 Memory 不就是 RAG 吗？”

建议回答：

> 一部分是 RAG，但不止是 RAG。RAG 解决的是检索增强问题，而我的 Memory V2 还额外处理了知识结构化、冲突治理、人工软锁、provenance 和 revision history。也就是说，它不只是‘把检索结果塞进 prompt’，而是在维护一个 profile 级研究知识库。

这个回答能把你和“单纯向量库 + top_k”区分开。

### 11.4 “为什么不直接做成标准 ReAct？”

建议回答：

> 因为论文解读是一个高成本、强约束场景，我更重视稳定性和可控性。完全开放式 ReAct 容易让 action space 失控，也更难调试。我现在采用的是工程化的局部闭环：把确定性的工具编排写死，把真正高歧义的节点交给模型做决策。等这套系统跑稳之后，再逐步引入统一的 ReAct executor，会更合理。

这个回答体现的是工程判断，而不是“我不会做”。

### 11.5 “为什么长期记忆不用纯向量数据库？”

建议回答：

> 因为我的长期记忆不是只服务于语义相似检索，还要支持 claim-evidence 关联、人工修订、review queue、manual lock、paper-to-paper graph 和 provenance 删除。纯向量库更适合做语义召回，但不适合做这类知识治理。所以我现在优先用结构化 memory schema，再在后面补 hybrid retrieval，会更符合这个场景。

### 11.6 面试中不要说的几句话

- 不要说“这就是一个完整的通用 Agent 平台”。
- 不要说“它已经实现了标准 ReAct / ToT / GoT”。
- 不要说“它和 RAG 没关系”，因为它明显有 retrieval-augmented 部分。
- 不要说“全靠模型自己决定”，因为你的项目明显有大量工程控制逻辑。

更稳的说法是：

- `bounded agentic workflow`
- `research agent system`
- `memory-augmented, tool-using, plan-and-execute pipeline`
- `面向论文解读场景的 agentic orchestration`

## 12. 如果继续演进，推荐的升级路线

这一节给的是工程上更现实的演进顺序，不是学术概念堆砌。

### Phase 1：补统一 Agent Runtime

目标：

- 把局部的“思考-行动-观察”闭环统一成一个有限状态执行器。

建议增加：

- `ActionSpec`
- `ObservationSpec`
- `StepRecord`
- `max_steps`
- `halt_reason`

收益：

- 真正把当前的局部 agent 行为提升为统一 runtime。
- 更容易做 trace、debug 和 replay。

### Phase 2：补 Hybrid RAG

目标：

- 把长期记忆检索从“结构化检索为主”升级成“结构化 + 向量 + lexical”的混合检索。

建议增加：

- claim / evidence / section chunk embedding
- vector index
- hybrid scoring
- cross-encoder rerank

收益：

- 跨论文语义召回能力会明显更强。
- 可以更好地覆盖同义表达、隐式相关和远距离语义相似。

### Phase 3：补动态 Planner

目标：

- 让系统根据论文类型、证据充足度和任务目标动态决定任务图。

建议增加：

- 论文类型分类器
- section coverage estimator
- evidence sufficiency checker
- dynamic task selection

收益：

- 让解释流程更自适应，不再是所有论文统一走固定模板。

### Phase 4：补多分支搜索

目标：

- 在少数高价值、高歧义节点上尝试 ToT 风格多方案搜索。

建议优先落在：

- selector Top-1 选择
- memory writeback 提升候选
- report refinement plan

收益：

- 提升复杂决策的鲁棒性。
- 减少单次采样带来的偶然性。

### 12.1 一个现实的结论

如果你的目标是“面试里把项目讲清楚”，那当前版本已经足够支撑你讲：

- 有 agent 特征；
- 有 planning；
- 有 memory；
- 有 tool use；
- 有 RAG；
- 但不是通用 ReAct/ToT/GoT 平台。

如果你的目标是“继续把它做成更标准的 agent system”，那最优先的不是盲目加概念，而是先补统一 runtime 和 hybrid RAG。这两件事做完，项目的 agent 味道会明显更强。
