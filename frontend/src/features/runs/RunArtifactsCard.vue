<script setup lang="ts">
import { computed } from 'vue'

import type { Artifact } from '@/types/api'
import { artifactHref, artifactKindMeta, formatBytes } from '@/lib/format'
import StatusTag from '@/components/StatusTag.vue'

const props = defineProps<{ artifacts: Artifact[] }>()

const screenshots = computed(() => props.artifacts.filter((artifact) => artifact.kind === 'screenshot'))
const videos = computed(() =>
  props.artifacts.filter((artifact) => artifact.kind === 'video' || artifact.kind === 'screenrecord'),
)
const logs = computed(() =>
  props.artifacts.filter((artifact) => ['logcat', 'hierarchy', 'network'].includes(artifact.kind)),
)
const others = computed(() =>
  props.artifacts.filter(
    (artifact) =>
      !screenshots.value.includes(artifact) && !videos.value.includes(artifact) && !logs.value.includes(artifact),
  ),
)

function nameOf(artifact: Artifact): string {
  return artifact.name || artifact.path.split(/[\\/]/).pop() || String(artifact.id)
}

function hrefOf(artifact: Artifact): string | null {
  return artifactHref(artifact)
}
</script>

<template>
  <div>
    <el-empty v-if="artifacts.length === 0" description="暂无产物" :image-size="48" />
    <div v-else class="stack" style="gap: 10px">
      <div v-if="screenshots.length > 0">
        <div class="hint">截图（{{ screenshots.length }}）</div>
        <div class="artifact-grid" style="margin-top: 6px">
          <div v-for="artifact in screenshots" :key="artifact.id" class="artifact-card">
            <a v-if="hrefOf(artifact)" :href="hrefOf(artifact) ?? undefined" target="_blank" rel="noreferrer">
              <div class="artifact-thumb">
                <img :src="hrefOf(artifact) ?? undefined" :alt="nameOf(artifact)" loading="lazy" />
              </div>
            </a>
            <div v-else class="artifact-thumb">无直链</div>
            <div class="artifact-name" :title="artifact.path">{{ nameOf(artifact) }}</div>
          </div>
        </div>
      </div>

      <div v-for="group in [
        { key: 'video', label: '视频 / 录屏', items: videos },
        { key: 'log', label: '日志 / 层级', items: logs },
        { key: 'other', label: '其他', items: others },
      ]" :key="group.key">
        <template v-if="group.items.length > 0">
          <div class="hint">{{ group.label }}（{{ group.items.length }}）</div>
          <div v-for="artifact in group.items" :key="artifact.id" class="artifact-row">
            <StatusTag :tone="artifactKindMeta(artifact.kind).tone">{{ artifactKindMeta(artifact.kind).label }}</StatusTag>
            <span class="name" :title="artifact.path">{{ nameOf(artifact) }}</span>
            <span class="size">{{ formatBytes(artifact.size) }}</span>
            <a v-if="hrefOf(artifact)" :href="hrefOf(artifact) ?? undefined" target="_blank" rel="noreferrer">查看</a>
            <span v-else class="muted small">无直链</span>
          </div>
        </template>
      </div>
    </div>
  </div>
</template>
