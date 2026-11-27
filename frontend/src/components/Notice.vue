<script setup lang="ts">
// 统一状态提示块：info / success / warning / danger 四级，block / compact 两种密度。
// 以 el-alert 为底座，保留其 role/图标/关闭按钮和无障碍行为；页面/卡片用 block，
// 字段旁用 compact。黄色只表示“注意/风险”，红色只表示“失败/阻断”。
import { computed } from 'vue'

export type NoticeSeverity = 'info' | 'success' | 'warning' | 'danger'
export type NoticeVariant = 'block' | 'compact'

const props = withDefaults(
  defineProps<{
    severity?: NoticeSeverity
    variant?: NoticeVariant
    title?: string
    closable?: boolean
    icon?: boolean
  }>(),
  { severity: 'info', variant: 'block', title: '', closable: false, icon: true },
)

const emit = defineEmits<{ close: [] }>()

// el-alert 的类型叫 error；对外统一用 danger，避免“红色=错误”和“危险”两套词。
const alertType = computed(() => (props.severity === 'danger' ? 'error' : props.severity))
</script>

<template>
  <el-alert
    :type="alertType"
    :title="title || undefined"
    :closable="closable"
    :show-icon="icon"
    :class="['app-notice', `app-notice--${severity}`, `app-notice--${variant}`]"
    :data-severity="severity"
    :data-variant="variant"
    @close="emit('close')"
  >
    <div v-if="$slots.default" class="app-notice-body">
      <slot />
      <div v-if="$slots.actions" class="app-notice-actions">
        <slot name="actions" />
      </div>
    </div>
  </el-alert>
</template>

<style scoped>
.app-notice {
  align-items: flex-start;
}

.app-notice--block {
  padding: 9px 11px;
  border-radius: 7px;
}

/* Element Plus 的 info 默认是灰色；按 Carbon 的语义统一为蓝色（信息）。 */
.app-notice--info {
  --el-alert-bg-color: var(--accent-soft);
}

.app-notice--info :deep(.el-alert__icon),
.app-notice--info :deep(.el-alert__title) {
  color: var(--accent);
}

.app-notice--info :deep(.el-alert__description) {
  color: var(--accent-ink);
}

.app-notice--compact {
  padding: 5px 8px;
  border-radius: 6px;
}

.app-notice--compact :deep(.el-alert__icon) {
  font-size: 14px;
}

.app-notice--compact :deep(.el-alert__title) {
  font-size: 11px;
  line-height: 1.45;
}

.app-notice--compact :deep(.el-alert__description) {
  margin: 1px 0 0;
  font-size: 10.5px;
  line-height: 1.5;
}

.app-notice-body {
  line-height: 1.55;
}

.app-notice-actions {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-top: 4px;
  flex-wrap: wrap;
}
</style>
