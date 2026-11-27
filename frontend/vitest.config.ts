import { fileURLToPath, URL } from 'node:url'
import vue from '@vitejs/plugin-vue'
import { defineConfig } from 'vitest/config'

// 单测只覆盖纯逻辑 / composable / 共享组件；不需要连后端，也不加载真实浏览器。
// app 版本等编译期常量在测试里给固定值，避免组件测试依赖 git/时间。
export default defineConfig({
  plugins: [vue()],
  define: {
    __APP_VERSION__: JSON.stringify('test'),
    __APP_COMMIT__: JSON.stringify('test'),
    __APP_BUILD_TIME__: JSON.stringify('2026-01-01T00:00:00.000Z'),
  },
  resolve: {
    alias: { '@': fileURLToPath(new URL('./src', import.meta.url)) },
  },
  test: {
    environment: 'happy-dom',
    include: ['src/**/*.test.ts'],
    restoreMocks: true,
    unstubGlobals: true,
  },
})
