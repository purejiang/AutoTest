# FROZEN · v0.1.3 React 前端（回滚备份）

本目录是 AutoTest v0.1.3 的 React + Ant Design 前端全量快照，自 v0.2 起**冻结**：

- 不再在此目录继续开发；新功能一律改 `frontend/`（Vue 3 主前端）。
- 保留原因：v0.2 Vue 迁移出问题时可用于对照/回滚。
- 后端静态目录固定为 `frontend/dist`，所以回滚构建也需要把 React 产物放到那里：

```bash
sh scripts/dev.sh --react       # 开发模式跑本目录
sh scripts/build.sh --react     # 构建 React 并同步产物到 frontend/dist
sh scripts/start.sh --react     # 生产模式：构建（如需要）并启动后端托管
```

也可以使用环境变量 `AUTOTEST_FRONTEND=react`。发版脚本 `scripts/release.sh` 只同步
`frontend/` 的版本号，不改本目录。
