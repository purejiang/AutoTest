<script setup lang="ts">
// 统一 Tab 组件：受控（modelValue + update:modelValue），只负责视觉与 a11y。
// 状态来源由父级决定（路由 query / Pinia / 本地 ref），组件不读路由、不写 store。
//
// variant：
// - segmented：脚本页的胶囊切换（含计数）
// - dock：控制台右侧卡片的 2 列切换（含图标/运行徽标）
// - sidebar：设置页左侧纵向导航
// - scroll：手动控制顶部可横滚 tabs（图标 + 下划线）
// - plain：运行详情卡片头的小号文字 tab（含计数）
import type { AppTabItem } from './appTabs'

const props = withDefaults(
  defineProps<{
    modelValue: string
    items: readonly AppTabItem[]
    variant?: 'segmented' | 'dock' | 'sidebar' | 'scroll' | 'plain'
    ariaLabel?: string
  }>(),
  { variant: 'segmented', ariaLabel: '视图切换' },
)

const emit = defineEmits<{ 'update:modelValue': [key: string] }>()
</script>

<template>
  <div :class="['app-tabs', `app-tabs--${props.variant}`]" role="tablist" :aria-label="props.ariaLabel">
    <button
      v-for="item in props.items"
      :key="item.key"
      type="button"
      role="tab"
      :class="['app-tabs__tab', { active: item.key === props.modelValue }]"
      :aria-selected="item.key === props.modelValue"
      @click="emit('update:modelValue', item.key)"
    >
      <el-icon v-if="item.icon" :size="13"><component :is="item.icon" /></el-icon>
      <span class="app-tabs__label">{{ item.label }}</span>
      <span v-if="item.count !== undefined" class="app-tabs__count">{{ item.count }}</span>
      <span v-if="item.badge" :class="['app-tabs__badge', item.badge.tone ?? 'green']">
        {{ item.badge.text }}
      </span>
    </button>
  </div>
</template>

<style scoped>
.app-tabs {
  display: inline-flex;
  min-width: 0;
}

.app-tabs__tab {
  font-family: inherit;
}

/* ---------------------------------------------------------------- segmented */
.app-tabs--segmented {
  padding: 2px;
  border: 1px solid var(--line);
  border-radius: 8px;
  background: var(--surface-2);
}

.app-tabs--segmented .app-tabs__tab {
  height: 24px;
  padding: 0 10px;
  border: 0;
  border-radius: 6px;
  background: transparent;
  color: var(--muted);
  font-size: 11.5px;
  white-space: nowrap;
  cursor: pointer;
}

.app-tabs--segmented .app-tabs__tab:hover {
  color: var(--ink);
}

.app-tabs--segmented .app-tabs__tab.active {
  background: var(--tab-active-bg);
  color: var(--tab-active-ink);
  font-weight: 600;
  box-shadow: var(--tab-active-shadow);
}

.app-tabs--segmented .app-tabs__count {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-width: 16px;
  height: 16px;
  margin-left: 5px;
  padding: 0 4px;
  border-radius: 8px;
  background: var(--surface-3);
  color: var(--muted);
  font-family: var(--mono);
  font-size: 9px;
  font-weight: 600;
  line-height: 1;
}

.app-tabs--segmented .app-tabs__tab.active .app-tabs__count {
  background: var(--accent-soft);
  color: var(--accent);
}

/* ---------------------------------------------------------------- dock */
.app-tabs--dock {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 3px;
  flex: none;
  padding: 6px;
  border-bottom: 1px solid var(--line);
}

.app-tabs--dock .app-tabs__tab {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 6px;
  height: 32px;
  padding: 0 8px;
  border: 0;
  border-radius: 6px;
  background: transparent;
  color: var(--muted);
  font-size: 12px;
  white-space: nowrap;
  cursor: pointer;
}

.app-tabs--dock .app-tabs__tab:hover {
  background: var(--surface-2);
  color: var(--ink);
}

.app-tabs--dock .app-tabs__tab.active {
  background: var(--accent-soft);
  color: var(--accent);
  font-weight: 650;
}

.app-tabs--dock .app-tabs__badge {
  display: inline-flex;
  align-items: center;
  height: 16px;
  padding: 0 5px;
  border-radius: 8px;
  background: var(--green-soft);
  color: var(--green);
  font-size: 9px;
  font-weight: 700;
}

.app-tabs--dock .app-tabs__badge.amber {
  background: var(--amber-soft);
  color: var(--amber);
}

/* ---------------------------------------------------------------- scroll */
.app-tabs--scroll {
  display: flex;
  align-items: stretch;
  gap: 1px;
  flex: none;
  padding: 0 7px;
  overflow-x: auto;
  border-bottom: 1px solid var(--line);
  scrollbar-width: none;
}

.app-tabs--scroll::-webkit-scrollbar {
  display: none;
}

.app-tabs--scroll .app-tabs__tab {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  flex: none;
  height: 38px;
  padding: 0 7px;
  border: 0;
  border-bottom: 2px solid transparent;
  background: transparent;
  color: var(--muted);
  font-size: 11.5px;
  white-space: nowrap;
  cursor: pointer;
}

.app-tabs--scroll .app-tabs__tab .el-icon {
  color: var(--muted-2);
}

.app-tabs--scroll .app-tabs__tab:hover {
  background: var(--surface-2);
  color: var(--ink);
}

.app-tabs--scroll .app-tabs__tab.active {
  border-bottom-color: var(--accent);
  color: var(--accent);
  font-weight: 650;
}

.app-tabs--scroll .app-tabs__tab.active .el-icon {
  color: var(--accent);
}

/* ---------------------------------------------------------------- sidebar */
.app-tabs--sidebar {
  display: flex;
  flex-direction: column;
  width: 100%;
}

.app-tabs--sidebar .app-tabs__tab {
  display: flex;
  align-items: center;
  justify-content: space-between;
  width: 100%;
  height: 36px;
  padding: 0 10px;
  border: 0;
  border-radius: 6px;
  background: transparent;
  color: var(--muted);
  font-size: 12.5px;
  text-align: left;
  cursor: pointer;
}

.app-tabs--sidebar .app-tabs__tab:hover {
  background: var(--surface-2);
  color: var(--ink);
}

.app-tabs--sidebar .app-tabs__tab.active {
  background: var(--accent-soft);
  color: var(--accent);
  font-weight: 650;
}

/* ---------------------------------------------------------------- plain */
.app-tabs--plain {
  gap: 4px;
}

.app-tabs--plain .app-tabs__tab {
  display: inline-flex;
  align-items: center;
  gap: 2px;
  height: 26px;
  padding: 0 8px;
  border: 1px solid transparent;
  border-radius: 5px;
  background: transparent;
  color: var(--muted);
  font-size: 11.5px;
  cursor: pointer;
}

.app-tabs--plain .app-tabs__tab:hover {
  background: var(--surface-2);
  color: var(--ink);
}

.app-tabs--plain .app-tabs__tab.active {
  border-color: var(--tab-active-border);
  background: var(--tab-active-bg);
  color: var(--tab-active-ink);
  font-weight: 600;
  box-shadow: var(--tab-active-shadow);
}

.app-tabs--plain .app-tabs__count {
  display: inline-flex;
  align-items: center;
  height: 17px;
  margin-left: 4px;
  padding: 0 5px;
  border-radius: 4px;
  background: var(--accent-soft);
  color: var(--accent);
  font-size: 9.5px;
  font-weight: 600;
}

/* 设置页在窄屏把左侧导航改成横向滚动条。 */
@media (max-width: 900px) {
  .app-tabs--sidebar {
    flex-direction: row;
    width: auto;
    gap: 3px;
    overflow-x: auto;
  }

  .app-tabs--sidebar .app-tabs__tab {
    width: auto;
    flex: none;
    padding: 0 10px;
  }
}
</style>
