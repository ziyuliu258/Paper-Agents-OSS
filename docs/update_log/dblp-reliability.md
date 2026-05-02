# DBLP 抓取稳定性问题分析与优化

## 背景

最近在本地图形化网页端测试论文检索时，日志中会出现较多与 DBLP 相关的 warning，看起来像是“网络不好使”或“DBLP 经常拿不到数据”。

经排查，这类 warning 主要出现在 **Web UI 触发自动选论文 / venue-first 抓取流程** 时，而不是 `/papers` 页面本身的搜索逻辑。

## 现象

- 在网页端触发任务时，日志会出现多条 DBLP warning。
- warning 常见于某些 venue/year 抓取阶段。
- 最终任务不一定失败；有时仍然能拿到候选论文，只是日志噪声较大。

## 调用链说明

真正触发外部抓取的路径是：

```text
Web UI 创建 job
  -> server/routers/jobs.py
  -> server/job_manager.py
  -> modules/paper_selector/fetcher.py
  -> utils/dblp.py / utils/openreview.py / utils/openalex.py
```

而网页里的论文搜索页 `/papers`：

```text
web/src/pages/PapersPage.tsx
  -> GET /papers
  -> server/routers/jobs.py:list_papers
  -> server/database.py:search_papers / list_papers
```

这一路径只查本地 SQLite，不会实时请求 DBLP。

## 已确认的根因

### 1. DBLP 请求重复创建 `httpx.AsyncClient`

`utils/dblp.py` 原实现中，每次分页请求、每次 retry 都会重新创建一个 `httpx.AsyncClient`。这会带来：

- 连接无法在同一次抓取中复用
- TLS / TCP 建连开销被放大
- 在并发抓取时更容易遇到瞬时网络抖动
- 一旦某次请求失败，日志容易快速累积 warning

### 2. venue-first 模式会放大外部源并发压力

`modules/paper_selector/fetcher.py` 会对每个 venue/year 同时调度：

- OpenReview
- OpenAlex
- DBLP

并统一通过 `asyncio.gather(..., return_exceptions=True)` 收集结果。

这意味着当网页触发一次任务时，后端可能瞬间并发发出多组外部请求。DBLP 本身只要出现短暂超时或连接异常，就会在日志中表现为大量 warning。

### 3. 原有 DBLP 错误处理对瞬时异常不够温和

原逻辑中：

- 5xx 会 retry
- 其他异常通常直接 warning
- 某一页失败后会提前结束该 venue/year 的 DBLP 抓取

结果就是：

- 偶发网络抖动也会被直接打成 warning
- 局部失败会让整段抓取提前结束
- 用户感知上会觉得 DBLP “经常不好使”

## 优化目标

这次修改的目标是：

1. 降低 DBLP 抓取时的连接抖动
2. 提高瞬时网络异常下的恢复能力
3. 减少无意义的 warning 噪声
4. 保持现有对外接口和候选论文结构不变
5. 不重构整个抓取系统，只做聚焦优化

## 计划中的改动

### A. 在单次 DBLP 抓取中复用一个共享 HTTP client

在 `utils/dblp.py` 中，让一次 `fetch_dblp_papers()` 调用内部复用同一个 `httpx.AsyncClient`，覆盖：

- 分页请求
- 重试请求

这样可以减少重复建连带来的不稳定性。

### B. 改善 retry / backoff / 错误分类

对 DBLP 请求进行更细粒度的处理：

- 可重试：连接错误、超时、5xx（必要时含 429）
- 不可重试：大多数 4xx、明显非瞬时错误

并使用有限次、轻量的 exponential backoff。

### C. 优化日志粒度

让日志更适合排查：

- 重试中的失败记录 attempt、offset、错误类型
- 最终失败再输出 warning
- 成功保留 summary log

这样可以避免日志中充满“看起来像大故障”的噪声。

### D. 给 venue-first 下的 DBLP 增加轻量并发保护

在 `modules/paper_selector/fetcher.py` 中，仅为 DBLP 路径增加一个小型并发上限，避免多 venue/year 同时打 DBLP 时把瞬时异常放大。

注意：

- 不会把所有来源串行化
- 不改 OpenReview / OpenAlex 的现有行为
- 不改变返回结构

## 非目标

这次不会做：

- 不会重写整个抓取架构
- 不会引入全局复杂网络调度器
- 不会改动 `/papers` 页面本地搜索逻辑
- 不会改动上游 `fetch_candidates()` 的输出格式

## 验证方式

### 1. 直接验证 DBLP 基础抓取

使用 `uv run python` 直接调用：

```python
await fetch_dblp_papers('CVPR', 2025, max_results=20)
```

确认：

- 能正常返回结果
- 结果结构不变
- 日志输出合理

### 2. 验证 venue-first 路径

触发一次带 `preferred_venues` 的任务，确认：

- 候选论文仍能成功抓取
- DBLP 日志更清晰
- warning 数量下降或至少更可解释

### 3. 回归验证

确认：

- `/papers` 页面仍只查本地 SQLite
- OpenReview / OpenAlex 路径未被不必要地串行化
- 上游筛选逻辑与最终候选结构保持不变
