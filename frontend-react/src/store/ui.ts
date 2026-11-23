// 跨页面轻量 UI 状态（选择设备、事件流自动滚动等）
import { create } from 'zustand'

export interface UiState {
  /** /devices 当前选中的设备 serial（切换页签后保留） */
  selectedSerial: string | null
  setSelectedSerial: (serial: string | null) => void
  /** 运行详情事件流是否自动滚到底部 */
  eventsAutoScroll: boolean
  setEventsAutoScroll: (value: boolean) => void
  /** /devices logcat 抽屉是否自动刷新 */
  logcatAutoRefresh: boolean
  setLogcatAutoRefresh: (value: boolean) => void
}

export const useUiStore = create<UiState>((set) => ({
  selectedSerial: null,
  setSelectedSerial: (serial) => set({ selectedSerial: serial }),
  eventsAutoScroll: true,
  setEventsAutoScroll: (value) => set({ eventsAutoScroll: value }),
  logcatAutoRefresh: true,
  setLogcatAutoRefresh: (value) => set({ logcatAutoRefresh: value }),
}))
