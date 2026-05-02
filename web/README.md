# Paper Agent Web

React 19 + TypeScript + Vite 前端。它不是模板页，而是 Paper Agent 的主工作台，负责运行 job、查看报告、管理 profile 和记忆工作区。

## Commands

```bash
npm install
npm run dev
npm run build
npm run lint
```

- `npm run dev`: 本地开发，默认 `http://127.0.0.1:5173`
- `npm run build`: 产出 `web/dist`，由 FastAPI 直接托管
- `npm run lint`: ESLint 检查

## Stack

- React 19
- TypeScript
- Vite 7
- Tailwind CSS v4
- React Router
- React Markdown
- @xyflow/react

## Page Map

- `/` → Dashboard
- `/run` → RunPage
- `/reports` → ReportsPage
- `/reports/job/:jobId` → ReportViewer
- `/papers` → PapersPage
- `/profiles` → ProfilesPage
- `/profiles/:profileId` → ProfileDetailPage
- `/profiles/:profileId/workspace` → MemoryWorkspacePage
- `/settings` → SettingsPage

## Key Files

```text
src/App.tsx                    路由定义
src/api/client.ts              API client 与前端类型
src/lib/jobConfig.ts           Run 页 session 持久化
src/hooks/useJobWebSocket.ts   job 状态和日志订阅
src/pages/RunPage.tsx          运行入口、实时日志、working memory 预览
src/pages/ReportsPage.tsx      历史页与 failed retry 入口
src/pages/ReportViewer.tsx     报告查看、artifact panels、report refinement
src/pages/ProfileDetailPage.tsx
src/pages/SettingsPage.tsx     浏览器本地 provider / key / model alias / 默认 run config 设置
src/pages/MemoryWorkspacePage.tsx
src/components/MemoryGraphCanvas.tsx
```

## Current UI Behavior

- Run 页会把最近一次会话持久化到浏览器 session / local state
- Run 页未选择 profile 时会进入 `Auto assign`，不再默认回落到 `default`
- 若 Topic 没填英文关键词，后端会在真正启动 job 前自动补 English search hints
- Papers 页和 Profile Detail 里的论文入口现在会按 source 类型显示 `Open PDF` 或 `Open Source`
- 若 auto selector 最终拿到的是 HTML 论文页，前端仍可从 Papers / Profile Detail 直接打开该 HTML source
- auto 模式当前 UI 要求至少一个 preferred venue，且 `date_range_days >= 30`
- Report Viewer 支持中英切换、working memory / distilled summary / selector diagnostics / report audit 面板、以及 report variant 切换
- Reports 页对 failed auto job 额外提供 `Run Again`，会把原配置带回 Run 页，但创建的是全新 job，不复用旧 fetch 文件
- ProfilesPage / ProfileDetailPage 支持删除非 `default` profile，并要求输入 `DELETE <profile_name>` 做 destructive 确认
- ProfileDetailPage 支持批量勾选当前 profile 下的文章并迁移到另一个 profile；迁移后后端会重建 source / target 两边的长期记忆
- SettingsPage 允许当前浏览器配置 OpenAI-compatible / Lite / embedding / R2 / Semantic Scholar / MinerU 等运行时凭据，并调整 `gpt_pro`、`gem_pro`、`gem_flash`、`gem_image`、`lite_model` 等模型别名
- SettingsPage 同时支持 server `.env` 解锁与浏览器本地 fallback runtime settings；server `.env` 是否可用取决于独立的 env access guard，本地敏感值保存在当前浏览器
- SettingsPage 同时暴露默认 run config；它修改的是当前浏览器的默认值，不会改写服务器上的 `config.yaml`
- SettingsPage 中的 secret 字段是 write-only：留空表示保留当前浏览器里的原值，勾选 clear 后保存才会真正删除本地值
- Run / retry / rerun / report refine / localized artifact 请求会自动携带当前浏览器的 runtime override；不同浏览器之间默认互不共享，但只有在 `ENV_RUNTIME_ACCESS_GUARD=password` 且当前浏览器未解锁 `.env` 时，后端才会把这些 override 当成实际 fallback runtime
- 如果服务器保持 `ENV_RUNTIME_ACCESS_GUARD=off`，后端会忽略 browser runtime override，统一走服务器 `.env`
- 如果服务器配置了 `ENV_RUNTIME_ACCESS_GUARD=password`，且当前浏览器未解锁 `.env`、同时也没有填 runtime settings，创建 job、retry / rerun、关键词生成、报告 refine 等需要模型的动作会直接被后端拒绝
- 如果服务器配置了 `ENV_RUNTIME_ACCESS_GUARD=password`，则可以在 SettingsPage 输入密码解锁 server `.env`；浏览器只发送派生 proof，不发送明文密码
- Memory 页面采用整页统一语言切换，而不是逐条翻译按钮
- 前端构建后，FastAPI 会直接托管 `web/dist`

## Related Backend Surfaces

- `/api/jobs/*`：create / retry / rerun / force-stop / websocket
- `/api/config`：默认 run config 读写
- `/api/config/runtime`：服务器侧 runtime settings 接口；当前浏览器本地执行链路默认不依赖它
- `/api/reports/*`：report 内容、assets、localized artifacts、report audit、report refinement
- `/api/profiles/*`：profile detail、brief、profile delete、memory rebuild、batch move、memory delete
- `/api/profiles/{id}/workspace/*`：memory workspace CRUD
