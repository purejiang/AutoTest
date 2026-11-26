<script setup lang="ts">
defineProps<{
  title: string
  subtitle?: string
  count?: number | string
}>()

defineEmits<{ back: [] }>()
</script>

<template>
  <div class="page-head">
    <div class="page-head-main">
      <div class="page-title">
        <button v-if="$slots.back" class="icon-btn" type="button" @click="$emit('back')">
          <slot name="back" />
        </button>
        <slot name="title">
          <h1>{{ title }}</h1>
        </slot>
        <span v-if="count !== undefined" class="count-pill">{{ count }}</span>
        <span v-if="subtitle" class="page-sub">{{ subtitle }}</span>
        <slot name="title-extra" />
      </div>
      <slot name="below-title" />
    </div>
    <div v-if="$slots.actions" class="page-actions">
      <slot name="actions" />
    </div>
  </div>
</template>

<style scoped>
.page-head-main {
  display: flex;
  flex-direction: column;
  flex: 1;
  gap: 4px;
  min-width: 0;
}
</style>
