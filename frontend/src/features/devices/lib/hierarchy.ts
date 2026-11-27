// 层级树检查器的纯逻辑（无 Vue / 无 DOM）：
// - 把后端 HierarchyNode 精简树包成带 key/深度/文档序号的 InspectorNode；
// - 截图坐标 <-> 设备坐标的换算（截图与层级默认同一坐标系，异常时按比例兜底）；
// - 点击点 -> 祖先链（根→叶），供「按层级选元素」；
// - 节点 -> Target（by/value/index），index 的算法与后端 selectors.py 完全一致，
//   保证前端拾取出来的元素回放时能命中同一个节点。
//
// 与后端的一致性依据（backend/app/android/selectors.py）：
// - 匹配只考虑 visible 节点，按先序遍历（文档顺序）取第 index 个；
// - text / resource_id / content_desc 都是 trim 后精确相等；
// - 录制归因的选择器优先级是 text > content_desc > resource_id（recorder/convert.py）。

import type { HierarchyNode, Target, TargetBy } from '@/types/api'

/** 可作为元素定位的属性（point 是坐标，不在此列）。 */
export type SelectorBy = 'text' | 'content_desc' | 'resource_id'

/** 层级树组件（el-tree）用的精简行结构。 */
export interface InspectorTreeRow {
  key: string
  node: HierarchyNode
  depth: number
  children: InspectorTreeRow[]
}

export interface InspectorNode {
  /** 稳定的树 key：根为 '0'，子节点为 '父key-序号'。 */
  key: string
  node: HierarchyNode
  /** 层级深度（根 = 0）。 */
  depth: number
  /** 先序文档序号（含根，与后端 flatten 顺序一致）。 */
  order: number
  parentKey: string | null
  children: InspectorNode[]
}

export interface InspectorTree {
  roots: InspectorNode[]
  /** 先序展开（含根），顺序与后端 hierarchy.flatten 一致。 */
  flat: InspectorNode[]
  maxDepth: number
  nodeCount: number
}

export interface InspectorScale {
  /** 设备坐标 -> 截图坐标的比例；1 表示截图与层级同尺寸。 */
  sx: number
  sy: number
  /** 截图原始像素尺寸（未加载时为 0）。 */
  width: number
  height: number
}

export interface InspectorBox {
  x: number
  y: number
  width: number
  height: number
}

export const EMPTY_TREE: InspectorTree = { roots: [], flat: [], maxDepth: 0, nodeCount: 0 }

/** 选择器候选顺序：与录制归因一致（文本最可读、最稳定）。 */
export const SELECTOR_ORDER: readonly SelectorBy[] = ['text', 'content_desc', 'resource_id']

const SELECTOR_FIELD: Record<SelectorBy, keyof HierarchyNode> = {
  text: 'text',
  content_desc: 'content_desc',
  resource_id: 'resource_id',
}

// ---------------------------------------------------------------- 建树

export function buildInspectorTree(root: HierarchyNode | null | undefined): InspectorTree {
  if (!root || typeof root !== 'object') return EMPTY_TREE
  const flat: InspectorNode[] = []
  let maxDepth = 0

  const build = (
    node: HierarchyNode,
    key: string,
    depth: number,
    parentKey: string | null,
  ): InspectorNode => {
    const item: InspectorNode = { key, node, depth, order: flat.length, parentKey, children: [] }
    flat.push(item)
    if (depth > maxDepth) maxDepth = depth
    const children = Array.isArray(node.children) ? node.children : []
    item.children = children.map((child, index) => build(child, `${key}-${index}`, depth + 1, key))
    return item
  }

  const roots = [build(root, '0', 0, null)]
  return { roots, flat, maxDepth, nodeCount: flat.length }
}

/** 按最大深度截取的 flat（用于「显示到第 N 层」）。 */
export function nodesWithinDepth(tree: InspectorTree, maxDepth: number): InspectorNode[] {
  if (maxDepth >= tree.maxDepth) return tree.flat
  return tree.flat.filter((item) => item.depth <= maxDepth)
}

// ---------------------------------------------------------------- 几何

export function boundsOf(node: HierarchyNode): [number, number, number, number] {
  const raw = Array.isArray(node.bounds) ? node.bounds : []
  const x1 = Number(raw[0]) || 0
  const y1 = Number(raw[1]) || 0
  const x2 = Number(raw[2]) || 0
  const y2 = Number(raw[3]) || 0
  return [x1, y1, x2, y2]
}

export function areaOf(node: HierarchyNode): number {
  const [x1, y1, x2, y2] = boundsOf(node)
  return Math.max(0, x2 - x1) * Math.max(0, y2 - y1)
}

export function containsPoint(node: HierarchyNode, x: number, y: number): boolean {
  const [x1, y1, x2, y2] = boundsOf(node)
  return x2 > x1 && y2 > y1 && x >= x1 && x <= x2 && y >= y1 && y <= y2
}

/**
 * 截图与层级的比例：默认 1:1（screencap 与 uiautomator 同坐标系）；
 * 仅当层级范围超出截图尺寸（缩放过截图 / FakeDriver 的 1×1 PNG）时按比例缩小。
 */
export function computeScale(tree: InspectorTree, pngWidth: number, pngHeight: number): InspectorScale {
  let maxX = 0
  let maxY = 0
  for (const { node } of tree.flat) {
    const [, , x2, y2] = boundsOf(node)
    if (x2 > maxX) maxX = x2
    if (y2 > maxY) maxY = y2
  }
  const sx = maxX > pngWidth && pngWidth > 0 ? pngWidth / maxX : 1
  const sy = maxY > pngHeight && pngHeight > 0 ? pngHeight / maxY : 1
  return { sx, sy, width: pngWidth, height: pngHeight }
}

export function scaledBox(node: HierarchyNode, scale: InspectorScale): InspectorBox {
  const [x1, y1, x2, y2] = boundsOf(node)
  return {
    x: x1 * scale.sx,
    y: y1 * scale.sy,
    width: Math.max(0, (x2 - x1) * scale.sx),
    height: Math.max(0, (y2 - y1) * scale.sy),
  }
}

// ---------------------------------------------------------------- 命中链

/**
 * 点击点所在的祖先链（根→叶）。
 *
 * 同一层有多个节点同时包含该点时取**面积最小**的（更具体）；面积相同取文档顺序靠后的
 * （uiautomator 里后绘制的在上层）。
 */
export function chainAt(tree: InspectorTree, x: number, y: number): InspectorNode[] {
  const chain: InspectorNode[] = []
  let level: InspectorNode[] = tree.roots
  for (;;) {
    let best: InspectorNode | null = null
    let bestArea = Number.POSITIVE_INFINITY
    for (const item of level) {
      if (!containsPoint(item.node, x, y)) continue
      const area = areaOf(item.node)
      if (area <= bestArea) {
        best = item
        bestArea = area
      }
    }
    if (!best) break
    chain.push(best)
    level = best.children
  }
  return chain
}

// ---------------------------------------------------------------- 选择器 / Target

/** 节点上可用的定位属性（text > content_desc > resource_id）。 */
export function selectorsOf(node: HierarchyNode): Array<{ by: SelectorBy; value: string }> {
  const result: Array<{ by: SelectorBy; value: string }> = []
  for (const by of SELECTOR_ORDER) {
    const value = String((node[SELECTOR_FIELD[by]] as string | undefined) ?? '').trim()
    if (value) result.push({ by, value })
  }
  return result
}

export function valueOf(node: HierarchyNode, by: SelectorBy): string {
  const raw = node[SELECTOR_FIELD[by]] as string | undefined
  return typeof raw === 'string' ? raw : ''
}

function matches(nodes: HierarchyNode, by: SelectorBy, wanted: string): boolean {
  const current = (nodes[SELECTOR_FIELD[by]] as string | undefined) ?? ''
  return String(current).trim() === wanted
}

/**
 * 与后端 `selectors.select_node` 相同的 index 计算：只看 visible 节点，
 * 按先序遍历把同选择器命中依次编号，返回该节点在其中的 0 基序号。
 */
export function matchIndex(tree: InspectorTree, node: HierarchyNode, by: SelectorBy, value: string): number {
  const wanted = value.trim()
  if (!wanted) return 0
  let index = 0
  for (const item of tree.flat) {
    const current = item.node
    if (!current.visible) continue
    if (!matches(current, by, wanted)) continue
    if (current === node) return index
    index += 1
  }
  return 0
}

/** 节点 -> Target；index 由整棵树重新计算（同文本/同 id 的第几个）。 */
export function targetForNode(
  tree: InspectorTree,
  node: HierarchyNode,
  by: SelectorBy,
  value?: string,
): Target {
  const chosen = value ?? valueOf(node, by)
  return { by: by as TargetBy, value: chosen, index: matchIndex(tree, node, by, chosen) }
}

/** 拾取推荐：先找最深层「有可读文本」的节点，再找可点击节点，最后退到任意可选节点。 */
export function recommendNode(chain: InspectorNode[]): InspectorNode | null {
  const usable = chain.filter((item) => item.node.visible && selectorsOf(item.node).length > 0)
  if (usable.length === 0) return null
  const readable = usable.filter((item) => {
    const text = String(item.node.text ?? '').trim()
    const desc = String(item.node.content_desc ?? '').trim()
    return Boolean(text || desc)
  })
  const clickable = usable.filter((item) => item.node.clickable)
  const pool = readable.length > 0 ? readable : clickable.length > 0 ? clickable : usable
  return pool[pool.length - 1]
}

// ---------------------------------------------------------------- 展示

export function shortClass(class_name: string): string {
  const name = String(class_name ?? '').trim()
  if (!name) return 'node'
  const short = name.split('.').pop() ?? name
  return short || name
}

/** 树行 / tooltip 的短标题：`[Button] 登录 #btn_login`。 */
export function labelOf(node: HierarchyNode, maxLength = 64): string {
  const parts: string[] = [`[${shortClass(node.class_name)}]`]
  const text = String(node.text ?? '').trim()
  const desc = String(node.content_desc ?? '').trim()
  const id =
    String(node.resource_id ?? '')
      .trim()
      .split('/')
      .pop() ?? ''
  if (text) parts.push(text)
  else if (desc) parts.push(`~${desc}`)
  if (id) parts.push(`#${id}`)
  const label = parts.join(' ')
  return label.length > maxLength ? `${label.slice(0, maxLength - 1)}…` : label
}

export function boundsText(node: HierarchyNode): string {
  const [x1, y1, x2, y2] = boundsOf(node)
  return `[${x1},${y1}][${x2},${y2}]`
}

/** 浏览模式的「复制选择器」文本：沿用层级抽屉原有优先级（resource_id → text → content_desc → class_name）。 */
export function selectorText(node: HierarchyNode): string {
  const id = String(node.resource_id ?? '').trim()
  if (id) return `resource_id=${id}`
  const text = String(node.text ?? '').trim()
  if (text) return `text=${text}`
  const desc = String(node.content_desc ?? '').trim()
  if (desc) return `content_desc=${desc}`
  return `class_name=${String(node.class_name ?? '').trim()}`
}

export function centerOf(node: HierarchyNode): { x: number; y: number } {
  const [x1, y1, x2, y2] = boundsOf(node)
  return { x: Math.round((x1 + x2) / 2), y: Math.round((y1 + y2) / 2) }
}

/** 深度色：图中描边、树行色条、拾取链三处共用，保证「同层同色」。 */
const DEPTH_HUES = [205, 145, 32, 275, 330, 178, 96, 248, 12, 300]

export function depthColor(depth: number): string {
  const hue = DEPTH_HUES[((depth % DEPTH_HUES.length) + DEPTH_HUES.length) % DEPTH_HUES.length]
  return `hsl(${hue} 68% 55%)`
}
