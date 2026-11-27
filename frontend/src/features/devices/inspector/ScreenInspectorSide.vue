<script setup lang="ts">
// 检查器右侧栏：browse 模式（层级树 + 节点详情）与 pick 模式（坐标 / 祖先链 / 定位方式）。
// 只负责展示与本地复制动作；树选中、拾取选择等状态由父组件持有。
import { Aim, CopyDocument, Location } from '@element-plus/icons-vue'
import { ElMessage } from 'element-plus'

import { copyText } from '@/lib/clipboard'
import {
  boundsText,
  depthColor,
  labelOf,
  selectorsOf,
  selectorText,
  type InspectorNode,
  type InspectorTree,
  type InspectorTreeRow,
  type SelectorBy,
} from '../lib/hierarchy'

const props = defineProps<{
  mode: 'browse' | 'pick'
  allowPoint: boolean
  tree: InspectorTree
  treeData: InspectorTreeRow[]
  expandedKeys: string[]
  maxDepthShown: number
  selectedKey: string | null
  hoverKey: string | null
  selectedNode: InspectorNode | null
  selectedFlags: string
  /** 是否已拾取到位置（决定显示空提示还是坐标/祖先链）。 */
  hasPickPoint: boolean
  pickPointDevice: { x: number; y: number } | null
  pickChain: InspectorNode[]
  pickKey: string | null
  recommendedKey: string | null
  pickNode: InspectorNode | null
  pickSelectors: Array<{ by: SelectorBy; value: string | number | null }>
  pickAttrBy: SelectorBy | null
  pickValue: string | number | null
  pickIndex: number
}>()

const emit = defineEmits<{
  'tree-click': [data: unknown]
  'tree-hover': [key: string | null]
  'choose-node': [item: InspectorNode]
  'set-attr': [value: string | number | boolean | undefined]
  'apply-element': []
  'apply-point': []
}>()

const TREE_PROPS = { children: 'children', label: 'label', disabled: 'disabled' } as const
const ATTR_LABEL: Record<SelectorBy, string> = {
  text: '文本 text',
  content_desc: '描述 desc',
  resource_id: '资源 ID',
}

// 与父组件同一规则：可见 + 至少一个可回填选择器。
function isPickable(item: InspectorNode): boolean {
  return item.node.visible && selectorsOf(item.node).length > 0
}

async function copySelector(): Promise<void> {
  const node = props.selectedNode?.node
  if (!node) return
  const ok = await copyText(selectorText(node))
  if (ok) ElMessage.success(`已复制：${selectorText(node)}`)
  else ElMessage.error('复制失败，请手动选择文本')
}

async function copyCenter(): Promise<void> {
  const node = props.selectedNode?.node
  if (!node) return
  const [x1, y1, x2, y2] = (node.bounds ?? [0, 0, 0, 0]) as number[]
  const text = `${Math.round((x1 + x2) / 2)}, ${Math.round((y1 + y2) / 2)}`
  const ok = await copyText(text)
  if (ok) ElMessage.success(`已复制中心坐标：${text}`)
  else ElMessage.error('复制失败，请手动选择文本')
}
</script>

<template>
<aside class="inspector-side">
  <!-- 浏览：层级树 + 节点详情 -->
  <template v-if="mode === 'browse'">
    <div class="side-head">
      <span>层级树</span>
      <span class="hint">{{ tree.nodeCount }} 节点</span>
    </div>
    <el-tree
      v-if="tree.nodeCount > 1"
      :key="`tree-${maxDepthShown}-${tree.nodeCount}`"
      class="hierarchy-tree"
      :data="treeData"
      node-key="key"
      :props="TREE_PROPS"
      :default-expanded-keys="expandedKeys"
      :expand-on-click-node="false"
      highlight-current
      :current-node-key="selectedKey ?? undefined"
      @node-click="(data: unknown) => emit('tree-click', data)"
    >
      <template #default="{ data }">
        <div
          class="tree-row"
          :class="{
            'is-hover': hoverKey === data.key,
            'is-selected': selectedKey === data.key,
            'is-invisible': !data.node.visible,
          }"
          @mouseenter="emit('tree-hover', data.key)"
          @mouseleave="emit('tree-hover', null)"
        >
          <span class="depth-bar" :style="{ background: depthColor(data.depth) }" />
          <span class="tree-label" :title="boundsText(data.node)">{{ labelOf(data.node) }}</span>
          <span v-if="data.node.clickable" class="tree-tag">可点</span>
        </div>
      </template>
    </el-tree>
    <el-empty v-else description="未获取到层级（设备可能离线或未授权 uiautomator）" :image-size="56" />

    <div v-if="selectedNode" class="detail-card">
      <div class="side-head detail-head">
        <span>节点详情</span>
        <el-button text size="small" :icon="CopyDocument" @click="copySelector">复制选择器</el-button>
      </div>
      <dl class="kv-list">
        <dt>class</dt>
        <dd class="code-text">{{ selectedNode.node.class_name || '—' }}</dd>
        <template v-if="selectedNode.node.text">
          <dt>text</dt>
          <dd>{{ selectedNode.node.text }}</dd>
        </template>
        <template v-if="selectedNode.node.resource_id">
          <dt>id</dt>
          <dd class="code-text">{{ selectedNode.node.resource_id }}</dd>
        </template>
        <template v-if="selectedNode.node.content_desc">
          <dt>desc</dt>
          <dd>{{ selectedNode.node.content_desc }}</dd>
        </template>
        <template v-if="selectedNode.node.package">
          <dt>package</dt>
          <dd class="code-text">{{ selectedNode.node.package }}</dd>
        </template>
        <dt>bounds</dt>
        <dd class="code-text">{{ boundsText(selectedNode.node) }}</dd>
        <dt>层级</dt>
        <dd>第 {{ selectedNode.depth }} 层 · 先序 #{{ selectedNode.order }}</dd>
        <dt>状态</dt>
        <dd>{{ selectedFlags }}</dd>
      </dl>
      <el-button size="small" :icon="Location" @click="copyCenter">复制中心坐标</el-button>
    </div>
    <div v-else class="side-tip">
      <p>悬停截图上任一元素，可在这里看到它的信息。</p>
      <p>点击图中的元素或树节点会高亮，并显示详情与选择器。</p>
    </div>
  </template>

  <!-- 拾取：坐标 + 祖先链 + 定位方式 -->
  <template v-else>
    <div class="side-head">
      <span>拾取元素</span>
      <span class="hint">点击左侧截图</span>
    </div>
    <div v-if="!hasPickPoint" class="pick-empty">
      <el-icon :size="22"><Aim /></el-icon>
      <p>在左侧截图上点击要操作的位置。</p>
      <p class="hint">会列出该位置所在的每一层节点，可任选一层作为定位目标。</p>
    </div>
    <template v-else>
      <div v-if="allowPoint" class="pick-coord">
        <div class="pick-coord-value">
          <span class="hint">坐标</span>
          <strong class="code-text">{{ pickPointDevice?.x }}, {{ pickPointDevice?.y }}</strong>
        </div>
        <el-button size="small" type="primary" plain :icon="Location" @click="emit('apply-point')"
          >使用坐标</el-button
        >
      </div>
      <div v-else class="pick-coord pick-coord-static">
        <span class="hint">这里只支持元素定位，不能用坐标。</span>
      </div>

      <div class="pick-section">该位置的层级（{{ pickChain.length }} 层，由外到内）</div>
      <div v-if="pickChain.length === 0" class="hint pick-none">
        {{
          allowPoint
            ? '这个点不在任何元素范围内，只能使用坐标。'
            : '这个点不在任何元素范围内，请换一处点击。'
        }}
      </div>
      <div v-else class="pick-chain">
        <button
          v-for="item in pickChain"
          :key="item.key"
          type="button"
          class="pick-row"
          :class="{
            'is-on': pickKey === item.key,
            'is-recommended': recommendedKey === item.key,
            'is-disabled': !isPickable(item),
          }"
          :disabled="!isPickable(item)"
          @click="emit('choose-node', item)"
        >
          <span class="depth-bar" :style="{ background: depthColor(item.depth) }" />
          <span class="pick-label">{{ labelOf(item.node) }}</span>
          <span v-if="recommendedKey === item.key" class="pick-tag">推荐</span>
          <span v-else-if="!item.node.visible" class="pick-tag muted">不可见</span>
          <span v-else-if="!isPickable(item)" class="pick-tag muted">无选择器</span>
        </button>
      </div>

      <template v-if="pickNode && pickSelectors.length">
        <template v-if="pickSelectors.length > 1">
          <div class="pick-section">定位方式</div>
          <el-radio-group
            class="pick-attrs"
            :model-value="pickAttrBy ?? undefined"
            size="small"
            @update:model-value="(value: string | number | boolean | undefined) => emit('set-attr', value)"
          >
            <el-radio-button v-for="selector in pickSelectors" :key="selector.by" :value="selector.by">
              {{ ATTR_LABEL[selector.by] }}
            </el-radio-button>
          </el-radio-group>
        </template>
        <div v-else class="pick-section">定位方式：{{ ATTR_LABEL[pickSelectors[0].by] }}</div>
        <div class="pick-preview">
          <span class="hint">回填</span>
          <code class="code-text">{{ pickAttrBy }} = {{ pickValue }}</code>
          <span class="hint">index {{ pickIndex }}</span>
        </div>
        <el-button type="primary" size="small" :icon="Aim" @click="emit('apply-element')">使用此元素</el-button>
        <div class="pick-note">index 按同选择器在可见节点里的文档顺序计算，与回放时一致。</div>
      </template>
      <div v-else class="pick-note">
        {{
          allowPoint
            ? '该位置的节点都没有 text / 内容描述 / 资源 ID，换一处点击，或直接使用坐标。'
            : '该位置的节点都没有 text / 内容描述 / 资源 ID，请换一处点击。'
        }}
      </div>
      <div class="pick-tip">双击截图 = 直接采用推荐项</div>
    </template>
  </template>
</aside>
</template>

<style scoped>
.inspector-side {
  display: flex;
  flex-direction: column;
  gap: 8px;
  min-width: 0;
  max-height: 68vh;
  overflow: auto;
  padding-right: 2px;
}

.side-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 6px;
  padding-bottom: 5px;
  border-bottom: 1px solid var(--line-soft);
  color: var(--ink);
  font-size: 11.5px;
  font-weight: 650;
}

.side-head .hint {
  font-weight: 400;
}

.detail-head {
  border-bottom: 0;
  padding-bottom: 0;
}

.hierarchy-tree {
  --el-tree-node-hover-bg-color: transparent;
  background: transparent;
  font-size: 11px;
}

.tree-row {
  display: flex;
  align-items: center;
  gap: 5px;
  min-width: 0;
  flex: 1;
  padding: 1px 3px;
  border-radius: 4px;
}

.tree-row.is-hover,
.tree-row.is-selected {
  background: var(--accent-soft);
}

.tree-row.is-invisible {
  opacity: 0.55;
}

.depth-bar {
  flex: none;
  width: 3px;
  height: 12px;
  border-radius: 2px;
}

.tree-label {
  overflow: hidden;
  color: var(--ink);
  font-size: 10.5px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.tree-tag {
  flex: none;
  padding: 0 4px;
  border-radius: 4px;
  background: var(--surface-3);
  color: var(--muted-2);
  font-size: 9px;
}

.detail-card {
  display: flex;
  flex-direction: column;
  gap: 6px;
  padding: 8px;
  border: 1px solid var(--line);
  border-radius: 7px;
  background: var(--surface-2);
}

.kv-list {
  display: grid;
  grid-template-columns: 54px minmax(0, 1fr);
  gap: 3px 8px;
  margin: 0;
}

.kv-list dt {
  color: var(--muted-2);
  font-size: 10px;
}

.kv-list dd {
  margin: 0;
  color: var(--ink);
  font-size: 10.5px;
  word-break: break-all;
}

.side-tip {
  color: var(--muted-2);
  font-size: 10.5px;
  line-height: 1.6;
}

.side-tip p {
  margin: 0 0 2px;
}

.pick-empty {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 5px;
  padding: 22px 12px;
  border: 1px dashed var(--line);
  border-radius: 7px;
  color: var(--muted);
  text-align: center;
}

.pick-empty p {
  margin: 0;
  font-size: 11px;
  line-height: 1.5;
}

.pick-coord {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  padding: 7px 8px;
  border: 1px solid var(--line);
  border-radius: 7px;
  background: var(--surface-2);
}

.pick-coord-static {
  justify-content: flex-start;
}

.pick-coord-value {
  display: flex;
  flex-direction: column;
  gap: 1px;
}

.pick-coord-value strong {
  color: var(--ink);
  font-size: 12px;
}

.pick-section {
  margin-top: 4px;
  color: var(--muted);
  font-size: 10.5px;
  font-weight: 600;
}

.pick-chain {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.pick-row {
  display: flex;
  align-items: center;
  gap: 6px;
  width: 100%;
  min-width: 0;
  padding: 4px 5px;
  border: 1px solid transparent;
  border-radius: 5px;
  background: transparent;
  color: var(--ink);
  text-align: left;
  cursor: pointer;
}

.pick-row:hover:not(:disabled) {
  background: var(--surface-3);
}

.pick-row.is-on {
  border-color: var(--accent);
  background: var(--accent-soft);
}

.pick-row.is-disabled {
  cursor: not-allowed;
  opacity: 0.5;
}

.pick-label {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  font-family: var(--mono);
  font-size: 10.5px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.pick-tag {
  flex: none;
  padding: 0 4px;
  border-radius: 4px;
  background: var(--accent-soft);
  color: var(--accent);
  font-size: 9px;
  font-weight: 600;
}

.pick-tag.muted {
  background: var(--surface-3);
  color: var(--muted-2);
  font-weight: 400;
}

.pick-attrs {
  display: flex;
  flex-wrap: wrap;
}

.pick-preview {
  display: flex;
  align-items: center;
  gap: 5px;
  flex-wrap: wrap;
  padding: 5px 7px;
  border: 1px solid var(--line-soft);
  border-radius: 6px;
  background: var(--surface-2);
}

.pick-preview code {
  color: var(--ink);
  font-size: 10.5px;
  word-break: break-all;
}

.pick-note {
  color: var(--muted-2);
  font-size: 10px;
  line-height: 1.5;
}

.pick-tip {
  margin-top: 2px;
  color: var(--muted-2);
  font-size: 10px;
}

.pick-none {
  line-height: 1.5;
}

:deep(.el-tree-node__content) {
  height: 24px;
  border-radius: 5px;
}

:deep(.el-tree-node__content:hover) {
  background: var(--surface-3);
}

@media (max-width: 980px) {
  .inspector-side {
    max-height: none;
  }
}
</style>
