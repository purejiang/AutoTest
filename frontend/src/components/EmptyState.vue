<script setup lang="ts">
// 页面/卡片级空态：图标 + 标题 + 说明 + 操作 + 附加内容。
// 行内轻提示（菜单空、表格内一行“暂无”）不要用它，保持轻量。
import type { Component } from 'vue'

withDefaults(
  defineProps<{
    title: string
    description?: string
    icon?: Component
    compact?: boolean
  }>(),
  { description: '', icon: undefined, compact: false },
)
</script>

<template>
  <div :class="['empty-state', { 'is-compact': compact }]">
    <el-icon v-if="icon" :size="compact ? 24 : 34" class="empty-state__icon">
      <component :is="icon" />
    </el-icon>
    <strong class="empty-state__title">{{ title }}</strong>
    <div v-if="description || $slots.default" class="empty-state__desc">
      <slot>{{ description }}</slot>
    </div>
    <slot name="extra" />
    <div v-if="$slots.actions" class="empty-state__actions">
      <slot name="actions" />
    </div>
  </div>
</template>

<style scoped>
.empty-state {
  display: flex;
  flex: 1;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 9px;
  min-height: 0;
  padding: 28px;
  text-align: center;
}

.empty-state.is-compact {
  gap: 8px;
  padding: 28px 20px;
}

.empty-state__icon {
  color: var(--muted-2);
}

.empty-state__title {
  color: var(--ink);
  font-size: 14px;
  font-weight: 600;
}

.empty-state.is-compact .empty-state__title {
  font-size: 12.5px;
}

.empty-state__desc {
  max-width: 360px;
  color: var(--muted);
  font-size: 11.5px;
  line-height: 1.55;
}

.empty-state__desc :deep(p) {
  margin: 0;
}

.empty-state.is-compact .empty-state__desc {
  max-width: 260px;
  font-size: 11px;
}

.empty-state__actions {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 7px;
  flex-wrap: wrap;
  margin-top: 3px;
}

@media (max-width: 640px) {
  .empty-state {
    padding: 20px 14px;
  }
}
</style>
