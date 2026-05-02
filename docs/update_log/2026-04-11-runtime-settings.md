# 2026-04-11 Runtime Settings

## 背景

部署到 VPS 后，如果所有请求都继续吃部署者写死在 `.env` 里的 key，就会有两个明显问题：

- 普通用户无法自己切换 provider / model / embedding backend
- 部署者自己的 API key 很容易被“顺手共用”

因此本轮最终又往前收了一步：**执行链路不再回落到 `.env`**。用户自己的 key / provider / model alias 保存在浏览器本地，再随请求透传给后端；浏览器没提供就直接拒绝运行。

## 主要实现点

### 1. 去掉 `.env` 执行兜底

- 新增 `data/runtime_config.yaml`
- `utils/config.py` 新增：
  - `load_runtime_config()`
  - `load_runtime_config_view()`
  - `save_runtime_config()`
  - runtime-aware `resolve_model()`
  - runtime-aware `get_embedding_model()`
- `DEFAULT_RUNTIME_CONFIG` 改成空白默认值，不再从 `.env` 预填 provider / key / model alias
- `load_runtime_config()` 改成优先只读取浏览器透传的 runtime override；没有 override 时返回空白运行时配置

### 2. 浏览器本地设置与 secret 语义

- Settings 页默认不再把用户输入的 key 写进服务器共享配置
- 浏览器会把 runtime settings 保存到 localStorage
- 这些本地设置会在创建 job、retry / rerun、topic keyword 生成、report refine、localized artifact 请求时自动通过请求头带给后端
- 后端请求上下文与后台 task 会继承这份 override，因此异步 job 能继续使用发起浏览器的配置

- 浏览器里的 secret 在 UI 中仍然只显示遮罩态
- 前端留空输入时不会覆盖当前浏览器已保存的 key
- 只有显式 clear 才会真正清空本地值

### 3. 动态 provider / model 生效

- `utils/llm.py` 改成按 runtime config 动态创建 OpenAI-compatible、Lite 和 R2 客户端
- `utils/embedding.py` 改成动态读取 embedding base URL / API key / model
- `utils/semantic_scholar.py` 改成动态读取 Semantic Scholar key
- 模型别名 `gpt_pro` / `gem_pro` / `gem_flash` / `gem_image` / `lite_model` 现在都可以在设置页中重定向
- `utils/config.py` 新增 runtime override context；浏览器请求头带来的 override 会覆盖服务器默认值

### 4. Web Settings 页面

- 新增 `/settings`
- 页面支持填写：
  - OpenAI-compatible base URL / API key
  - Lite base URL / API key
  - Embedding base URL / API key / model
  - Semantic Scholar / MinerU key
  - R2 endpoint / bucket / access keys / public URL
  - 本地代理端口
  - 模型别名
  - 默认 run config
- 保存 runtime settings 与保存默认 run config 分开处理，避免误改
- “清空浏览器设置”会移除当前浏览器保存的本地 runtime settings 和默认 run config

## 测试命令

本次严格只使用仓库内 `.venv`，未安装、升级或卸载任何系统级包。

```bash
.venv/bin/python -m unittest tests.test_runtime_config
.venv/bin/python -m unittest discover -s tests -p 'test_*.py'
cd web && npm run build
```

## 结果

- `tests.test_runtime_config` 通过
- 全量 `unittest discover` 通过
- 前端 `npm run build` 通过

## 已知风险

- 当前隔离粒度是“浏览器”，不是“账号”；同一台机器上共享同一浏览器配置的人仍然会共用这些值
- 现阶段暴露的是 provider 与模型别名层；更细粒度的“每个子任务单独选模型”还没有做成独立配置面板
- 浏览器本地 key 会落在 localStorage，适合轻量隔离，但不等于真正的安全密钥托管

## 后续建议

- 给 `/settings` 增加更细的字段校验和连通性测试
- 为不同 provider 增加“测试连接”按钮，减少保存后才发现 endpoint/key 不可用的情况
- 如果未来做多用户部署，补上用户级 key 存储、权限边界和审计日志
