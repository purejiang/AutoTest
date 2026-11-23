/** 构建时注入的版本/提交/构建时间（见 vite.config.ts 的 define） */
declare const __APP_VERSION__: string
declare const __APP_COMMIT__: string
declare const __APP_BUILD_TIME__: string

/// <reference types="vite/client" />

declare module '*.vue' {
  import type { DefineComponent } from 'vue'
  const component: DefineComponent<Record<string, unknown>, Record<string, unknown>, unknown>
  export default component
}
