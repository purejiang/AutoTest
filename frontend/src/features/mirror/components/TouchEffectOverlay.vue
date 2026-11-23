<script setup lang="ts">
// 镜像触控特效覆盖层：绝对定位在镜像 canvas 正上方，只负责坐标换算与尺寸同步。
// 渲染逻辑见 ../render/touchEffectRenderer.ts。

import { onBeforeUnmount, onMounted, ref, watch } from 'vue'

import { TouchEffectRenderer } from '../render/touchEffectRenderer'

const props = withDefaults(
  defineProps<{
    /** 关闭后立即清空覆盖层，后续 pointer 事件不再绘制 */
    enabled?: boolean
    /** 点击特效：按下触点、长按呼吸环（波纹另由 rippleEnabled 控制） */
    tapEnabled?: boolean
    /** 滑动特效：拖动轨迹与跟随终点圆点 */
    swipeEnabled?: boolean
    /** 只控制按下/松手的扩散波纹；关闭后仍保留触点和滑动轨迹 */
    rippleEnabled?: boolean
    /** 需要对齐的镜像 canvas（父组件的 canvasRef） */
    target?: HTMLCanvasElement | null
  }>(),
  { enabled: true, tapEnabled: true, swipeEnabled: true, rippleEnabled: true, target: null },
)

const overlayRef = ref<HTMLCanvasElement | null>(null)
let renderer: TouchEffectRenderer | null = null
let mirrored: HTMLCanvasElement | null = null
let resizeObserver: ResizeObserver | null = null
let windowListenersBound = false
// 一次手势内复用 pointerdown 时量到的覆盖层位置，避免每次 pointermove 都强制布局。
let pointerOrigin: { left: number; top: number } | null = null

function refreshLayout(): void {
  const overlay = overlayRef.value
  const stage = overlay?.parentElement
  if (!overlay || !stage || !mirrored) return
  const stageRect = stage.getBoundingClientRect()
  const targetRect = mirrored.getBoundingClientRect()
  const width = mirrored.clientWidth
  const height = mirrored.clientHeight
  if (width <= 0 || height <= 0) {
    overlay.style.display = 'none'
    pointerOrigin = null
    return
  }
  overlay.style.display = 'block'
  overlay.style.left = `${targetRect.left - stageRect.left - stage.clientLeft}px`
  overlay.style.top = `${targetRect.top - stageRect.top - stage.clientTop}px`
  overlay.style.width = `${width}px`
  overlay.style.height = `${height}px`
  renderer?.resize(width, height)
  pointerOrigin = null
}

function bindTarget(target: HTMLCanvasElement | null): void {
  resizeObserver?.disconnect()
  resizeObserver = null
  mirrored = target
  if (!target) return
  if (typeof ResizeObserver !== 'undefined') {
    resizeObserver = new ResizeObserver(() => refreshLayout())
    resizeObserver.observe(target)
  }
  refreshLayout()
}

function localPoint(clientX: number, clientY: number, fresh: boolean): { x: number; y: number } | null {
  if (fresh || !pointerOrigin) {
    const rect = overlayRef.value?.getBoundingClientRect()
    if (!rect || rect.width <= 0 || rect.height <= 0) return null
    pointerOrigin = { left: rect.left, top: rect.top }
  }
  return { x: clientX - pointerOrigin.left, y: clientY - pointerOrigin.top }
}

function down(clientX: number, clientY: number): void {
  const point = localPoint(clientX, clientY, true)
  if (point) renderer?.down(point.x, point.y)
}

function move(clientX: number, clientY: number): void {
  const point = localPoint(clientX, clientY, false)
  if (point) renderer?.move(point.x, point.y)
}

function up(clientX: number, clientY: number): void {
  const point = localPoint(clientX, clientY, false)
  if (point) renderer?.up(point.x, point.y)
  pointerOrigin = null
}

function cancel(): void {
  renderer?.cancel()
  pointerOrigin = null
}

function clear(): void {
  renderer?.clear()
  pointerOrigin = null
}

onMounted(() => {
  const canvas = overlayRef.value
  if (!canvas) return
  renderer = new TouchEffectRenderer(canvas)
  renderer.setEnabled(props.enabled)
  renderer.setTapEnabled(props.tapEnabled)
  renderer.setSwipeEnabled(props.swipeEnabled)
  renderer.setRippleEnabled(props.rippleEnabled)
  bindTarget(props.target)
  window.addEventListener('resize', refreshLayout)
  windowListenersBound = true
})

watch(
  () => props.target,
  (target) => bindTarget(target),
)

watch(
  () => props.enabled,
  (enabled) => {
    renderer?.setEnabled(enabled)
    if (!enabled) pointerOrigin = null
  },
)

watch(
  () => props.tapEnabled,
  (tapEnabled) => renderer?.setTapEnabled(tapEnabled),
)

watch(
  () => props.swipeEnabled,
  (swipeEnabled) => renderer?.setSwipeEnabled(swipeEnabled),
)

watch(
  () => props.rippleEnabled,
  (rippleEnabled) => renderer?.setRippleEnabled(rippleEnabled),
)

onBeforeUnmount(() => {
  resizeObserver?.disconnect()
  resizeObserver = null
  if (windowListenersBound) {
    window.removeEventListener('resize', refreshLayout)
    windowListenersBound = false
  }
  renderer?.destroy()
  renderer = null
})

defineExpose({
  down,
  move,
  up,
  cancel,
  clear,
  /** 供浏览器端视频录制合成特效层用 */
  getCanvas: () => overlayRef.value,
})
</script>

<template>
  <canvas ref="overlayRef" class="mirror-touch-effect" aria-hidden="true" />
</template>
