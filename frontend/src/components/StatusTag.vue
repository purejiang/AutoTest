<script setup lang="ts">
import type { Tone } from '@/lib/format'

withDefaults(
  defineProps<{
    tone?: Tone
    dot?: boolean
    mono?: boolean
  }>(),
  { tone: 'gray', dot: false, mono: false },
)
</script>

<template>
  <span :class="['tag', tone, { 'tag-mono': mono }]">
    <span v-if="dot" class="tag-dot" />
    <slot />
  </span>
</template>
