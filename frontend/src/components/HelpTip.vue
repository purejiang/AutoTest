<script setup lang="ts">
// 标题右侧的“?”：只放可选的长说明、示例或链接；关键前置条件仍要放在可见的 hint 里。
import { QuestionFilled } from '@element-plus/icons-vue'

withDefaults(
  defineProps<{
    text: string
    label?: string
    placement?: 'top' | 'bottom'
  }>(),
  { label: '查看说明', placement: 'top' },
)
</script>

<template>
  <el-tooltip :content="text" :placement="placement" :show-after="120">
    <button type="button" class="help-tip" :aria-label="label">
      <el-icon :size="12"><QuestionFilled /></el-icon>
    </button>
  </el-tooltip>
</template>

<style scoped>
.help-tip {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 16px;
  height: 16px;
  padding: 0;
  border: 0;
  border-radius: 50%;
  background: transparent;
  color: var(--muted-2);
  cursor: help;
}

.help-tip:hover,
.help-tip:focus-visible {
  color: var(--accent);
  outline: none;
}

.help-tip:focus-visible {
  box-shadow: 0 0 0 2px var(--accent-soft);
}
</style>
