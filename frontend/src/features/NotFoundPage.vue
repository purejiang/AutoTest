<script setup lang="ts">
import { useRouter } from 'vue-router'

const router = useRouter()
</script>

<template>
  <div class="empty-page">
    <div class="empty-code">404</div>
    <h2>页面不存在</h2>
    <p class="muted">这个地址没有对应的页面，可能已随 v0.2 结构调整而移动。</p>
    <el-button type="primary" @click="router.push('/devices')">回到设备工作台</el-button>
  </div>
</template>
