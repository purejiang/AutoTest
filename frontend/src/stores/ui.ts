import { defineStore } from 'pinia'

export type ConsoleTab = 'manual' | 'auto'
export type ManualTab = 'record' | 'text' | 'app' | 'tools'

/** 跨页面轻量 UI 状态（选择设备、控制台 tab、事件流自动滚动等）。 */
export const useUiStore = defineStore('ui', {
  state: () => ({
    selectedSerial: null as string | null,
    eventsAutoScroll: true,
    logcatAutoRefresh: true,
    consoleTab: 'manual' as ConsoleTab,
    manualTab: 'record' as ManualTab,
    /** 脚本页/编辑器点「运行」后带到控制台预选的脚本 id。 */
    pendingRunScriptId: null as string | null,
  }),
  actions: {
    setSelectedSerial(serial: string | null) {
      this.selectedSerial = serial
    },
    setEventsAutoScroll(value: boolean) {
      this.eventsAutoScroll = value
    },
    setLogcatAutoRefresh(value: boolean) {
      this.logcatAutoRefresh = value
    },
    setConsoleTab(tab: ConsoleTab) {
      this.consoleTab = tab
    },
    setManualTab(tab: ManualTab) {
      this.manualTab = tab
    },
    setPendingRunScriptId(id: string | null) {
      this.pendingRunScriptId = id
    },
  },
})
