import { defineStore } from 'pinia'

/** 跨页面轻量 UI 状态（选择设备、事件流自动滚动等）。 */
export const useUiStore = defineStore('ui', {
  state: () => ({
    selectedSerial: null as string | null,
    eventsAutoScroll: true,
    logcatAutoRefresh: true,
    mirrorRecordHint: false,
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
    setMirrorRecordHint(value: boolean) {
      this.mirrorRecordHint = value
    },
  },
})
