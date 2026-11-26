<script setup lang="ts">
// 截图 + 层级叠加检查器：一张当前屏幕截图，上面叠加各层元素框。
// - browse 模式：悬停看信息、右侧全量层级树与图联动、点击复制选择器（手动控制「层级树」用它）；
// - pick 模式：点击位置后按「祖先链」列出该点所在的每一层，选元素或坐标回填 Target（脚本编辑器用它）。
//
// 坐标系：screencap 与 uiautomator 同坐标系，默认 1:1；仅当层级范围超出截图尺寸时按比例缩放。
import { Aim, CopyDocument, Location, Refresh } from '@element-plus/icons-vue'
import { ElMessage } from 'element-plus'
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'

import type { HierarchyNode, Target } from '@/types/api'
import { copyText } from '@/lib/clipboard'
import {
  boundsText,
  buildInspectorTree,
  chainAt,
  computeScale,
  depthColor,
  labelOf,
  matchIndex,
  nodesWithinDepth,
  recommendNode,
  scaledBox,
  selectorText,
  selectorsOf,
  shortClass,
  targetForNode,
  valueOf,
  type InspectorNode,
  type SelectorBy,
} from '@/lib/hierarchy'

const props = withDefaults(
  defineProps<{
    /** 当前层级根节点（后端 /hierarchy 的 node）。 */
    root: HierarchyNode | null
    /** 截图 objectURL。 */
    imageUrl: string | null
    mode?: 'browse' | 'pick'
    /** pick 模式是否允许回填坐标（执行条件等场景只允许元素）。 */
    allowPoint?: boolean
    loading?: boolean
    /** 已格式化的错误文案（describeApiError 的结果）。 */
    error?: string | null
  }>(),
  { mode: 'browse', allowPoint: true, loading: false, error: null },
)

const emit = defineEmits<{ refresh: []; pick: [target: Target] }>()

const MAX_OVERLAY_BOXES = 3000
const MAX_LABELS = 200
const TREE_PROPS = { children: 'children', label: 'label', disabled: 'disabled' } as const
const ATTR_LABEL: Record<SelectorBy, string> = {
  text: '文本 text',
  content_desc: '描述 desc',
  resource_id: '资源 ID',
}

// ---------------------------------------------------------------- 层级数据

const tree = computed(() => buildInspectorTree(props.root))
const nodeByKey = computed(() => {
  const map = new Map<string, InspectorNode>()
  for (const item of tree.value.flat) map.set(item.key, item)
  return map
})

const maxDepthShown = ref(0)
const clickableOnly = ref(false)
const showLabels = ref(false)

const sliderMax = computed(() => Math.max(tree.value.maxDepth, 1))

const filteredNodes = computed(() => {
  const list = nodesWithinDepth(tree.value, maxDepthShown.value)
  return clickableOnly.value ? list.filter((item) => item.node.clickable) : list
})

const truncated = computed(() => filteredNodes.value.length > MAX_OVERLAY_BOXES)

const expandedKeys = computed(() => tree.value.flat.filter((item) => item.depth < 2).map((item) => item.key))

type TreeRow = { key: string; node: HierarchyNode; depth: number; children: TreeRow[] }

function pruneRows(items: InspectorNode[], maxDepth: number): TreeRow[] {
  return items.map((item) => ({
    key: item.key,
    node: item.node,
    depth: item.depth,
    children: item.depth < maxDepth ? pruneRows(item.children, maxDepth) : [],
  }))
}

const treeData = computed<TreeRow[]>(() => pruneRows(tree.value.roots, maxDepthShown.value))

// ---------------------------------------------------------------- 截图尺寸 / 比例

const canvasRef = ref<HTMLDivElement | null>(null)
const pngWidth = ref(0)
const pngHeight = ref(0)
const displayWidth = ref(0)
const displayHeight = ref(0)

const scale = computed(() => computeScale(tree.value, pngWidth.value, pngHeight.value))

function syncDisplaySize(): void {
  const el = canvasRef.value
  if (!el) return
  displayWidth.value = el.clientWidth
  displayHeight.value = el.clientHeight
}

let resizeObserver: ResizeObserver | null = null
onMounted(() => {
  if (typeof ResizeObserver === 'undefined') return
  resizeObserver = new ResizeObserver(() => syncDisplaySize())
  if (canvasRef.value) resizeObserver.observe(canvasRef.value)
  syncDisplaySize()
})
onBeforeUnmount(() => {
  resizeObserver?.disconnect()
  resizeObserver = null
  if (rafId) cancelAnimationFrame(rafId)
})

watch(
  () => canvasRef.value,
  (el) => {
    if (!resizeObserver) return
    resizeObserver.disconnect()
    if (el) resizeObserver.observe(el)
  },
  { flush: 'post' },
)

function onImageLoad(event: Event): void {
  const image = event.target as HTMLImageElement
  pngWidth.value = image.naturalWidth || 0
  pngHeight.value = image.naturalHeight || 0
  selectedKey.value = null
  resetPick()
  requestAnimationFrame(syncDisplaySize)
}

watch(
  () => props.imageUrl,
  () => {
    pngWidth.value = 0
    pngHeight.value = 0
    selectedKey.value = null
    resetPick()
    imageHover.value = null
    treeHoverKey.value = null
    pointer.value = null
  },
)

// 层级变化（刷新/换设备）后重置视图范围与选中项。
watch(tree, (next) => {
  maxDepthShown.value = next.maxDepth
  selectedKey.value = null
  imageHover.value = null
  treeHoverKey.value = null
  resetPick()
})

watch(
  () => props.mode,
  () => resetPick(),
)

// ---------------------------------------------------------------- 叠加框

interface OverlayBox {
  key: string
  x: number
  y: number
  width: number
  height: number
  depth: number
  clickable: boolean
  visible: boolean
}

const overlayBoxes = computed<OverlayBox[]>(() => {
  const out: OverlayBox[] = []
  const current = scale.value
  for (const item of filteredNodes.value) {
    const box = scaledBox(item.node, current)
    if (box.width <= 0 || box.height <= 0) continue
    out.push({
      key: item.key,
      x: box.x,
      y: box.y,
      width: box.width,
      height: box.height,
      depth: item.depth,
      clickable: item.node.clickable,
      visible: item.node.visible,
    })
    if (out.length >= MAX_OVERLAY_BOXES) break
  }
  return out
})

const labelItems = computed(() => {
  if (!showLabels.value || pngWidth.value <= 0 || pngHeight.value <= 0) return []
  const out: Array<{ key: string; text: string; style: Record<string, string> }> = []
  const current = scale.value
  for (const item of filteredNodes.value.filter((entry) => entry.node.visible).slice(0, MAX_LABELS)) {
    const node = item.node
    const text =
      String(node.text ?? '').trim() ||
      String(node.content_desc ?? '').trim() ||
      (String(node.resource_id ?? '')
        .split('/')
        .pop() ??
        '') ||
      shortClass(node.class_name)
    const box = scaledBox(node, current)
    if (box.width <= 0 || box.height <= 0) continue
    out.push({
      key: item.key,
      text,
      style: {
        left: `${(box.x / pngWidth.value) * 100}%`,
        top: `${(box.y / pngHeight.value) * 100}%`,
      },
    })
  }
  return out
})

// ---------------------------------------------------------------- 悬停 / 选中

const pointer = ref<{ x: number; y: number; px: number; py: number } | null>(null)
const imageHover = ref<InspectorNode | null>(null)
const treeHoverKey = ref<string | null>(null)
const selectedKey = ref<string | null>(null)

const hoverKey = computed<string | null>(() => {
  if (imageHover.value) return imageHover.value.key
  return treeHoverKey.value
})

const hoveredBox = computed(() => overlayBoxes.value.find((box) => box.key === hoverKey.value) ?? null)
const selectedBox = computed(() =>
  selectedKey.value ? (overlayBoxes.value.find((box) => box.key === selectedKey.value) ?? null) : null,
)
const selectedNode = computed(() =>
  selectedKey.value ? (nodeByKey.value.get(selectedKey.value) ?? null) : null,
)

function flagsOf(node: HierarchyNode | undefined): string {
  if (!node) return ''
  const flags: string[] = []
  if (node.clickable) flags.push('可点击')
  if (!node.enabled) flags.push('禁用')
  if (!node.visible) flags.push('不可见')
  return flags.length > 0 ? flags.join(' · ') : '静态节点'
}

const selectedFlags = computed(() => flagsOf(selectedNode.value?.node))

const tipNode = computed<InspectorNode | null>(() => imageHover.value)

const tipFlags = computed(() => flagsOf(tipNode.value?.node))

const tipStyle = computed<Record<string, string>>(() => {
  const point = pointer.value
  if (!point) return {} as Record<string, string>
  const width = displayWidth.value || 0
  const height = displayHeight.value || 0
  const maxLeft = Math.max(6, width - 340)
  const left = Math.min(Math.max(6, point.px + 14), maxLeft)
  let top = point.py + 14
  let transform = 'none'
  if (height > 0 && point.py > height * 0.55) {
    top = point.py - 14
    transform = 'translateY(-100%)'
  }
  return { left: `${left}px`, top: `${Math.max(6, top)}px`, transform }
})

// rAF 节流：pointermove 高频，命中测试放在下一帧只算一次。
let rafId = 0
let pendingPoint: { x: number; y: number; px: number; py: number } | null = null

function imagePointFromEvent(event: MouseEvent | PointerEvent): {
  x: number
  y: number
  px: number
  py: number
} | null {
  const el = canvasRef.value
  if (!el || pngWidth.value <= 0 || pngHeight.value <= 0) return null
  const rect = el.getBoundingClientRect()
  if (rect.width <= 0 || rect.height <= 0) return null
  const px = event.clientX - rect.left
  const py = event.clientY - rect.top
  return {
    x: (px / rect.width) * pngWidth.value,
    y: (py / rect.height) * pngHeight.value,
    px,
    py,
  }
}

function applyPendingHover(): void {
  rafId = 0
  const point = pendingPoint
  pendingPoint = null
  if (!point) return
  pointer.value = point
  const chain = chainAt(tree.value, point.x, point.y)
  imageHover.value = chain.length > 0 ? chain[chain.length - 1] : null
}

function onPointerMove(event: PointerEvent): void {
  const point = imagePointFromEvent(event)
  if (!point) return
  pendingPoint = point
  if (!rafId) rafId = requestAnimationFrame(applyPendingHover)
}

function onPointerLeave(): void {
  if (rafId) {
    cancelAnimationFrame(rafId)
    rafId = 0
  }
  pendingPoint = null
  pointer.value = null
  imageHover.value = null
}

function onOverlayClick(event: MouseEvent): void {
  const point = imagePointFromEvent(event)
  if (!point) return
  if (props.mode === 'pick') {
    setPickPoint(point.x, point.y)
    return
  }
  const chain = chainAt(tree.value, point.x, point.y)
  selectedKey.value = chain.length > 0 ? chain[chain.length - 1].key : null
}

function onOverlayDblClick(event: MouseEvent): void {
  if (props.mode !== 'pick') return
  const point = imagePointFromEvent(event)
  if (!point) return
  setPickPoint(point.x, point.y)
  if (pickKey.value) applyElement()
  else if (props.allowPoint) applyPoint()
}

// ---------------------------------------------------------------- 拾取

const pickPoint = ref<{ x: number; y: number } | null>(null)
const pickChain = ref<InspectorNode[]>([])
const recommendedKey = ref<string | null>(null)
const pickKey = ref<string | null>(null)
const pickAttrBy = ref<SelectorBy | null>(null)

function resetPick(): void {
  pickPoint.value = null
  pickChain.value = []
  recommendedKey.value = null
  pickKey.value = null
  pickAttrBy.value = null
}

const pickNode = computed(() => (pickKey.value ? (nodeByKey.value.get(pickKey.value) ?? null) : null))
const pickSelectors = computed(() => (pickNode.value ? selectorsOf(pickNode.value.node) : []))
const pickValue = computed(() => {
  if (!pickNode.value || !pickAttrBy.value) return ''
  return valueOf(pickNode.value.node, pickAttrBy.value)
})
const pickIndex = computed(() => {
  if (!pickNode.value || !pickAttrBy.value) return 0
  return matchIndex(tree.value, pickNode.value.node, pickAttrBy.value, pickValue.value)
})

const pickPointDevice = computed(() => {
  const point = pickPoint.value
  if (!point) return null
  const sx = scale.value.sx || 1
  const sy = scale.value.sy || 1
  return { x: Math.round(point.x / sx), y: Math.round(point.y / sy) }
})

const pointerDevice = computed(() => {
  const point = pointer.value
  if (!point) return null
  const sx = scale.value.sx || 1
  const sy = scale.value.sy || 1
  return { x: Math.round(point.x / sx), y: Math.round(point.y / sy) }
})

const statusLine = computed(() => {
  if (pngWidth.value <= 0) return props.error ? '截图不可用' : '截图加载中…'
  const parts = [`截图 ${pngWidth.value}×${pngHeight.value}`]
  if (tree.value.nodeCount > 0) {
    parts.push(`${tree.value.nodeCount} 节点`, `最深 ${tree.value.maxDepth} 层`)
  }
  if (truncated.value) parts.push(`框过多，只绘制前 ${MAX_OVERLAY_BOXES} 个`)
  return parts.join(' · ')
})

const pickBoxes = computed(() => {
  const current = scale.value
  return pickChain.value
    .map((item) => ({ key: item.key, depth: item.depth, ...scaledBox(item.node, current) }))
    .filter((box) => box.width > 0 && box.height > 0)
})

function setPickPoint(x: number, y: number): void {
  pickPoint.value = { x, y }
  const chain = chainAt(tree.value, x, y)
  const recommended = recommendNode(chain)
  pickChain.value = chain
  recommendedKey.value = recommended?.key ?? null
  pickKey.value = recommended?.key ?? null
  pickAttrBy.value = recommended ? (selectorsOf(recommended.node)[0]?.by ?? null) : null
}

function isPickable(item: InspectorNode): boolean {
  return item.node.visible && selectorsOf(item.node).length > 0
}

function choosePickNode(item: InspectorNode): void {
  if (!isPickable(item)) return
  pickKey.value = item.key
  const selectors = selectorsOf(item.node)
  if (!selectors.some((selector) => selector.by === pickAttrBy.value)) {
    pickAttrBy.value = selectors[0]?.by ?? null
  }
}

function setPickAttr(value: string | number | boolean | undefined): void {
  const by = String(value ?? '')
  if (by !== 'text' && by !== 'content_desc' && by !== 'resource_id') return
  pickAttrBy.value = by
}

function applyElement(): void {
  const node = pickNode.value
  const by = pickAttrBy.value
  if (!node || !by) return
  emit('pick', targetForNode(tree.value, node.node, by))
}

function applyPoint(): void {
  const point = pickPointDevice.value
  if (!point || !props.allowPoint) return
  emit('pick', { by: 'point', x: point.x, y: point.y })
}

// ---------------------------------------------------------------- 树交互 / 复制

function setDepth(value: number | number[]): void {
  maxDepthShown.value = Array.isArray(value) ? (value[0] ?? 0) : value
}

function onTreeClick(data: unknown): void {
  const item = data as InspectorNode
  if (!item || !item.key) return
  selectedKey.value = item.key
  if (item.depth > maxDepthShown.value) maxDepthShown.value = item.depth
}

function onTreeLeave(): void {
  treeHoverKey.value = null
}

const modeHint = computed(() =>
  props.mode === 'pick' ? '单击截图拾取 · 双击直接采用推荐' : '悬停看信息 · 点击选中 · 树与图联动',
)

async function copySelector(): Promise<void> {
  const node = selectedNode.value?.node
  if (!node) return
  const ok = await copyText(selectorText(node))
  if (ok) ElMessage.success(`已复制：${selectorText(node)}`)
  else ElMessage.error('复制失败，请手动选择文本')
}

async function copyCenter(): Promise<void> {
  const node = selectedNode.value?.node
  if (!node) return
  const [x1, y1, x2, y2] = (node.bounds ?? [0, 0, 0, 0]) as number[]
  const text = `${Math.round((x1 + x2) / 2)}, ${Math.round((y1 + y2) / 2)}`
  const ok = await copyText(text)
  if (ok) ElMessage.success(`已复制中心坐标：${text}`)
  else ElMessage.error('复制失败，请手动选择文本')
}
</script>

<template>
  <div class="screen-inspector">
    <div class="inspector-bar">
      <el-button size="small" :icon="Refresh" :loading="loading" @click="emit('refresh')">刷新</el-button>
      <span class="bar-divider" aria-hidden />
      <span class="bar-label">叠加到</span>
      <el-slider
        class="depth-slider"
        :model-value="maxDepthShown"
        :min="0"
        :max="sliderMax"
        :step="1"
        :show-tooltip="false"
        size="small"
        :disabled="tree.maxDepth < 1"
        @update:model-value="setDepth"
      />
      <span class="bar-value">{{ maxDepthShown }}/{{ tree.maxDepth }} 层</span>
      <el-checkbox
        :model-value="clickableOnly"
        size="small"
        @update:model-value="clickableOnly = Boolean($event)"
      >
        只看可点击
      </el-checkbox>
      <el-checkbox :model-value="showLabels" size="small" @update:model-value="showLabels = Boolean($event)">
        标签
      </el-checkbox>
      <span class="spacer" />
      <span class="hint">{{ modeHint }}</span>
    </div>

    <div class="inspector-body">
      <div class="inspector-left">
        <section class="inspector-stage" :class="{ 'is-pick': mode === 'pick' }">
          <el-alert
            v-if="error"
            type="error"
            :closable="false"
            show-icon
            title="截图 / 层级获取失败"
            :description="error"
          />
          <el-skeleton v-else-if="!imageUrl && loading" :rows="6" animated />
          <el-empty v-else-if="!imageUrl" description="暂无截图" :image-size="64" />
          <div v-else ref="canvasRef" class="inspector-canvas">
            <img :src="imageUrl" alt="当前设备屏幕截图" @load="onImageLoad" />
            <svg
              v-if="pngWidth > 0 && pngHeight > 0"
              class="inspector-overlay"
              :viewBox="`0 0 ${pngWidth} ${pngHeight}`"
              preserveAspectRatio="none"
              aria-hidden="true"
              @pointermove="onPointerMove"
              @pointerleave="onPointerLeave"
              @click="onOverlayClick"
              @dblclick="onOverlayDblClick"
            >
              <rect
                v-for="box in overlayBoxes"
                :key="box.key"
                :x="box.x"
                :y="box.y"
                :width="box.width"
                :height="box.height"
                :stroke="depthColor(box.depth)"
                :stroke-opacity="!box.visible ? 0.2 : box.clickable ? 0.9 : 0.42"
                :fill="box.clickable ? depthColor(box.depth) : 'none'"
                :fill-opacity="0.07"
                stroke-width="1"
                vector-effect="non-scaling-stroke"
              />
              <g v-if="pickBoxes.length">
                <rect
                  v-for="box in pickBoxes"
                  :key="`pick-${box.key}`"
                  :x="box.x"
                  :y="box.y"
                  :width="box.width"
                  :height="box.height"
                  :stroke="depthColor(box.depth)"
                  :stroke-opacity="0.85"
                  :fill="depthColor(box.depth)"
                  fill-opacity="0.09"
                  stroke-width="1.5"
                  vector-effect="non-scaling-stroke"
                />
              </g>
              <rect
                v-if="hoveredBox"
                :x="hoveredBox.x"
                :y="hoveredBox.y"
                :width="hoveredBox.width"
                :height="hoveredBox.height"
                stroke="var(--accent)"
                fill="var(--accent)"
                fill-opacity="0.1"
                stroke-width="2"
                vector-effect="non-scaling-stroke"
              />
              <rect
                v-if="selectedBox"
                :x="selectedBox.x"
                :y="selectedBox.y"
                :width="selectedBox.width"
                :height="selectedBox.height"
                stroke="var(--accent)"
                fill="none"
                stroke-width="2.5"
                stroke-dasharray="6 3"
                vector-effect="non-scaling-stroke"
              />
              <template v-if="pickPoint">
                <line
                  :x1="pickPoint.x"
                  :y1="0"
                  :x2="pickPoint.x"
                  :y2="pngHeight"
                  stroke="var(--accent)"
                  stroke-opacity="0.55"
                  stroke-width="1"
                  stroke-dasharray="4 4"
                  vector-effect="non-scaling-stroke"
                />
                <line
                  :x1="0"
                  :y1="pickPoint.y"
                  :x2="pngWidth"
                  :y2="pickPoint.y"
                  stroke="var(--accent)"
                  stroke-opacity="0.55"
                  stroke-width="1"
                  stroke-dasharray="4 4"
                  vector-effect="non-scaling-stroke"
                />
              </template>
            </svg>
            <div v-if="labelItems.length" class="inspector-labels">
              <span
                v-for="item in labelItems"
                :key="item.key"
                class="inspector-label"
                :style="item.style"
                :title="item.text"
              >
                {{ item.text }}
              </span>
            </div>
            <div v-if="tipNode && pointer" class="inspector-tip" :style="tipStyle">
              <div class="tip-head">
                <span class="tip-class">{{ shortClass(tipNode.node.class_name) }}</span>
                <span class="tip-depth">L{{ tipNode.depth }}</span>
              </div>
              <div class="tip-grid">
                <template v-if="tipNode.node.text">
                  <span>text</span><b>{{ tipNode.node.text }}</b>
                </template>
                <template v-if="tipNode.node.resource_id">
                  <span>id</span><b>{{ tipNode.node.resource_id }}</b>
                </template>
                <template v-if="tipNode.node.content_desc">
                  <span>desc</span><b>{{ tipNode.node.content_desc }}</b>
                </template>
                <template v-if="tipNode.node.package">
                  <span>pkg</span><b>{{ tipNode.node.package }}</b>
                </template>
                <span>bounds</span><b>{{ boundsText(tipNode.node) }}</b> <span>序号</span
                ><b>#{{ tipNode.order }}</b> <span>状态</span><b>{{ tipFlags }}</b> <span>选择器</span
                ><b>{{ selectorText(tipNode.node) }}</b>
              </div>
              <div v-if="mode === 'pick'" class="tip-foot">单击拾取 · 双击采用推荐</div>
            </div>
          </div>
        </section>

        <div class="inspector-status">
          <span>{{ statusLine }}</span>
          <span v-if="pointerDevice" class="status-point"
            >· ({{ pointerDevice.x }}, {{ pointerDevice.y }})</span
          >
          <span v-else-if="pngWidth > 0" class="muted">· 鼠标移到截图上查看元素</span>
        </div>
      </div>

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
            @node-click="onTreeClick"
          >
            <template #default="{ data }">
              <div
                class="tree-row"
                :class="{
                  'is-hover': hoverKey === data.key,
                  'is-selected': selectedKey === data.key,
                  'is-invisible': !data.node.visible,
                }"
                @mouseenter="treeHoverKey = data.key"
                @mouseleave="onTreeLeave"
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
          <div v-if="!pickPoint" class="pick-empty">
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
              <el-button size="small" type="primary" plain :icon="Location" @click="applyPoint"
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
                @click="choosePickNode(item)"
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
                  @update:model-value="setPickAttr"
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
              <el-button type="primary" size="small" :icon="Aim" @click="applyElement">使用此元素</el-button>
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
    </div>
  </div>
</template>

<style scoped>
.screen-inspector {
  display: flex;
  flex-direction: column;
  gap: 8px;
  min-width: 0;
}

.inspector-bar {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
  padding: 2px 0;
}

.bar-divider {
  width: 1px;
  height: 16px;
  background: var(--line);
}

.bar-label,
.bar-value {
  color: var(--muted);
  font-size: 11px;
  white-space: nowrap;
}

.bar-value {
  min-width: 58px;
  font-family: var(--mono);
  color: var(--muted-2);
}

.depth-slider {
  width: 116px;
  margin: 0 2px;
}

.inspector-body {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 320px;
  gap: 10px;
  align-items: start;
  min-width: 0;
}

.inspector-left {
  display: flex;
  flex-direction: column;
  gap: 6px;
  min-width: 0;
}

.inspector-stage {
  display: flex;
  align-items: flex-start;
  justify-content: center;
  min-height: 220px;
  padding: 8px;
  border: 1px solid var(--line);
  border-radius: 8px;
  background: var(--surface-3);
  overflow: auto;
}

.inspector-canvas {
  position: relative;
  display: inline-block;
  max-width: 100%;
  overflow: hidden;
  border-radius: 4px;
  background: #000;
  box-shadow: 0 1px 3px rgb(0 0 0 / 18%);
}

.inspector-canvas img {
  display: block;
  max-width: 100%;
  max-height: 62vh;
  user-select: none;
}

.inspector-overlay {
  position: absolute;
  inset: 0;
  width: 100%;
  height: 100%;
  cursor: crosshair;
  touch-action: none;
}

.inspector-stage.is-pick .inspector-overlay {
  cursor: crosshair;
}

.inspector-labels {
  position: absolute;
  inset: 0;
  pointer-events: none;
}

.inspector-label {
  position: absolute;
  max-width: 180px;
  padding: 1px 4px;
  border-radius: 3px;
  background: rgb(15 23 42 / 78%);
  color: #f8fafc;
  font-size: 10px;
  line-height: 1.3;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.inspector-tip {
  position: absolute;
  z-index: 3;
  width: max-content;
  max-width: 340px;
  padding: 7px 9px;
  border: 1px solid var(--line);
  border-radius: 7px;
  background: var(--surface);
  box-shadow: 0 6px 20px rgb(15 23 42 / 22%);
  pointer-events: none;
  font-size: 10.5px;
  line-height: 1.45;
}

.tip-head {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-bottom: 4px;
}

.tip-class {
  color: var(--ink);
  font-family: var(--mono);
  font-size: 11px;
  font-weight: 650;
}

.tip-depth {
  padding: 0 4px;
  border-radius: 4px;
  background: var(--surface-3);
  color: var(--muted);
  font-family: var(--mono);
  font-size: 9.5px;
}

.tip-grid {
  display: grid;
  grid-template-columns: 46px minmax(0, 1fr);
  gap: 1px 6px;
}

.tip-grid > span {
  color: var(--muted-2);
}

.tip-grid > b {
  max-width: 260px;
  overflow: hidden;
  color: var(--ink);
  font-weight: 500;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.tip-foot {
  margin-top: 5px;
  padding-top: 4px;
  border-top: 1px solid var(--line-soft);
  color: var(--accent);
}

.inspector-status {
  display: flex;
  align-items: center;
  gap: 4px;
  flex-wrap: wrap;
  color: var(--muted);
  font-size: 10.5px;
}

.status-point {
  color: var(--accent);
  font-family: var(--mono);
}

.status-warn {
  color: var(--amber);
}

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

:deep(.el-tree-node__content) {
  height: 24px;
  border-radius: 5px;
}

:deep(.el-tree-node__content:hover) {
  background: var(--surface-3);
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

@media (max-width: 980px) {
  .inspector-body {
    grid-template-columns: minmax(0, 1fr);
  }

  .inspector-side {
    max-height: none;
  }
}
</style>
