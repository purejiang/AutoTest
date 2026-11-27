# AutoTest 架构 / UI / 模块化评审（v0.2.6）

> 评审方式：静态阅读 + 目录/依赖/样式统计 + lint/typecheck/测试校验；未做浏览器像素级对比。
> 结论用于指导后续重构，不改变产品功能与对外契约。

## 1. 结论摘要

- **后端**：契约驱动、分层清楚、测试密度高（pytest 670 用例、ruff 全绿、模块间无循环依赖），是项目最稳的部分。
- **前端**：目录按 feature 划分、API/错误信封/查询键集中、主题 token 完整、lint/typecheck/build 全绿；
  主要债务集中在三类：**全局样式沉淀**、**共享层偏薄**、**5 个巨型组件**。
- **通用化**：设置页（后端 SETTINGS_SCHEMA 驱动）、状态标签、变量输入、API 客户端已经通用化；
  页头、Tab、空态、WebSocket、本地持久化仍是各写一套。
- **内聚/耦合**：feature 依赖图清晰且无环；`lib/` 混入 feature 私有域逻辑，`console ↔ mirror ↔ devices`
  边界靠约定维持；后端 API 层直接编排 runner/recorder/capture。
- **不需要推倒重来**：按「低风险清理 → 共享层 → 拆分 → 契约防漂移」四批渐进即可。

## 2. 基线（评审时）

| 项 | 数值 |
|---|---|
| 前端 | Vue 3.5 + Vite + TS + Element Plus + TanStack Query + Pinia + CodeMirror；89 个 `.ts/.vue`，约 28.5k 行 |
| 前端样式 | 全局 `styles/index.css` 3,143 行 + 组件 scoped 3,823 行 |
| 后端 | FastAPI + SQLAlchemy + Pydantic；54 个 `.py`，约 20.4k 行，46 个模块，无循环依赖 |
| 测试 | 后端 pytest 667（P0 后 670）；前端 0 单测 |
| 门禁 | 后端 ruff + pytest + import smoke；前端 lint + typecheck + build（CI 无前端测试） |
| 双前端 | `frontend-react/` 为 v0.1.3 冻结备份（58 文件 / 12.4k 行），不参与构建 |

## 3. 做得好的地方（保留）

1. `docs/contracts.md` 单一契约 + `api_schemas.py` 请求模型 + `errors.py` 错误信封；前端 `client.ts`
   统一解析错误并去重提示。
2. 设置页由后端 `SETTINGS_SCHEMA`（type/title/desc/min/max/options/layer/env）驱动，
   `SettingField.vue` 一处渲染所有类型与来源（default/file/env），启动项只读——全项目最好的通用化范例。
3. `StatusTag` + `lib/format.ts` 的 `Tone/StatusMeta` 在 20+ 文件复用；`VariableField/VariablePickerButton`
   与 `KeyValueEditor` 在脚本编辑器内复用。
4. 镜像域已拆出 `useMirrorSession/usePointerGestures/useKeyboardInput/render/*`，协议、手势、渲染分离。
5. 主题 174 个 token、深浅双主题、`index.html` 提前注入防闪白；Element Plus 作为控件层被统一收紧。
6. 后端 `deps.py`、`device_registry.py`、`FakeDriver`、`RunEventBus` 边界清楚；每个模块都有测试。

## 4. 主要问题与证据

### 4.1 样式层

- `index.css` 含约 1,500 行按页面分区的样式（devices/scripts/runs/settings 等），与「页面样式在组件
  scoped style 维护」的目标冲突；媒体查询里还在维护旧设备页三栏布局。
- 清理前有 47 个全局类无任何模板/脚本引用（`.device-layout`、`.control-card`、`.mirror-panel`、
  `.device-chip`、`.settings-tabs`、`.rec-player`、`.run-tabs` 等），属于 v0.1/v0.2 旧页面残留。
- 字号 14 档（9/9.5/10/10.5/11/11.5/12/12.5/13/14/17/18/28/64），6 档 < 12px；圆角 12 档。
- 深色主题依赖 `html.dark .xxx` 补丁兜底（旧页面写死浅色）；`--muted-2`（#94a3b8）在白底对比度
  **2.56:1**，却用于 10px 的 `.card-hint`，不满足 WCAG AA。
- 浅色主题下移动端 `theme-color` 曾是固定深色 `#0f151d`。

### 4.2 通用化 / 组件化

- `PageHead` 仅 2 处使用（脚本编辑器、录制详情）；其他页头手写。
- Tab 5 套实现：`dock-tabs`（控制台）、`seg`（脚本页）、`settings-tab`（设置页）、`manual-tab`
  （手动面板）、`tab-btn`（运行详情）；`el-tabs` 使用 0 次。
- 空态 10+ 套 class，与 16 处 `el-empty` 混用。
- `useRunEventsWs` 与 `useRecordingEventsWs` 相似度 85.8%，`lib/mirrorWs.ts` 另有一套重连；
  重连/心跳/去重/终态停止三处维护。
- `localStorage` 在三处各写 try/catch；`NAV_ITEMS` 导出后无人使用，布局内联重复 nav（已修）；
  `MirrorStage` 声明 `hierarchy/logcat` emit 却从不触发（已修）。
- 5 个巨型组件：`MirrorStage` 1402、`ScreenInspector` 1352、`SettingsPage` 1364、`ManualPanel` 755 行。
- `lib/` 混入 feature 私有逻辑：`h264/webcodecs/mirrorWs` 只被 mirror 用，`hierarchy` 只被
  ScreenInspector 用，`condition` 只被 scripts 用；`format.ts` 混合通用格式化与 run/step/event 语义。

### 4.3 契约 / 后端

- `types/api.ts` 532 行纯手写，无生成/校验；后端响应仍是 `dict`，OpenAPI 没有响应 schema。
- 前端硬编码 `SCRCPY_SERVER_VERSION/JAR`，后端已有 `/api/settings/scrcpy/options` 却无人调用。
- 三份 HTTP→错误码表（`main.py`、`client.ts`、契约 §1）；`contracts.md §0` 仍写 React 文件名（已修）。
- `api/devices.py` 1091 行 / 22 端点，`apply_stream_touch` 174 行触摸流状态机在 HTTP 层，
  且直接 import capture/recorder/runner。
- `mirror.py` 1133 行（`MirrorSession` 730 行）、`scrcpy.py` 1914 行、`recorder/session.py` 1240 行。
- 路由前缀不一致：mirror/recorder/reports 用绝对路径，其余用 `prefix`（已统一）；
  `main.py` 动态发现吞 `ImportError/SyntaxError` 仅告警且无路由表断言（已补路由测试）。

### 4.4 测试 / 文档

- 后端测试密度高；前端只有 lint/typecheck/build，无单测/视觉回归，样式与组件拆分缺保护网。
- 契约文档 46KB，但无 ADR/架构图；§0 与 Vue 现状不符（已修）。

## 5. 已落地的 P0（本轮）

1. 删除 47 个无引用全局类及其媒体查询；`index.css` 3,143 → 2,624 行。
2. 新增 `--surface-alt/--line-faint/--pre-ink/--accent-ink/--accent-ink-soft/--accent-line/
   --amber-line/--amber-ink` 语义 token，替换 count-pill/batch-bar/tab-btn/editor/condition 等写死颜色；
   删除 8 条“旧页面深色补丁”，深色主题回到 token 驱动。
3. `theme-color` 随浅/深主题同步（index.html boot script + `useTheme`）。
4. 导航单一来源：新增 `src/navigation.ts`（路径/文案/图标），`AppLayout` 直接消费，删除 router 中未用的
   `NAV_ITEMS`。
5. 设置页版本/内置 jar 改由 `GET /api/settings/scrcpy/options` 提供，删除前端硬编码与
   `MirrorConfigCard` 的 `5.0.1` 默认值。
6. 移除 `MirrorStage` 未使用的 `hierarchy/logcat` emits 与 ConsolePage 绑定。
7. 后端路由统一：HTTP 用 `router`（带 `/api` prefix），WS 用 `ws_router`（挂根）；`main.py` 同时发现两者，
   并修复 `/ws/runs/{run_id}` 的挂载守卫（FastAPI 新版 `_IncludedRouter` 导致顶层检查失效）。
8. 新增 `tests/test_core_routes.py`：递归展开路由后断言关键 HTTP/WS 路径、无重复 HTTP 路由。
9. 更新 `contracts.md §0/§12/§14.6` 为 Vue + axios + router/ws_router 约定。

## 6. P1 已落地

1. **Vitest 基建**：`vitest.config.ts`、`npm run test`、CI 增加 Test 步骤；当前 **66 个用例**。
2. **通用 seq 事件流**：新增 `api/useSeqEventStream.ts`，`useRunEventsWs` / `useRecordingEventsWs`
   变成薄适配器（URL/字段映射/终态语义），公共的重连、心跳、去重、上限、清理只保留一份；
   用假 WebSocket 覆盖重连/终态/去重/截断/ping/清理。
3. **持久化开关**：`composables/usePersistentFlag.ts`，MirrorStage 三个特效开关改用它，
   删除组件内 `readFlag/writeFlag` 与 3 个写回 watch。
4. **页头**：RunDetail 改用 `PageHead`（返回/状态/元信息/操作统一）；ScriptsPage 补 `h1.visually-hidden`。
5. **统一 Tab**：`components/AppTabs.vue`（受控，5 种 variant），替换 5 套实现：
   控制台 dock、脚本页 segmented、设置页 sidebar、手动面板 scroll、运行详情 plain；
   删除各页面的重复 tab 样式与全局 `.tab-btn`/`.seg` 样式。
6. **统一空态**：`components/EmptyState.vue`（图标/标题/说明/操作/compact），控制台镜像卡与手动面板
   两处自定义空态改为复用；行内轻提示保持原样。
7. **类型收紧**：`SettingField` 的 `modelValue` 从 `any` 收为 `string | number | boolean | null`。
8. **文档/门禁**：`docs/component-conventions.md`；README 开发命令补 `npm run test`；CI 前端加入单测。

### 修复的问题

- `:global(html.dark) .selector` 在 scoped 样式中的写法会被编译成 `html.dark,html.dark{...}`（选择器被吞），
  导致深色 token 被错误地作用到 `<html>` 上。改为新增 `--tab-active-*` token（浅/深各一套），
  组件只消费 token，不再写 `:global()` 补丁。

### 设计决定（沿用现状，不再改）

- 列表页（脚本/设置/录制）不放大标题，只用 `h1.visually-hidden` + 面包屑；详情页（脚本编辑器/运行详情/
  录制详情）用 `PageHead`。因此 `PageHead` 不铺到列表页，避免与既定信息架构冲突。
- 侧栏/抽屉内的行内空提示（`pick-empty`、`menu-empty` 等）保持轻量，不套 `EmptyState`。

## 7. P2 已落地（结构收敛）

### 7.1 lib 私有域归位（纯移动，typecheck 护航）

| 原位置 | 新位置 | 理由 |
|---|---|---|
| `lib/h264.ts`、`lib/webcodecs.ts`、`lib/mirrorWs.ts` | `features/mirror/lib/` | 只被镜像域使用 |
| `lib/hierarchy.ts` | `features/devices/lib/` | 只被层级检查器使用 |
| `lib/condition.ts` | `features/scripts/lib/` | 只被条件编辑器使用 |
| `lib/format.ts`、`lib/scriptVars.ts`、`lib/bulk.ts`、`lib/clipboard.ts` | 保留在 `lib/` | 跨 feature 共享 |

### 7.2 巨型组件拆分

| 组件 | 拆分前 | 拆分后 |
|---|---|---|
| `ManualPanel` | 755 行 | 容器 120 行 + 5 个 tab（29–151 行）+ 共享 `manual-panel.css` |
| `SettingsPage` | 1344 行 | 容器 390 行 + 5 个 tab（67–199 行）+ `useSettingsDraft`(324) + `settings.css`(189) |
| `MirrorStage` | 1402 行 | 812 行 + `MirrorDeviceHeader`(424) + `MirrorActionRail`(234) |
| `ScreenInspector` | 1352 行 | 903 行 + `inspector/ScreenInspectorSide.vue`(538，browse/pick 两种侧栏) |

拆分方式：
- **只搬代码不改行为**：模板片段原样提取，样式块按选择器前缀整块搬到子组件（含对应媒体查询），
  父组件删除同名字样后确认无残留死样式。
- **状态归属**：手动面板 / 镜像菜单的界面状态下沉到子组件（含 `onClickOutside` 与 Escape），
  业务动作仍由父组件通过 emit 处理；设置页用 `useSettingsDraft` + provide/inject 下发草稿，
  各 tab 自带 `useHealth`/`useMaintenanceStats` 等查询（同一 query key 自动复用缓存）。
- **样式共享**：同一面板下的公共样式抽到 `manual-panel.css` / `settings.css`，通用类名收在
  `.manual-page` / `.settings-pane` 作用域内，避免污染全局。

### 7.3 全局样式迁出（完成）

`styles/index.css` 3,143 → **1,424 行**，页面级样式全部按 feature 归位：

| 文件 | 行数 | 内容 |
|---|---|---|
| `features/scripts/scripts.css` | 575 | 编辑器三栏、节点列表/节点编辑、表单、校验 |
| `features/settings/settings.css` | 281 | 设置页 5 个 tab + 工具链/表单/维护 |
| `features/mirror/mirror.css` | 264 | 画布/状态行/占位/录制条/视频录制状态点 |
| `features/runs/runs.css` | 249 | 进度、变量、步骤表、事件流、产物 |
| `features/console/manual-panel.css` | 243 | 手动控制 5 个 tab 共用 |
| `styles/index.css` | 1,424 | token、shell、Element Plus 覆写、表格、少量跨 feature 共享类 |

做法与校验：
- 迁出前逐类核对使用者：被迁出的类只在对应 feature 内使用；`pre-block`、`run-meta`、`run-actions`、
  `mirror-statusline-sep`、`kv`、`recording-event-low`、`logcat-pre` 等跨 feature 类留在全局。
- 迁移后发现并清理了重复定义（`mirror-toolbar`、`settings-grid`、`run-meta`），并在构建产物里逐一 grep
  确认所有选择器仍存在（脚本化检查 27 个关键类，缺失 0）。
- 全局类从 257 降到 104 个，启发式未引用为 0（`recording-event-low` 由 `:row-class-name` 动态返回）。

### 7.4 未做 / 下一批

- `MirrorStage` 的工具栏（特效弹层）与 viewport 仍未独立；`ScriptEditorPage`(770)、`RecordingDetailPage`(716) 次优先。
- `ScreenInspector` 的 canvas 命中测试/叠加层可进一步抽 `useHierarchyInspector`。
- `MirrorConfigCard`(671)、`AutoPanel`(607)、`RunMonitor`(589) 可继续收薄。

## 8. P3 契约防漂移（已落地第一步）

目标：让「手写 TS 类型」与「后端真实响应」不再靠人肉同步。

1. **后端响应模型**：`api_schemas.py` 新增 `OutModel`（`extra="ignore"`，避免短期字段不一致 500）与
   `RunOut` / `RunDetailOut` / `RunListOut` / `StepResultOut` / `ArtifactOut` / `SettingsOut` / `SettingEntryOut`；
   `/api/runs`（创建/列表/详情）与 `/api/settings`（GET/PUT/DELETE/reset/reload）加上 `response_model`，
   OpenAPI 现在有真实响应 schema（此前只有请求体）。
2. **导出与生成**：`scripts/export_openapi.py` 导出 `docs/openapi.json`（68 个路径，支持 `--check`）；
   前端 `npm run types:gen` 用 `openapi-typescript` 生成 `src/types/generated.ts`（4.4k 行），
   `npm run types:check` 校验生成结果无漂移。
3. **编译期护栏**：`src/types/contract.ts` 用类型断言检查手写 `Run`/`StepResult`/`Artifact`/`RunDetail`/
   `SettingsResponse`/`SettingEntry` 与生成类型：后端新增字段、改可空性都会在 `npm run typecheck` 时报错并指出字段名
   （已验证：删掉 `Run.created_at` 会报 `Type 'true' is not assignable to type '"created_at"'`）。
4. **首轮就抓到两处真实漂移**：`SettingsResponse.restart_required` 缺失、`SettingEntry.options/min/max/desc/
   layer/writable` 未容忍 null —— 已按后端契约修正手写类型。
5. **CI**：后端 job 跑 `export_openapi.py --check`，前端 job 跑 `npm run types:check`；新增
   `tests/test_core_routes.py::test_openapi_exposes_core_response_models` 断言核心 schema 存在。

### 8.1 扩展（本轮补齐）

- 响应模型与生成类型已覆盖 **runs / steps / artifacts / settings / scripts / recordings** 六个核心资源；
  `/api/scripts`（列表/详情）与 `/api/recordings`（列表/详情）加上 `response_model`。
- `OutModel` 改为 `extra="allow"`：模型声明字段用于 OpenAPI 与校验，未声明字段**原样透传**，
  避免手写 dict 领先于模型时字段被静默丢弃；生成类型会带 `& { [key: string]: unknown }`，
  `contract.ts` 用 `Plain<T>` 去掉索引签名后再比对。
- 护栏又抓到一处真实漂移：`Script.created_at/updated_at` 手写为非空，后端脚本是 JSON 文件可能没有时间戳 → 已改为 `| null`。

### 8.2 未做

- devices / reports 端点的响应模型（收益递减，暂不铺开）。
- 前后端共享 fixture（条件求值、选择器 index、变量插值）双向断言。
- 可选拆 `api/devices.py` 触摸流状态机、`mirror.py` 会话/注册表。

## 8. 验收基线

```bash
cd frontend && npm run lint && npm run typecheck && npm run build
cd ../backend && uv run ruff check app && uv run pytest -q
```

P0 结果：前端 lint/typecheck/build 通过；后端 ruff 通过、pytest 670 passed。
P1/P2 结果：前端 lint/typecheck/test/build 通过（66 passed）；后端 pytest 670 passed。
P3 结果：前端 lint/typecheck/test/build + `types:check` 通过；后端 ruff 通过、pytest **671 passed**。
