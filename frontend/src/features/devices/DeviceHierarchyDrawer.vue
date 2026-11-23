<script setup lang="ts">
import { Refresh } from '@element-plus/icons-vue'
import { ElMessage } from 'element-plus'
import { computed, ref } from 'vue'

import { describeApiError } from '@/api/client'
import { useDeviceHierarchy } from '@/api/devices'
import type { HierarchyNode } from '@/types/api'

const props = defineProps<{
  serial: string | null
  open: boolean
}>()

const emit = defineEmits<{ close: [] }>()

const raw = ref(false)
const query = useDeviceHierarchy(computed(() => props.serial), {
  raw: computed(() => raw.value),
  enabled: computed(() => props.open),
})

interface HierarchyTreeNode {
  key: string
  label: string
  selector: string
  children: HierarchyTreeNode[]
}

function shortClass(name: string): string {
  if (!name) return 'node'
  return name.split('.').pop() ?? name
}

function selectorFor(node: HierarchyNode): string {
  if (node.resource_id) return `resource_id=${node.resource_id}`
  if (node.text) return `text=${node.text}`
  if (node.content_desc) return `content_desc=${node.content_desc}`
  return `class_name=${node.class_name}`
}

function buildTree(node: HierarchyNode, key: string, depth: number): HierarchyTreeNode {
  const parts = [`[${shortClass(node.class_name)}]`]
  if (node.text) parts.push(`"${node.text}"`)
  if (node.resource_id) parts.push(`#${node.resource_id.split('/').pop()}`)
  parts.push(`[${node.bounds?.join(',') ?? ''}]`)
  return {
    key,
    label: parts.join(' '),
    selector: selectorFor(node),
    children: depth < 6 ? node.children.map((child, index) => buildTree(child, `${key}-${index}`, depth + 1)) : [],
  }
}

const treeData = computed<HierarchyTreeNode[]>(() => {
  const node = query.data.value?.node
  return node ? [buildTree(node, '0', 0)] : []
})

function collectKeys(nodes: HierarchyTreeNode[], out: string[] = []): string[] {
  for (const node of nodes) {
    if (out.length >= 400) break
    out.push(node.key)
    collectKeys(node.children, out)
  }
  return out
}

const expandedKeys = computed(() => collectKeys(treeData.value))

async function copyText(text: string): Promise<boolean> {
  try {
    if (navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(text)
      return true
    }
  } catch {
    // fall through
  }
  try {
    const textarea = document.createElement('textarea')
    textarea.value = text
    textarea.style.position = 'fixed'
    textarea.style.opacity = '0'
    document.body.appendChild(textarea)
    textarea.select()
    const ok = document.execCommand('copy')
    textarea.remove()
    return ok
  } catch {
    return false
  }
}

async function handleNodeClick(data: HierarchyTreeNode): Promise<void> {
  const ok = await copyText(data.selector)
  if (ok) ElMessage.success(`已复制：${data.selector}`)
  else ElMessage.error('复制失败，请手动选择文本')
}
</script>

<template>
  <el-drawer :model-value="open" size="920px" append-to-body @update:model-value="!$event && emit('close')">
    <template #header>
      <div class="row">
        <strong>层级树</strong>
        <span class="code-text muted">{{ serial ?? '' }}</span>
      </div>
    </template>
    <div class="row" style="margin-bottom: 8px; flex-wrap: wrap">
      <span class="hint">点击节点复制选择器（resource_id → text → content_desc → class_name）。</span>
      <span class="spacer" />
      <span class="hint">原始 XML</span>
      <el-switch v-model="raw" size="small" />
      <el-button size="small" :icon="Refresh" :loading="query.isFetching.value" @click="query.refetch()">刷新</el-button>
    </div>

    <el-skeleton v-if="query.isLoading.value" :rows="10" animated />
    <el-alert
      v-else-if="query.isError.value"
      type="error"
      :closable="false"
      show-icon
      title="层级获取失败"
      :description="describeApiError(query.error.value)"
    />
    <template v-else-if="raw">
      <pre v-if="query.data.value?.raw" class="pre-block" style="max-height: 72vh">{{ query.data.value.raw }}</pre>
      <el-alert
        v-else
        type="info"
        :closable="false"
        show-icon
        title="当前驱动不提供原始 XML"
        :description="query.data.value?.rawUnavailable ?? '后端返回 raw_unavailable'"
      />
    </template>
    <el-empty v-else-if="treeData.length === 0" description="暂无层级数据（设备可能离线或未授权 uiautomator）" />
    <el-tree
      v-else
      :data="treeData"
      node-key="key"
      :props="{ label: 'label', children: 'children' }"
      :default-expanded-keys="expandedKeys"
      :expand-on-click-node="false"
      @node-click="handleNodeClick"
    />
  </el-drawer>
</template>
