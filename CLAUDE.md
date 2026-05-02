# CLAUDE.md

Paper Agent 是一个 **Web-only** 的学术论文解读系统。当前主链是：选论文或上传 PDF → 优先拿真实 PDF，拿不到时走 HTML 论文页提取 → 生成 `paper_notes` → 执行 T1-T7 → 写回 Memory V2 → 组装中英文报告 → 在 Report Viewer 中继续做 grounded report refinement。

## Karpathy-Inspired Working Style

这些规则合并自 `forrestchang/andrej-karpathy-skills`，但已经按 Paper Agent 的实际工作流做了本地化。总体取向是：**非平凡任务时，宁可更谨慎、更可验证，也不要靠猜测和过度实现。**

### 1. Think Before Coding

- 先说清假设，再动手改代码。尤其是涉及 `auto/manual`、`PDF/HTML`、`profile_mode`、`browser/.env runtime`、`retry/rerun/force-stop` 这些语义时，不要默默选一种解释直接写。
- 如果同一个需求存在多种合理解释，先列出来，选最符合当前代码语义的路径；如果风险较高，先问清。
- 如果用户要求的方案明显会破坏当前不变量，直接指出并给出更简单或更安全的替代方案。
- 如果还没定位清楚真实入口文件，不要先做大改；先缩小问题范围。

### 2. Simplicity First

- 只写解决当前问题所需的最小代码，不预埋“未来也许会用到”的抽象层。
- 单次需求不要引入新的配置层、新的状态同步机制，或新的 data flow，除非现有结构真的承载不了。
- 优先扩展现有模块边界：
  - selector 问题优先落在 `modules/paper_selector/*`
  - interpreter 问题优先落在 `modules/paper_interpreter/*`
  - runtime settings 问题优先落在 `utils/config.py`、`server/app.py`、`server/routers/config.py`、`web/src/pages/SettingsPage.tsx`
- 如果实现写到 200 行但 50 行能说清，就继续收缩。

### 3. Surgical Changes

- 只改完成当前任务必须动到的文件和行。
- 不要顺手整理相邻模块、重写注释、替换命名、统一格式，除非这些改动和当前任务直接相关。
- 保持既有风格，不因为“我更喜欢另一种写法”就顺带重构。
- 只清理由本次改动引入的 orphan：
  - 新增后变成未使用的 import / helper / state 要删掉
  - 早就存在但与本任务无关的 dead code，只提示，不顺手清除
- 自检标准：**每一处 diff 都能直接追溯到用户请求。**

### 4. Goal-Driven Execution

- 把任务改写成“步骤 + 验证”而不是“感觉做完了”：
  1. 定位真实入口与约束 → verify: 相关文件链路和语义已确认
  2. 做最小改动 → verify: diff 只覆盖任务相关文件
  3. 跑最贴近的检查 → verify: 测试 / 构建 / 手工路径通过
- 修 bug 时，优先把问题转成可验证目标：
  - selector / routing / config bug：优先补或运行对应测试
  - 前端页面改动：至少跑一次 `cd web && npm run build`
  - 后端 API / pipeline 语义改动：优先跑目标化 `unittest` / smoke test
- 对“让它工作”这类模糊指令，要自己补足成功标准；对高风险改动，要把验证写出来再实施。

### 5. Repo-Specific Verification Defaults

- 改 `web/src/**` 的交互、文案、状态流或 API 调用：跑 `cd web && npm run build`
- 改 runtime settings / `.env` / browser override / headers：优先看 `tests/test_runtime_config.py`
- 改 selector、候选过滤、topic-fit、source 获取：优先看 `tests/test_paper_selector_regressions.py`、`tests/test_html_source_support.py`、`tests/test_topic_enrichment.py`
- 改 profile assignment / move / memory delete / purge：优先看 `tests/test_profile_assignment.py`、`tests/test_profile_move.py`、`tests/test_job_purge_and_memory_delete.py`
- 改 report audit / report artifact：优先看 `tests/test_report_auditor.py`、`tests/test_report_memory_artifacts.py`
- 若当前环境缺依赖跑不动检查，要在交付说明里明确写出“没能运行什么、卡在哪里”。

## Fast Start

```bash
python run.py

cd web
npm run dev
```

生产构建：

```bash
cd web
npm run build
cd ..
python run.py
```

## Current Pipeline

```text
Web UI
  -> FastAPI /api/*
  -> JobManager
     -> auto selector or manual upload
        -> precision-first fit gate
        -> source download (PDF preferred, HTML fallback)
     -> PaperProcessorAgent
        -> PDF figure extraction or HTML text extraction
     -> PaperInterpreterAgent
        -> build_paper_notes
        -> selected paper topic audit
        -> auto profile match / create
        -> WorkingMemory + memory retrieval bundles
        -> Group A (T1/T5/T6) + Group B (T2/T3/T4)
        -> T7
        -> report audit + conservative auto-repair
        -> distillation + Memory V2 writeback
        -> report.en.md + report.md
        -> localized memory artifacts
     -> Report Viewer refinement variants
```

## High-Signal File Map

- `server/job_manager.py`: create / retry / rerun / force-stop / purge
- `modules/paper_selector/*`: auto search, rerank, topic-fit gating, selectable-candidate filtering
- `utils/pdf_sources.py`: DOI / landing-page based PDF URL enrichment
- `utils/source_documents.py`: source download, redirect follow, HTML extraction
- `modules/paper_interpreter/task_runner.py`: shared context, T1-T7 orchestration
- `modules/paper_interpreter/agent.py`: WorkingMemory lifecycle, auto profile routing, Memory V2 writeback
- `modules/paper_interpreter/report_auditor.py`: groundedness / consistency audit artifact
- `modules/paper_interpreter/report_refiner.py`: report variants
- `utils/profile_assignment.py`: auto profile match / auto create heuristics
- `utils/config.py`: browser runtime override 解析、secret mask、模型别名解析
- `server/routers/config.py`: 默认 run config 与 runtime settings API
- `utils/memory.py`: Profile-scoped long-term memory and provenance
- `web/src/pages/RunPage.tsx`: run controls, retry draft, force-stop UX
- `web/src/pages/ReportViewer.tsx`: artifact panels and report refinement
- `web/src/components/DeleteProfileDialog.tsx`: destructive profile cleanup UX
- `web/src/pages/ProfileDetailPage.tsx`: profile-linked papers, batch move UX, destructive memory actions
- `web/src/pages/SettingsPage.tsx`: browser-local API keys / provider endpoints / model aliases / default run config
- `web/src/pages/MemoryWorkspacePage.tsx`: editable graph / review / timeline workspace

## Editing Invariants

1. Job artifacts are isolated under `results/jobs/{job_id}` and `data/fetch/jobs/{job_id}`.
2. Run-page edits are run-scoped overrides; they should not mutate global `config.yaml`.
3. `retry` is **in-place** on the same failed job; `rerun` / `regenerate` is a **replacement job**; `force-stop` purges the current job and its memory bundle.
4. If the topic is too weak for search (for example Chinese-only with no English keywords), the backend now auto-generates English search hints before selector / topic-fit.
5. Auto runs should prefer real PDFs, but if the fetched source is actually HTML, downstream processing must stay on the HTML path instead of pretending it is a PDF.
6. Unspecified profile now means `profile_mode=auto`, not “quietly use default”.
7. Auto routing now uses bilingual profile fingerprints plus embedding-backed matching, so Chinese profile names/descriptions can still match English papers and vice versa.
8. `default` profile cannot be deleted and is ignored by auto routing unless the user explicitly selects it.
9. Profile-level batch move is a bundle migration: moving papers between profiles must also move their writebacks / claims / synthesis / graph / entity references, then rebuild both profiles.
10. Memory is profile-scoped. English is source-of-truth; Chinese is a display layer.
11. Report structure currently supports `classic` and `pmrc`.
12. Report refinement writes file-backed variants under `results/jobs/{job_id}/variants`.
13. `config.yaml` 负责默认 run 参数；浏览器本地 API key / provider / embedding / model alias 由 `/settings` 存进 localStorage，再通过请求头传给后端；浏览器没提供 override 时，执行链路会直接失败。
14. Settings 页里的 secret 是 write-only：留空表示保留当前浏览器里的原值，显式 clear 才会清空；不同浏览器之间默认不共享这些设置。

## Docs To Keep In Sync

当 pipeline、artifact、页面、API 或运行语义变化时，同步更新：

- `README.md`
- `AGENTS.md`
- `CLAUDE.md`
- `web/README.md`
- `specs/Web工作台与记忆系统-现行规格.md`
- `specs/上下文与记忆.md`
- `docs/update_log/*.md`

## 公开仓库同步指南

本项目同时维护一个私有开发仓库和一个公开开源仓库。由于私有仓库的 git 历史中包含敏感信息（API key 等），**绝对不能**将私有仓库的 commit 历史推送到公开仓库。

### 仓库关系

| Remote | 地址 | 用途 |
|--------|------|------|
| `origin` | `git@github.com:ziyuliu258/Paper-Agents.git` | 私有开发仓库，完整历史 |
| `public` | `git@github.com:ziyuliu258/Paper-Agents-OSS.git` | 公开仓库，干净历史 |

### 本地分支结构

- `master`：日常开发分支，推送到 `origin`
- `public`：孤儿分支（与 master 无祖先关系），专门用于向公开仓库推送

### 初次设置（新开发者）

```bash
# 1. 克隆私有仓库（如果还没有的话）
git clone git@github.com:ziyuliu258/Paper-Agents.git
cd Paper-Agents

# 2. 添加公开仓库 remote
git remote add public git@github.com:ziyuliu258/Paper-Agents-OSS.git

# 3. 拉取公开仓库的 main 分支，作为本地 public 分支
git fetch public
git checkout -b public public/main

# 4. 切回 master 继续开发
git checkout master
```

### 日常同步到公开仓库

当需要将最新代码同步到公开仓库时：

```bash
# 1. 确保 master 上的改动已经提交
git checkout master
git push origin master

# 2. 切到 public 分支，用 master 的文件覆盖
git checkout public
git checkout master -- .

# 3. 提交并推送
git add -A && git commit -m "sync: 简要描述本次更新"
git push public public:main

# 4. 切回 master 继续开发
git checkout master
```

### 严禁操作

- **绝对不要** `git push public master:main`：这会把 master 的完整历史（含密钥）推到公开仓库
- **绝对不要** `git push origin public`：这会把 public 分支推到私有仓库（虽然不危险，但会造成混乱）
- **绝对不要** `git merge master` 在 public 分支上：这会把 master 历史合并进来

### 原理

`public` 分支是通过 `git checkout --orphan` 创建的，它没有任何父 commit，和 `master` 不存在祖先关系。因此即使 `public` 分支包含与 `master` 相同的文件内容，推送到公开仓库时也不会携带 `master` 的历史记录。

每次同步时，`git checkout master -- .` 只是把 master 当前的**文件快照**复制到 public 分支的工作区，不涉及任何 commit 历史的引入。
