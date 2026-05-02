# Plan: Paper Agent Web UI — FastAPI + React + shadcn/ui

> 历史规划文档。本文描述的是项目从早期命令行形态演进到 Web UI 的设计阶段，不代表当前最终状态。当前仓库已收口为 Web-only，`main.py` 已退役。

## Context

将 Paper Agent 从 CLI 工具改造为带 Web UI 的应用。用户可在浏览器中配置主题、运行 pipeline、实时查看日志、浏览生成的报告、管理记忆 Profile。

**技术栈：** FastAPI (后端) + React + TypeScript + shadcn/ui + TailwindCSS (前端) + WebSocket (实时日志)
**部署：** Monorepo，FastAPI 同时 serve API + 前端静态文件

---

## 目录结构

```text
Paper-Agents/
├── server/                          # FastAPI 后端
│   ├── __init__.py
│   ├── app.py                       # FastAPI app 入口 + 静态文件 mount
│   ├── routers/
│   │   ├── config.py                # GET/PUT /api/config
│   │   ├── jobs.py                  # POST /api/jobs, GET /api/jobs/{id}, WebSocket /api/jobs/{id}/ws
│   │   ├── profiles.py              # CRUD /api/profiles
│   │   └── reports.py               # GET /api/reports, GET /api/reports/{name}
│   ├── schemas.py                   # Pydantic request/response models
│   ├── job_manager.py               # 异步任务管理器 + 日志广播
│   └── ws.py                        # WebSocket 连接管理
├── web/                             # React 前端
│   ├── package.json
│   ├── tsconfig.json
│   ├── vite.config.ts
│   ├── tailwind.config.ts
│   ├── components.json              # shadcn/ui config
│   ├── index.html
│   ├── src/
│   │   ├── main.tsx
│   │   ├── App.tsx                  # 路由
│   │   ├── api/                     # API client (fetch wrapper)
│   │   │   └── client.ts
│   │   ├── hooks/
│   │   │   ├── useJob.ts            # WebSocket job tracking
│   │   │   └── useConfig.ts
│   │   ├── pages/
│   │   │   ├── Dashboard.tsx        # 首页概览
│   │   │   ├── SelectionPage.tsx    # 论文筛选配置 + 运行
│   │   │   ├── ReportsPage.tsx      # 报告列表
│   │   │   ├── ReportViewer.tsx     # 单篇报告渲染 (Markdown)
│   │   │   └── ProfilesPage.tsx     # 记忆 Profile 管理
│   │   └── components/
│   │       ├── Layout.tsx           # Sidebar + Header
│   │       ├── ConfigEditor.tsx     # 表单式 config 编辑
│   │       ├── JobProgress.tsx      # 实时进度 + 日志流
│   │       ├── LogStream.tsx        # 终端风格日志区
│   │       └── MarkdownRenderer.tsx # Markdown 渲染 + 图片
├── run.py                           # 启动 FastAPI server
├── modules/                         # 现有模块不改
├── utils/                           # 现有工具不改
└── requirements.txt
```

---

## 说明

下文保留原始阶段性设计内容，便于追溯演进背景；其中涉及保留 CLI、`main.py`、`pdf_path` 手动路径等描述，均属于历史状态，不应再作为当前实现依据。
