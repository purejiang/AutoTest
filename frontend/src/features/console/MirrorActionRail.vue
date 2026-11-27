<script setup lang="ts">
// 镜像右侧快捷键栏：返回/桌面/最近/截图 + 更多系统按键。
import {
  Back,
  Bell,
  Camera,
  ChatDotRound,
  Delete,
  Grid,
  HomeFilled,
  Menu as MenuIcon,
  MoreFilled,
  Promotion,
  TurnOff,
} from '@element-plus/icons-vue'
import { onClickOutside, useEventListener } from '@vueuse/core'
import { ref } from 'vue'

defineProps<{ locked: boolean }>()
const emit = defineEmits<{
  key: [key: string]
  screenshot: []
}>()

const MORE_KEYS = [
  { key: 'menu', label: '菜单', icon: MenuIcon },
  { key: 'notification', label: '通知', icon: Bell },
  { key: 'volume_up', label: '音量+', icon: Promotion },
  { key: 'volume_down', label: '音量−', icon: Promotion },
  { key: 'power', label: '电源', icon: TurnOff },
  { key: 'enter', label: '回车', icon: ChatDotRound },
  { key: 'delete', label: '删除', icon: Delete },
]

const menuRef = ref<HTMLDivElement | null>(null)
const menuOpen = ref(false)

onClickOutside(menuRef, () => {
  menuOpen.value = false
})
useEventListener(window, 'keydown', (event: KeyboardEvent) => {
  if (event.key === 'Escape' && menuOpen.value) {
    event.stopPropagation()
    menuOpen.value = false
  }
})

function pickKey(key: string): void {
  menuOpen.value = false
  emit('key', key)
}
</script>

<template>
<aside class="action-rail">
  <button
    type="button"
    class="rail-btn"
    title="返回"
    :disabled="locked"
    @click="emit('key', 'back')"
  >
    <el-icon :size="16"><Back /></el-icon><span>返回</span>
  </button>
  <button
    type="button"
    class="rail-btn"
    title="桌面"
    :disabled="locked"
    @click="emit('key', 'home')"
  >
    <el-icon :size="16"><HomeFilled /></el-icon><span>桌面</span>
  </button>
  <button
    type="button"
    class="rail-btn"
    title="最近任务"
    :disabled="locked"
    @click="emit('key', 'recents')"
  >
    <el-icon :size="16"><Grid /></el-icon><span>最近</span>
  </button>
  <button type="button" class="rail-btn accent" title="截图" @click="emit('screenshot')">
    <el-icon :size="16"><Camera /></el-icon><span>截图</span>
  </button>
  <div class="rail-divider" />
  <div ref="menuRef" class="more-wrap">
    <button
      type="button"
      class="rail-btn"
      :aria-expanded="menuOpen"
      title="更多系统按键"
      :disabled="locked"
      @click="menuOpen = !menuOpen"
    >
      <el-icon :size="16"><MoreFilled /></el-icon><span>更多</span>
    </button>
    <div v-if="menuOpen" class="more-menu">
      <div class="more-title">系统按键</div>
      <div class="more-grid">
        <button
          v-for="item in MORE_KEYS"
          :key="item.key"
          type="button"
          class="more-key"
          @click="pickKey(item.key)"
        >
          <el-icon :size="15"><component :is="item.icon" /></el-icon>
          {{ item.label }}
        </button>
      </div>
    </div>
  </div>
</aside></template>

<style scoped>
.action-rail {
  position: relative;
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 3px;
  overflow: visible;
  padding: 8px 0;
  border-left: 1px solid #232b36;
  background: #111820;
}

.rail-btn {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 3px;
  width: 46px;
  height: 43px;
  padding: 0;
  border: 0;
  border-radius: 7px;
  background: transparent;
  color: #8494a8;
  font-size: 9.5px;
  line-height: 1;
  cursor: pointer;
}

.rail-btn:hover:not(:disabled) {
  background: rgba(255, 255, 255, 0.07);
  color: #f0f6ff;
}

.rail-btn:disabled {
  opacity: 0.4;
  cursor: not-allowed;
}

.rail-btn.accent {
  color: #7fb0ff;
}

.rail-divider {
  width: 24px;
  height: 1px;
  margin: 3px 0;
  background: #293342;
}

.more-wrap {
  position: relative;
}

.more-menu {
  position: absolute;
  right: calc(100% + 8px);
  bottom: 0;
  z-index: 45;
  width: 238px;
  padding: 8px;
  border: 1px solid var(--line);
  border-radius: 9px;
  background: var(--surface);
  box-shadow: var(--shadow-md);
}

.more-title {
  padding: 3px 4px 6px;
  color: var(--muted-2);
  font-size: 10px;
}

.more-grid {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 4px;
}

.more-key {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 3px;
  height: 43px;
  padding: 0;
  border: 1px solid var(--line);
  border-radius: 6px;
  background: var(--surface);
  color: var(--muted);
  font-size: 9.5px;
  cursor: pointer;
}

.more-key:hover {
  border-color: var(--accent);
  background: var(--accent-soft);
  color: var(--accent);
}
@media (max-width: 640px) {
  .action-rail {
    padding: 6px 0;
  }

  .rail-btn {
    width: 42px;
    height: 40px;
    font-size: 9px;
  }

  .more-menu {
    right: calc(100% + 5px);
    width: 210px;
  }
}
</style>
