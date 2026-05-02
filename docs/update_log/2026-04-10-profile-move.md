# 2026-04-10 Profile Move

## 背景

补齐 profile 运营侧缺口：用户希望在某个 profile 下批量勾选文章，把它们迁移到另一个 profile，并在迁移时真正清掉旧 profile 的影响、重建新 profile 的影响，而不是只改一个展示字段。

## 实现点

- 新增 `POST /api/profiles/{profile_id}/move-papers`
- 前端 `ProfileDetailPage` 新增多选、目标 profile 选择和确认对话框
- 迁移粒度按 `job_id` 对应的 provenance bundle 处理
- 后端迁移时会同步处理：
  - `memory_writebacks`
  - claims / evidence 归属
  - synthesis / graph edges
  - claims 引用到的 entity 做 move / clone / relink / merge
- 若目标 profile 已有同名 entity 或相同 paper-edge，会尽量复用或合并
- 迁移完成后分别重建 source / target 两边 profile cognition
- 对应 job 会同步更新到目标 profile，并标记为手动迁移后的显式归属

## 测试命令

```bash
.venv/bin/python -m unittest tests.test_profile_move
.venv/bin/python -m unittest discover -s tests -p 'test_*.py'
cd web && npm run build
```

## 结果

- `tests.test_profile_move` 通过
- 全量 `unittest discover` 43 个测试通过
- 前端 `npm run build` 通过

## 已知风险

- 当前 revision / review 的迁移策略偏保守，重点保证活跃 bundle 和当前长期记忆状态正确；如果后续需要更精细的人工编辑 lineage 追踪，还可以继续细化
- graph edge 合并目前采用“复用已有目标 edge，尽量补充更完整 summary / weight”的策略，适合先避免重复对象

## 后续建议

- 增加 dry-run 预览，让用户在确认前看到将迁移多少 claims / entities / graph edges
- 在 Reports / Papers 页补充“Move to profile”入口，减少必须先进入 Profile Detail 的路径成本
