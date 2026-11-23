import { execSync } from 'node:child_process'
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

// 开发期：前端 5173 -> 后端 8787（/api 与 /ws 都代理，WS 需 ws:true）
const BACKEND = process.env.AUTOTEST_BACKEND ?? 'http://127.0.0.1:8787'

function readPackageVersion(): string {
  try {
    const pkg = JSON.parse(readFileSync(new URL('./package.json', import.meta.url), 'utf8')) as {
      version?: string
    }
    return pkg.version ?? '0.0.0'
  } catch {
    return '0.0.0'
  }
}

function readGitCommit(): string {
  try {
    const commit = execSync('git rev-parse --short HEAD', {
      cwd: fileURLToPath(new URL('.', import.meta.url)),
    })
      .toString()
      .trim()
    const dirty = execSync('git status --porcelain', {
      cwd: fileURLToPath(new URL('.', import.meta.url)),
    })
      .toString()
      .trim()
    return dirty ? `${commit}-dirty` : commit
  } catch {
    return 'unknown'
  }
}

const APP_VERSION = process.env.AUTOTEST_APP_VERSION ?? readPackageVersion()
const APP_COMMIT = process.env.AUTOTEST_GIT_COMMIT ?? readGitCommit()
const APP_BUILD_TIME = process.env.AUTOTEST_BUILD_TIME ?? new Date().toISOString()

export default defineConfig({
  plugins: [vue()],
  define: {
    __APP_VERSION__: JSON.stringify(APP_VERSION),
    __APP_COMMIT__: JSON.stringify(APP_COMMIT),
    __APP_BUILD_TIME__: JSON.stringify(APP_BUILD_TIME),
  },
  resolve: {
    alias: { '@': fileURLToPath(new URL('./src', import.meta.url)) },
  },
  server: {
    port: 5173,
    strictPort: false,
    proxy: {
      '/api': { target: BACKEND, changeOrigin: true },
      '/artifacts': { target: BACKEND, changeOrigin: true },
      '/ws': { target: BACKEND.replace('http', 'ws'), ws: true },
    },
  },
  build: {
    outDir: 'dist',
    sourcemap: false,
    chunkSizeWarningLimit: 1800,
  },
})
