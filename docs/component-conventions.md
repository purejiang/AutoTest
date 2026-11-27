# 前端组件与样式约定（v0.2.6）

目标：共享层只放真正跨 feature 的东西；页面/领域相关逻辑留在 feature 内。
新增代码按本约定评审，避免再次出现「全局样式沉淀 + 巨型组件 + 各写一套」。

## 1. 目录与边界

- `components/`：跨 feature 的通用视觉组件。当前：`AppTabs`、`EmptyState`、`PageHead`、`StatusTag`、
  `Notice`（状态/警告）、`HelpTip`（标题旁长说明）、`VariableField`、`VariablePickerButton`。
  新增前先确认已有组件是否可扩展，不要复制一份。
- `composables/`：跨 feature 的通用状态/浏览器能力（`useTheme`、`usePersistentFlag`、
  `useScriptVariables`）。域名相关逻辑放 `features/<域>/composables/`。
- `lib/`：无 Vue 依赖、**跨 feature 复用**的纯函数（`format`、`scriptVars`、`bulk`、`clipboard`）。
  只被单个 feature 使用的逻辑放 `features/<域>/lib/`：镜像协议在 `features/mirror/lib/`，
  层级树在 `features/devices/lib/`，条件在 `features/scripts/lib/`。
- `api/`：一个后端域一个模块；query key、类型、hooks 同文件。通用连接骨架放
  `api/useSeqEventStream.ts`，域 hook 只写 URL/字段映射。
- `features/<域>/`：页面、域组件、域 composable、域 lib。跨 feature 引用必须显式且单向：
  `console → devices/mirror`、`scripts → devices/recorder` 属已知例外；新增反向依赖先讨论。
- `styles/index.css`：只放 token、shell、Element Plus 覆写、真正跨页面的共享样式；
  页面布局/组件样式放组件 `<style scoped>`。
- 同一页面族的共享样式放该 feature 下的 `*.css` 并在页面入口 `import`：
  `features/scripts/scripts.css`、`features/settings/settings.css`、`features/mirror/mirror.css`、
  `features/runs/runs.css`、`features/console/manual-panel.css`。
- 往 feature css 迁样式前：脚本核对每类只被对应 feature 使用；迁完在构建产物里 grep 关键选择器，
  并检查没有和 `styles/index.css` 重复定义。跨 feature 复用的类（如 `pre-block`、`kv`）留在全局。

## 2. 组件规模与拆分

- 单文件超过 ~400 行（template + script + style）时，优先按「职责」拆子组件或 composable；
  拆分只搬代码，不改行为，保留对外 props/emits/`defineExpose`。
- 同一面板的多个子页共用一个 `*.css`（如 `features/console/manual-panel.css`、
  `features/settings/settings.css`），通用类名收在面板根类作用域内，避免全局污染。
- 子页需要的后端数据由子页自己 `useXxx()`（同一 query key 自动复用缓存），不要为取数层层传 props。
- 一个组件只承担一个页面区块；页面容器负责取数/编排，子组件通过 props/emits 交互。
- 禁止在子组件里直接调父组件的 API/路由；需要时用 emit 或 composable。

## 3. 样式

- 颜色/圆角/阴影/间距优先用 `:root` token；禁止在 scoped 样式里新增裸色值
  （代码块、日志、始终深色的镜像舞台/叠加层除外，需注释说明）。
- 深浅主题必须同时成立：不要写 `html.dark .xxx { background: #... }` 补丁；
  正确做法是组件用 token，深色由 token 值切换。
- 新增全局类前先确认组件 scoped 是否足够；页面级类不要放进 `styles/index.css`。
- 字号保持现有密度（正文 12–13px、辅助 11px），不要再新增 9/9.5/10px 档。

## 4. 交互与可访问性

- 页头：列表页（脚本/设置/录制）不放大标题，用 `h1.visually-hidden` + 面包屑；详情页用 `PageHead`
  （标题/返回/计数/副标题/操作/标题下说明）。
- Tab 统一用 `AppTabs`（受控）：状态由父级持有（路由 query / Pinia / 本地 ref），组件只发
  `update:modelValue`；variant 按场景选 `segmented`/`dock`/`sidebar`/`scroll`/`plain`，不要新增第 6 套。
- 空态统一用 `EmptyState`：页面/卡片级用带图标/标题/说明/操作（可 `compact`）；行内提示保持轻量。
- 不要在 `<style scoped>` 里写 `:global(html.dark) .x` 这种前缀式补丁（会被编译成错误选择器），
  深色差异一律通过新增 token 解决。
- 布尔偏好用 `usePersistentFlag`，不要在组件里直写 localStorage。
- 表单控件（`el-select` / `el-input` / `el-input-number`）必须是 `el-form-item` 的直接子元素；
  需要「控件 + 标题下提示」时，用一个显式 `width:100%` 的列容器（如 `.capture-field`）包住控件和
  `.hint`。不要给无宽度的 flex 包裹里的 `el-select` 写 `width:100%`：它会以自身内容宽度作为百分比
  基准，塌缩成几十像素（v0.2.6 脚本设置「Logcat 范围」即此问题，实测 34px）。
- 设置页采用可读宽度 + 单列设置行：内容列最大 840px，`SettingField` 在容器 ≥640px 时左侧放
  标签/说明、右侧放 280px 控件；容器更窄时自动上下堆叠（`@container`，不是写死视口断点）。
  不要再把设置字段排成固定两列，也不要把控件拉满整张卡片。
- 错误提示沿用全局 `notifyApiError`；只有需要自定义交互（如逐字段错误）时才捕获 mutation 错误。

### 4.1 说明、帮助与警告

说明文字分五层，不要把所有解释都堆在字段下面：

| 层 | 位置 | 用途 | 长度 |
|---|---|---|---|
| Label | 控件上方/左侧 | 名称 | 名词短语 |
| Hint | 标题/控件下方，始终可见 | 影响、边界、继承关系 | 中文 ≤40 字，桌面 1 行、窄屏 ≤2 行 |
| HelpTip | 标题右侧 `?` | 示例、长解释、链接 | 不限制，但不放关键前置条件 |
| Default | 控件下方 | “默认：8000” | 只放值/来源 |
| Notice | 页面/卡片顶部或字段旁 | 状态、风险、失败、操作 | 标题 ≤20 字，正文 ≤2 句 |

- 短说明用全局 `.hint`；长说明用 [HelpTip](../frontend/src/components/HelpTip.vue)；
  **关键信息不能只放在 tooltip 或 placeholder 里**（NN/g 表单建议）。
- 同一事实只出现一次：label / hint / placeholder / default 不互相复述。
- 说明要紧挨触发它的控件：动作/模式（如「清除数据」「grant」）的提示放在该下拉框下方，
  不要放到后面的 package/参数字段下面；开关关闭时不再显示只对开启状态有意义的计数说明。
- 状态提示统一用 [Notice](../frontend/src/components/Notice.vue)，四级语义：

| severity | 颜色 | 图标 | 使用场景 |
|---|---|---|---|
| info | 蓝 `--accent` | info | 解释、只读、启动项 |
| success | 绿 `--green` | check | 保存成功、检查通过 |
| warning | 黄 `--amber` | warning | 非阻断风险、性能/成本提醒、依赖未开启 |
| danger | 红 `--red` | error | 失败、阻断、数据丢失 |

- 黄色只表示“注意/风险”，红色只表示“已失败/阻断”；颜色必须配合图标和文案，不能只靠颜色。
- `variant="compact"` 用于字段旁，`block` 用于页面/卡片级；字段校验错误继续用 `el-form-item` 红字，
  不再额外套 Notice；瞬时操作结果继续用 `ElMessage`；行内状态继续用 `StatusTag`。
- 禁止新增 `.field-hint-warn` / `.inline-warning` / `.video-warning` 之类的 ad-hoc 警告盒子；
  新增提示先判断能否用 `.hint` / HelpTip / Notice 三种。

## 5. 后端契约类型

- 改后端响应结构时：先改 `api_schemas.py` 的 `*Out` 模型 → 跑 `python scripts/export_openapi.py`
  → `cd frontend && npm run types:gen`，再改 `src/types/api.ts`。
- `src/types/contract.ts` 是编译期护栏：后端字段改名/新增/改可空性会让 `npm run typecheck` 失败并指出字段。
- CI 会校验 `docs/openapi.json` 与 `src/types/generated.ts` 是否最新；不要手改 `generated.ts`。

## 6. 测试

- 纯逻辑（`lib/`、域工具）必须有 Vitest 单测；共享 composable 用假 WebSocket/假 storage 测边界。
- 共享组件至少覆盖：props 默认值、slot、关键 emit。
- 提交前跑：`npm run lint && npm run typecheck && npm run test && npm run build`。
- CI 已加入 `npm run test`；新增测试文件命名 `*.test.ts`，与源码同目录。
