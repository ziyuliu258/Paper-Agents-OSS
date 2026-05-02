# "每日一篇" 论文解读 Agent

> 历史规格文档。本文保留的是项目早期双轨输入与命令行设计背景，其中涉及 `main.py`、`manual_pdf_path`、模块独立 CLI 调试等内容，均不代表当前实现。当前仓库已完全收口为 Web-only。

## 2026-04 现状注记

这份文档保留的是 CLI 时代的设计背景。当前实现与其核心差异如下：

- 当前统一入口是 **FastAPI + React Web 工作台**，不再以 CLI 为主入口
- 手动模式通过 Web 上传 PDF，不再使用 `manual_pdf_path`
- selector 现在支持 **venue-first / general search**、PDF URL enrichment、AIC enrichment，以及只在 selectable candidates 中做最终精排
- interpreter 内部现已形成 **shared `paper_notes` + WorkingMemory + retrieval bundles + Group A/B T1-T7** 的正式结构
- 长期记忆现为 **Profile 隔离的 Memory V2**，支持 review queue、manual lock、按 job / paper 删除 bundle
- 报告页现已升级为包含 working memory / distilled summary / selector diagnostics / report variants 的工作台
- 报告支持 `classic / pmrc` 结构模式，并支持 grounded report refinement
- 作业控制现在区分 `retry`、`rerun / regenerate`、`force-stop + purge`

若要看当前规格，请优先参考：

- `specs/Web工作台与记忆系统-现行规格.md`
- `specs/上下文与记忆.md`
- `AGENTS.md`
- `README.md`


**版本：3.0 | 双轨输入 + 双模型交叉解读 + 模块化架构**

---

## 一、系统概述

本系统是一个模块化的学术论文自动筛选与深度解读管道。支持两种输入模式：

- **模式 A（自动筛选）：** 根据用户配置的语义主题，从 ArXiv / Semantic Scholar 自动抓取并筛选每日最值得关注的 1 篇论文。
- **模式 B（手动输入）：** 用户直接提供论文 PDF 文件路径，跳过筛选阶段，直接进入处理与解读。

最终输出：在 `results/` 文件夹中生成完整的 Markdown 解读文件（含图片资源）。

### 核心设计原则

1. **模块独立**：每个 Agent 拥有独立子文件夹，可单独调试运行
2. **底层复用**：通用工具函数统一放置于 `utils/`
3. **高内聚解读管线**：采用“全局上下文提取 -> 金字塔分层写作 -> 审校润色”的 Writer-Reviewer 架构，结合多模态看图说话，保证长文的流畅、易读与深度。
4. **渐进式管道**：各阶段通过标准数据结构串联，任一阶段可独立测试

---

## 二、环境与依赖

### 2.1 环境变量（`.env`）

| 变量名 | 用途 |
|---|---|
| `OPENAI_BASE_URL` | OpenRouter API 基地址 |
| `OPENAI_API_KEY` | OpenRouter API Key |
| `GPT_PRO` | GPT 模型名（如 `openai/gpt-5.2`） |
| `GEM_PRO` | Gemini Pro 模型名（如 `google/gemini-3.1-pro-preview`） |
| `GEM_FLASH` | Gemini Flash 模型名（如 `google/gemini-3-flash-preview`） |
| `GEM_IMAGE` | Gemini 图像模型名（如 `google/gemini-3-pro-image-preview`） |
| `MINERU_KEY` | MinerU API Key |

### 2.2 LLM 调用统一规范

所有 LLM 调用均通过 OpenRouter 的 OpenAI 兼容接口进行。`utils/llm.py` 提供统一封装：

- 支持通过模型别名（`gpt_pro` / `gem_pro` / `gem_flash`）快速切换
- 核心分析任务使用 `reasoning_effort: "high"`
- 支持并行调用两个模型并汇总结果

---

## 三、数据管线总览

```
用户输入
  ├─ 模式A: config.yaml 主题配置
  │    ↓
  │  ┌─────────────────────────────┐
  │  │  1. Paper Selector Agent    │
  │  │  (论文筛选)                  │
  │  │  ArXiv/SS检索 → 语义重排     │
  │  │  → AIC提取 → LLM精选Top1    │
  │  └──────────┬──────────────────┘
  │             ↓
  │        paper_meta.json (论文元数据 + PDF路径)
  │             ↓
  ├─ 模式B: 用户直接提供 PDF 路径
  │             ↓
  │  ┌─────────────────────────────┐
  │  │  2. Paper Processor Agent   │
  │  │  (论文处理)                  │
  │  │  MinerU API解析PDF → 结构化  │
  │  │  文本 + Gemini指定页码 →     │
  │  │  PyMuPDF提取图片             │
  │  └──────────┬──────────────────┘
  │             ↓
  │        parsed_paper.json (结构化全文 + 图片路径列表)
  │             ↓
  │  ┌─────────────────────────────┐
  │  │  3. Paper Interpreter Agent │
  │  │  (论文解读)                  │
  │  │  分章节双模型并行解读 →       │
  │  │  合并优化 → 生成Markdown     │
  │  └──────────┬──────────────────┘
  │             ↓
  │        results/YYYY-MM-DD_论文标题.md
  └─────────────────────────────────
```

### 3.1 当前实现约束补充（2026-03）

- 模式 B 的用户本地 PDF 统一放在 `data/local/`
- 模式 A 自动筛选阶段默认**不下载候选 PDF**，仅保留候选元数据与必要的轻量上下文
- 模式 A 只有最终被选中的 1 篇论文才会下载到 `data/fetch/`
- 若需要复盘筛选过程，可额外在 `data/cache/` 中缓存候选 JSON、排序结果或 AIC 文本；该目录是可选缓存层，不是最终产物目录
- 第一版实现以“先跑通双模式闭环”为优先，不强依赖远端临时 PDF 存储

---

## 四、模块详细设计

### 4.1 Paper Selector Agent（论文筛选）

> 路径：`modules/paper_selector/agent.py`
> 独立调试：`python -m modules.paper_selector.agent`

**职责：** 根据用户的 `topics` 与筛选规则，自动筛选出最值得关注的 1 篇论文，并尽量获取可直接进入下游流水线的 PDF。

#### 4.1.1 候选召回策略（按 `preferred_venues` 分流）

- **输入：** `config.yaml` 中的 `topics`、`selection` 配置
- **核心规则：**
  - 若 `preferred_venues` **为空**：走通用检索模式（General Search）
  - 若 `preferred_venues` **非空**：走 venue-first 模式（先拿指定会议/期刊的论文名单）

##### A. General Search（`preferred_venues` 为空）

- **数据源：** ArXiv API + Semantic Scholar API 双源并行检索
- **流程：**
  1. 根据 `topics.keywords` 优先检索，必要时回退到 `topics.query`
  2. 获取候选池（约 50-100 篇）
  3. 去重合并（基于 DOI / ArXiv ID / 归一化标题）

##### B. Venue-first（`preferred_venues` 非空）

- **目标：** 如果用户显式指定了会议/期刊，就先从这些 venue 的论文名单中选，再做 topic 筛选
- **推荐数据源路由：**
  - **OpenReview：** 优先覆盖国际顶会（如 ICLR、NeurIPS、ICML 等）
  - **OpenAlex：** 作为通用 works 列表与引用量主源，适用于国际顶会与国际顶刊
  - **Semantic Scholar：** 作为 metadata / citation / venue 补充源
  - **ArXiv：** 不作为 venue 名单主源，而作为 PDF 获取主源
- **流程：**
  1. 根据 `preferred_venues` 拉取对应 venue 的论文名单
  2. 获取每篇论文的基础元数据与引用量
  3. 再根据 `topics` 做相关性筛选

#### 4.1.2 双轨筛选规则（Classic / Recent）

在 topic 相关性成立的前提下，候选论文按两条轨道判断是否进入下一轮：

- **Classic（经典论文）**
  - 与 `topics` 相关
  - **不限时间**
  - `citationCount >= classic_min_citations`
  - **不强制要求**在 `preferred_venues` 中

- **Recent（新文）**
  - 与 `topics` 相关
  - **不看引用量**
  - 发表时间在最近 `date_range_days` 内
  - **且必须属于** `preferred_venues` 指定的会议/期刊（当该列表非空时）

候选论文最终允许命中以下三种状态：

- `classic`
- `recent`
- `recent+classic`

未命中任何轨道的候选不应进入最终候选池。

#### 4.1.3 语义重排（Semantic Re-ranking）

- 使用轻量级 Embedding（通过 OpenRouter 调用 `text-embedding-3-small` 或类似模型）
- 将论文标题+摘要向量化，与用户配置的长句主题计算余弦相似度
- 筛选语义最接近的 **Top 5-10** 篇

#### 4.1.4 AIC 上下文提取（轻量级）

- 对 Top 5-10 论文进行轻量级全文获取：
  - 优先从 `ar5iv.labs.arxiv.org` 获取 HTML，用 BeautifulSoup 提取 Abstract / Introduction / Conclusion
  - HTML 不可用时，下载 PDF，用 PyMuPDF 提取前 2 页 + 后 2 页文本

#### 4.1.5 LLM 精选 Top-1

- 将 5-10 篇论文的 AIC 文本一次性发送给 LLM（使用 `gem_flash` 降低成本）
- Prompt 中明确用户偏好和评价标准
- LLM 输出唯一 1 篇论文 ID + 选择理由
- 自动下载该论文的 PDF 至 `data/fetch/`

#### 4.1.6 PDF 获取策略

- 若候选已带开放 PDF，可直接使用
- 若论文存在 ArXiv 对应版本，则优先使用 ArXiv PDF 作为下游输入
- 在 venue-first 模式下，会议/期刊名单源负责“找论文”，ArXiv 负责“尽量提供稳定 PDF”

#### 4.1.7 输出数据结构

```json
{
  "paper_id": "2403.xxxxx",
  "title": "...",
  "authors": ["..."],
  "abstract": "...",
  "url": "https://arxiv.org/abs/2403.xxxxx",
  "pdf_path": "data/fetch/2403.xxxxx.pdf",
  "venue": "...",
  "date": "2026-03-01",
  "match_track": "recent|classic|recent+classic",
  "selection_reason": "..."
}
```

---

### 4.2 Paper Processor Agent（论文处理）

> 路径：`modules/paper_processor/agent.py`
> 独立调试：`python -m modules.paper_processor.agent --pdf <path_to_pdf>`

**职责：** 将论文 PDF 转换为结构化文本 + 提取关键图片。

#### 4.2.1 PDF 结构化解析（MinerU API）

- 调用 MinerU API 对 PDF 进行解析
- 获取结构化输出：标题层级、段落文本、表格、公式（LaTeX）
- 若 MinerU 不可用则降级：使用 PyMuPDF 提取纯文本 + Gemini 辅助结构化

#### 4.2.2 关键图片提取

- **步骤 1 - Gemini 识别关键图表：** 将 PDF 全文（或 MinerU 解析结果）发送给 Gemini（`gem_pro`），要求其输出：
  - 论文中所有关键图表的编号（如 Figure 1, Table 2）
  - 每个图表所在的页码
  - 图表的简要说明（用于后续解读引用）
- **步骤 2 - PyMuPDF 精准提取：** 根据 Gemini 返回的页码信息，使用 PyMuPDF 提取对应页面中的图片
  - 按 `Figure_1.png`、`Table_2.png` 等命名
  - 存储到 `results/assets/` 对应子文件夹

#### 4.2.3 输出数据结构

```json
{
  "paper_id": "2403.xxxxx",
  "title": "...",
  "sections": [
    {
      "heading": "Abstract",
      "level": 1,
      "content": "..."
    },
    {
      "heading": "1. Introduction",
      "level": 1,
      "content": "..."
    },
    {
      "heading": "2.1 Problem Formulation",
      "level": 2,
      "content": "..."
    }
  ],
  "figures": [
    {
      "id": "Figure_1",
      "page": 3,
      "image_path": "results/assets/2403.xxxxx/Figure_1.png",
      "caption": "Overall architecture of the proposed method."
    }
  ],
  "tables": [
    {
      "id": "Table_1",
      "page": 7,
      "image_path": "results/assets/2403.xxxxx/Table_1.png",
      "caption": "Comparison with state-of-the-art methods.",
      "markdown": "| Method | Acc | F1 |\n|---|---|---|"
    }
  ],
  "raw_text": "..."
}
```

---

### 4.3 Paper Interpreter Agent（论文解读）

> 路径：`modules/paper_interpreter/agent.py`
> 独立调试：`python -m modules.paper_interpreter.agent --input <parsed_paper.json>`

**职责：** 基于结构化论文内容，生成完整、详细、清晰易懂的 Markdown 解读文章。

#### 4.3.1 解读风格定位

**参考风格：** 机器之心 / PaperWeekly（半专业型）
- 面向有一定基础的 AI / CS 读者
- 兼顾深度和可读性：有公式但会解释，有术语但会铺垫
- 重视"这篇论文为什么重要"和"它到底做了什么不同的事"

#### 4.3.2 解读任务拆解（7 个子任务）

解读过程拆分为 7 个子任务，每个子任务独立调用 LLM，最后组装：

| 子任务 | 内容 | 输入 | 模型 |
|---|---|---|---|
| **T1. 一句话总结** | 用一句话概括论文核心贡献 | Abstract + Conclusion | gem_pro |
| **T2. 研究背景与动机** | 这个领域的现状是什么？现有方法有什么痛点？这篇论文要解决什么问题？ | Introduction + Related Work | 双模型并行 |
| **T3. 核心方法详解** | 论文提出了什么方法？架构是怎样的？关键创新点在哪？（配合架构图解读） | Method + Figures | 双模型并行 |
| **T4. 实验与结果分析** | 在哪些数据集/基准上测试？和哪些方法对比？结果如何？有什么值得注意的发现？ | Experiments + Tables | 双模型并行 |
| **T5. 消融实验解读** | 各组件的贡献分别是多少？哪些设计选择是关键的？ | Ablation（若有） | gem_pro |
| **T6. 局限性与未来方向** | 论文自述的限制？你认为还有哪些潜在问题？可能的改进方向？ | Conclusion + Discussion | gpt_pro |
| **T7. 总结与评价** | 整合以上所有内容，给出整体评价：创新性、实用性、完成度 | 所有 T1-T6 的输出 | gpt_pro |

#### 4.3.3 双模型并行策略（T2、T3、T4）

这三个最核心的子任务采用双模型交叉策略：

```
              ┌─── gem_pro ──→ 解读版本A ───┐
输入(章节文本) ─┤                              ├─→ gem_pro 合并优化 → 最终版本
              └─── gpt_pro ──→ 解读版本B ───┘
```

1. **并行生成：** 将相同的章节文本 + Prompt 同时发送给 `gem_pro` 和 `gpt_pro`
2. **合并优化：** 将两个版本交给其中一个模型（默认 `gem_pro`），Prompt 要求：
   - 取两个版本各自的优点
   - 补充对方遗漏的要点
   - 统一语言风格和术语翻译
   - 消除矛盾之处，以更准确的版本为准

#### 4.3.4 Markdown 组装模板

最终输出的 Markdown 结构如下：

```markdown
---
title: "{论文标题}"
date: {YYYY-MM-DD}
paper_url: "{URL}"
authors: "{作者列表}"
---

# {论文标题}

> **一句话总结：** {T1 输出}

## 论文信息
- **标题：** ...
- **作者：** ...
- **机构：** ...
- **发表：** ...
- **链接：** ...

## 一、研究背景与动机
{T2 输出，含引用图片}

## 二、核心方法详解
{T3 输出，含架构图引用和解读}

### 2.1 ...
### 2.2 ...

## 三、实验与结果分析
{T4 输出，含表格引用}

## 四、消融实验
{T5 输出}

## 五、局限性与未来方向
{T6 输出}

## 六、总结与评价
{T7 输出}

---
*本文由 Paper Agent 自动生成，解读仅供参考。*
```

---

## 五、`utils/` 底层工具模块

| 文件 | 职责 |
|---|---|
| `utils/llm.py` | LLM 统一调用封装（OpenRouter 兼容接口）、双模型并行调用、reasoning_effort 配置 |
| `utils/arxiv_api.py` | ArXiv API 检索、元数据获取、PDF 下载 |
| `utils/semantic_scholar.py` | Semantic Scholar API 检索、metadata / citation 补充 |
| `utils/openalex.py` | OpenAlex works 检索、venue 名单与引用量获取 |
| `utils/openreview.py` | OpenReview venue 论文名单获取（顶会优先） |
| `utils/embedding.py` | 文本向量化、余弦相似度计算 |
| `utils/mineru.py` | MinerU API 调用封装（PDF → 结构化文本） |
| `utils/pdf_parser.py` | PyMuPDF 封装：文本提取、图片提取、页面渲染 |
| `utils/markdown.py` | Markdown 文件生成、模板渲染、图片路径处理 |
| `utils/config.py` | 配置文件加载（config.yaml + .env） |
| `utils/logger.py` | 统一日志模块 |
| `utils/__init__.py` | 包初始化 |

---

## 六、项目目录结构

```
Paper-Agent/
├── config.yaml                          # 主题配置、筛选参数、模型偏好
├── .env                                 # API Keys 和模型名
├── main.py                              # 主入口：串联完整管道
├── specs/
│   └── "每日一篇"论文解读Agent.md        # 本规划文档
├── utils/                               # 底层工具函数
│   ├── __init__.py
│   ├── llm.py                           # LLM 统一调用
│   ├── arxiv_api.py                     # ArXiv API
│   ├── semantic_scholar.py              # Semantic Scholar API
│   ├── openalex.py                      # OpenAlex works / citation
│   ├── openreview.py                    # OpenReview venue 列表
│   ├── embedding.py                     # 向量化与相似度
│   ├── mineru.py                        # MinerU API
│   ├── pdf_parser.py                    # PyMuPDF 封装
│   ├── markdown.py                      # Markdown 生成
│   ├── config.py                        # 配置加载
│   └── logger.py                        # 日志
├── modules/                             # Agent 模块（各自独立）
│   ├── paper_selector/                  # 论文筛选 Agent
│   │   ├── __init__.py
│   │   ├── agent.py                     # 主调用入口
│   │   ├── fetcher.py                   # 检索与过滤逻辑
│   │   ├── reranker.py                  # 语义重排
│   │   └── selector.py                  # LLM 精选
│   ├── paper_processor/                 # 论文处理 Agent
│   │   ├── __init__.py
│   │   ├── agent.py                     # 主调用入口
│   │   ├── pdf_structurizer.py          # PDF 结构化（MinerU / 降级方案）
│   │   └── figure_extractor.py          # 图片识别与提取
│   └── paper_interpreter/               # 论文解读 Agent
│       ├── __init__.py
│       ├── agent.py                     # 主调用入口
│       ├── task_runner.py               # 子任务调度（T1-T7）
│       ├── dual_model.py                # 双模型并行 + 合并策略
│       └── assembler.py                 # Markdown 组装
├── data/
│   ├── local/                           # 用户手动提供的本地 PDF
│   ├── fetch/                           # 自动模式最终选中的论文 PDF
│   ├── cache/                           # 候选元数据 / AIC / 排序结果等可选缓存
│   └── parsed/                          # 中间解析结果 JSON（如后续启用）
├── results/                             # 最终输出
│   ├── assets/                          # 图片资源（按论文ID分子文件夹）
│   │   └── 2403.xxxxx/
│   │       ├── Figure_1.png
│   │       └── Table_1.png
│   └── 2026-03-01_论文标题.md            # 最终 Markdown
└── requirements.txt                     # 依赖
```

---

## 七、`config.yaml` 配置示例

```yaml
# ===== 输入模式 =====
mode: "auto"  # "auto"(模式A自动筛选) 或 "manual"(模式B手动输入)
manual_pdf_path: ""  # 模式B时填写 PDF 路径，建议位于 data/local/

# ===== 主题配置（模式A） =====
topics:
  - name: "时间序列预测"
    query: "关注时间序列预测中的非Transformer架构创新，如状态空间模型、线性模型"
    keywords: ["time series forecasting", "SSM", "Mamba", "linear model"]
  - name: "3D重建"
    query: "3D Gaussian Splatting 和 NeRF 的最新进展"
    keywords: ["3D reconstruction", "gaussian splatting", "NeRF"]

# ===== 筛选参数（模式A） =====
selection:
  candidate_pool_size: 80           # 初始候选池大小
  date_range_days: 7                # recent 轨道的时间窗口（天）
  classic_min_citations: 50         # classic 轨道最低引用量
  semantic_top_k: 8                 # 语义重排保留数量
  preferred_venues:                 # recent 轨道强约束的会议/期刊；非空时触发 venue-first
    - "NeurIPS"
    - "ICML"
    - "ICLR"
    - "CVPR"
    - "ECCV"
    - "AAAI"
    - "ACL"
  preferred_institutions: []        # 偏好机构（可选）

# ===== 模型配置 =====
models:
  fast: "gem_flash"                 # 轻量任务（筛选、分类）
  primary: "gem_pro"                # 主力模型
  secondary: "gpt_pro"             # 辅助模型（双模型交叉时使用）
  merge_model: "gem_pro"           # 合并优化使用的模型
  reasoning_effort: "high"          # 思考强度

# ===== 输出配置 =====
output:
  results_dir: "results"
  assets_dir: "results/assets"
  filename_pattern: "{date}_{title_short}"  # 输出文件名模式

# ===== 存储配置 =====
storage:
  local_dir: "data/local"
  fetch_dir: "data/fetch"
  cache_dir: "data/cache"
  keep_cache: false                 # 是否保留本轮筛选缓存；默认 false 表示每次运行前自动清空
```

---

## 八、`main.py` 主流程伪代码

```python
from utils.config import load_config
from modules.paper_selector.agent import PaperSelectorAgent
from modules.paper_processor.agent import PaperProcessorAgent
from modules.paper_interpreter.agent import PaperInterpreterAgent

def main():
    config = load_config()

    # Step 0: 确定输入来源
    if args.pdf:
      paper_meta = {
        "pdf_path": args.pdf,
        "paper_id": Path(args.pdf).stem,
        "title": "",
      }
    elif config["mode"] == "auto":
        # 模式A：自动筛选
        selector = PaperSelectorAgent(config)
        paper_meta = selector.run()
    else:
        # 模式B：手动输入 PDF
        paper_meta = {
            "pdf_path": config["manual_pdf_path"],
            "paper_id": "manual_input",
            "title": "",  # 由 Processor 从 PDF 中提取
        }

    # Step 1: 论文处理（PDF → 结构化文本 + 图片）
    processor = PaperProcessorAgent(config)
    parsed_paper = processor.run(paper_meta)

    # Step 2: 论文解读（结构化文本 → Markdown）
    interpreter = PaperInterpreterAgent(config)
    output_path = interpreter.run(parsed_paper)

    print(f"解读完成：{output_path}")

if __name__ == "__main__":
    main()
```

---

## 九、独立调试说明

每个 Agent 模块都支持独立运行和调试：

### Paper Selector Agent

```bash
# 运行完整筛选流程
python -m modules.paper_selector.agent

# 仅测试 ArXiv API 检索
python -m modules.paper_selector.fetcher --topic "time series forecasting"

# 仅测试语义重排
python -m modules.paper_selector.reranker --input data/candidates.json
```

### Paper Processor Agent

```bash
# 处理指定 PDF
python -m modules.paper_processor.agent --pdf data/local/example.pdf

# 仅测试 MinerU 解析
python -m modules.paper_processor.pdf_structurizer --pdf data/local/test.pdf

# 仅测试图片提取
python -m modules.paper_processor.figure_extractor --pdf data/local/test.pdf --pages 3,5,7
```

### Paper Interpreter Agent

```bash
# 从已解析的 JSON 生成解读
python -m modules.paper_interpreter.agent --input data/parsed/2403.xxxxx.json

# 仅运行某个子任务
python -m modules.paper_interpreter.task_runner --task T3 --input data/parsed/test.json

# 测试双模型合并
python -m modules.paper_interpreter.dual_model --input data/parsed/test.json --section method
```

---

## 十、开发优先级与实施路线

### Phase 1: 基础设施（先跑通）

1. [ ] `utils/config.py` - 配置加载
2. [ ] `utils/llm.py` - LLM 调用封装（含双模型并行）
3. [ ] `utils/logger.py` - 日志模块
4. [ ] `config.yaml` + `.env` 配置文件

### Phase 2: 论文筛选 Agent（模式A）

5. [ ] `utils/arxiv_api.py` - ArXiv API 封装
6. [ ] `utils/semantic_scholar.py` - Semantic Scholar API 封装
7. [ ] `utils/openalex.py` - venue works 列表与引用量获取
8. [ ] `utils/openreview.py` - OpenReview 顶会名单获取
9. [ ] `utils/embedding.py` - 向量化与相似度
10. [ ] `modules/paper_selector/` - 完整筛选流程（general search + venue-first）
11. [ ] AIC 提取与回退链路（`ar5iv` HTML / PDF 前后页文本）
12. [ ] **可行性测试**：验证 API 数据是否可获取、筛选质量是否达标

### Phase 3: 论文处理 Agent

10. [ ] `utils/mineru.py` - MinerU API 封装
11. [ ] `utils/pdf_parser.py` - PyMuPDF 封装
12. [ ] `modules/paper_processor/` - 完整处理流程

### Phase 4: 论文解读 Agent

13. [ ] `modules/paper_interpreter/task_runner.py` - 7 个子任务
14. [ ] `modules/paper_interpreter/dual_model.py` - 双模型策略
15. [ ] `modules/paper_interpreter/assembler.py` - Markdown 组装
16. [ ] `utils/markdown.py` - Markdown 工具

### Phase 5: 整合与调优

17. [ ] `main.py` - 完整管道串联（保留 `--pdf` 手动覆盖）
18. [ ] 存储策略验证（`data/local/` / `data/fetch/` / `data/cache/`）
19. [ ] 端到端测试
20. [ ] Prompt 调优与输出质量迭代

---

## 十一、实现范围说明（第一版）

- 第一版按 spec 实现 `General Search + Venue-first` 双入口策略
- 第一版引入 `config.yaml`，不采用“先命令行、后补配置文件”的方案
- 第一版默认使用本地存储闭环：`data/local/` 保存手动 PDF，`data/fetch/` 保存自动模式最终选中的 PDF
- 第一版不要求引入远端临时 PDF 存储；若后续本地空间压力明显，再单独设计远端缓存与清理策略
- 第一版优先保证 selector 输出的 `paper_meta` 与现有 `PaperProcessorAgent` / `PaperInterpreterAgent` 兼容，减少对下游已完成模块的扰动
- 第一版只承诺稳定支持**国际有名顶会与国际顶刊**，暂不承诺中文顶刊的稳定 venue-first 覆盖
- 第一版中 `preferred_venues` 的语义修正为：
  - 在 recent 轨道中作为强约束
  - 非空时触发 venue-first 候选召回
  - 不再被理解为对所有候选统一硬过滤
