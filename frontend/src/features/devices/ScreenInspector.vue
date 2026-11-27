<script setup lang="ts">
// 截图 + 层级叠加检查器：一张当前屏幕截图，上面叠加各层元素框。
// - browse 模式：悬停看信息、右侧全量层级树与图联动、点击复制选择器（手动控制「层级树」用它）；
// - pick 模式：点击位置后按「祖先链」列出该点所在的每一层，选元素或坐标回填 Target（脚本编辑器用它）。
//
// 坐标系：screencap 与 uiautomator 同坐标系，默认 1:1；仅当层级范围超出截图尺寸时按比例缩放。
import { Aim, CopyDocument, Location, Refresh } from '@element-plus/icons-vue'
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'

import type { HierarchyNode, Target } from '@/types/api'
import ScreenInspectorSide from './inspector/ScreenInspectorSide.vue'
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
  type InspectorTreeRow,
  type SelectorBy,
} from './lib/hierarchy'

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
    /** 后端本次 dump 层级耗时（ms），用于状态行显示 */
    hierarchyMs?: number | null
  }>(),
  { mode: 'browse', allowPoint: true, loading: false, error: null, hierarchyMs: null },
)

const emit = defineEmits<{ refresh: []; pick: [target: Target] }>()

const MAX_OVERLAY_BOXES = 3000
const MAX_LABELS = 200

// ---------------------------------------------------------------- 层级数据

const tree = computed(() => buildInspectorTree(props.root))
const nodeByKey = computed(() => {
  const map = new Map<string, InspectorNode>()
  for (const item of tree.value.flat) map.set(item.key, item)
  return map
})

const maxDepthShown = ref(0)
// 用户手动调过「叠加到第 N 层」后，刷新不再把范围重置回全量（大树全量很吃渲染）。
const depthChosen = ref(false)
const clickableOnly = ref(false)
const showLabels = ref(false)

const sliderMax = computed(() => Math.max(tree.value.maxDepth, 1))

const filteredNodes = computed(() => {
  const list = nodesWithinDepth(tree.value, maxDepthShown.value)
  return clickableOnly.value ? list.filter((item) => item.node.clickable) : list
})

const truncated = computed(() => filteredNodes.value.length > MAX_OVERLAY_BOXES)

const expandedKeys = computed(() => tree.value.flat.filter((item) => item.depth < 2).map((item) => item.key))

function pruneRows(items: InspectorNode[], maxDepth: number): InspectorTreeRow[] {
  return items.map((item) => ({
    key: item.key,
    node: item.node,
    depth: item.depth,
    children: item.depth < maxDepth ? pruneRows(item.children, maxDepth) : [],
  }))
}

const treeData = computed<InspectorTreeRow[]>(() => pruneRows(tree.value.roots, maxDepthShown.value))

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
  maxDepthShown.value = depthChosen.value ? Math.min(maxDepthShown.value, next.maxDepth) : next.maxDepth
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
    if (props.hierarchyMs !== null && props.hierarchyMs !== undefined) {
      parts.push(`层级 ${props.hierarchyMs}ms`)
    }
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
  depthChosen.value = true
  maxDepthShown.value = Array.isArray(value) ? (value[0] ?? 0) : value
}

function onTreeClick(data: unknown): void {
  const item = data as InspectorNode
  if (!item || !item.key) return
  selectedKey.value = item.key
  if (item.depth > maxDepthShown.value) {
    depthChosen.value = true
    maxDepthShown.value = item.depth
  }
}

function onTreeLeave(): void {
  treeHoverKey.value = null
}

const modeHint = computed(() =>
  props.mode === 'pick' ? '单击截图拾取 · 双击直接采用推荐' : '悬停看信息 · 点击选中 · 树与图联动',
)

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
                v-memo="[box.x, box.y, box.width, box.height, box.depth, box.visible, box.clickable]"
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

      <ScreenInspectorSide
        :mode="mode"
        :allow-point="allowPoint"
        :tree="tree"
        :tree-data="treeData"
        :expanded-keys="expandedKeys"
        :max-depth-shown="maxDepthShown"
        :selected-key="selectedKey"
        :hover-key="hoverKey"
        :selected-node="selectedNode"
        :selected-flags="selectedFlags"
        :has-pick-point="Boolean(pickPoint)"
        :pick-point-device="pickPointDevice"
        :pick-chain="pickChain"
        :pick-key="pickKey"
        :recommended-key="recommendedKey"
        :pick-node="pickNode"
        :pick-selectors="pickSelectors"
        :pick-attr-by="pickAttrBy"
        :pick-value="pickValue"
        :pick-index="pickIndex"
        @tree-click="onTreeClick"
        @tree-hover="treeHoverKey = $event"
        @choose-node="choosePickNode"
        @set-attr="setPickAttr"
        @apply-element="applyElement"
        @apply-point="applyPoint"
      />
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

@media (max-width: 980px) {
  .inspector-body {
    grid-template-columns: minmax(0, 1fr);
  }
}

</style>
