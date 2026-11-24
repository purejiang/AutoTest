<script setup lang="ts">
import { InfoFilled } from '@element-plus/icons-vue'

withDefaults(
  defineProps<{
    /** 重启对象；标题已经限定设置范围，这里只补生效方式。 */
    target?: '后端' | '镜像'
    detail: string
  }>(),
  { target: '后端' },
)
</script>

<template>
  <el-tooltip :content="detail" placement="top">
    <span class="card-hint restart-hint" tabindex="0" :aria-label="`重启${target}生效，查看说明`">
      <el-icon><InfoFilled /></el-icon>
      重启{{ target }}生效
    </span>
  </el-tooltip>
</template>

<style scoped>
.restart-hint {
  display: inline-flex;
  align-items: center;
  gap: 4px;
}

.restart-hint .el-icon {
  color: var(--accent);
  font-size: 12px;
}

.restart-hint:focus-visible {
  outline: 2px solid var(--accent);
  outline-offset: 2px;
  border-radius: 4px;
}
</style>
