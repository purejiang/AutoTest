// [fe-mirror 占位] fe-mirror 小组将替换本文件为完整镜像面板实现。
// fe-core 只依赖本导出：MirrorPanel({ serial, onDeviceChange? })
import { Empty } from 'antd'

export interface MirrorPanelProps {
  serial: string
  onDeviceChange?: () => void
}

export default function MirrorPanel(_props: MirrorPanelProps) {
  return (
    <Empty
      description="镜像组件加载中（fe-mirror 实现中）"
      image={Empty.PRESENTED_IMAGE_SIMPLE}
    />
  )
}
