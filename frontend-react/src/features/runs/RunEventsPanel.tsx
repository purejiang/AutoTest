// 运行详情 · 实时事件流（WS + REST 历史合并后按 seq 展示）
import { useEffect, useRef } from 'react'
import { Badge, Button, Card, Empty, Space, Switch, Tag, Typography } from 'antd'
import { ClearOutlined } from '@ant-design/icons'

import type { RunEvent } from '@/types/api'
import { formatTime, runEventColor, summarizeRunEvent } from '@/lib/format'

export interface RunEventsPanelProps {
  events: RunEvent[]
  connected: boolean
  reconnectAttempt: number
  autoScroll: boolean
  onToggleAutoScroll: (value: boolean) => void
  onClear?: () => void
}

export default function RunEventsPanel({
  events,
  connected,
  reconnectAttempt,
  autoScroll,
  onToggleAutoScroll,
  onClear,
}: RunEventsPanelProps) {
  const listRef = useRef<HTMLDivElement | null>(null)

  useEffect(() => {
    if (!autoScroll) return
    const element = listRef.current
    if (element) element.scrollTop = element.scrollHeight
  }, [events.length, autoScroll])

  return (
    <Card
      size="small"
      title={
        <Space size={8} wrap>
          <span>实时事件流</span>
          <Tag color="blue">{events.length}</Tag>
          <Badge
            status={connected ? 'processing' : 'default'}
            text={connected ? 'WS 已连接' : reconnectAttempt > 0 ? `重连中（第 ${reconnectAttempt} 次）` : 'WS 未连接'}
          />
        </Space>
      }
      extra={
        <Space size={8}>
          <Typography.Text type="secondary" className="small-text">
            自动滚底
          </Typography.Text>
          <Switch size="small" checked={autoScroll} onChange={onToggleAutoScroll} />
          {onClear ? (
            <Button
              size="small"
              icon={<ClearOutlined />}
              disabled={events.length === 0}
              onClick={onClear}
              title="清空本地事件缓冲（不影响后端事件记录）"
            >
              清空
            </Button>
          ) : null}
        </Space>
      }
    >
      {events.length === 0 ? (
        <Empty description="暂无事件（运行开始后会实时推送）" image={Empty.PRESENTED_IMAGE_SIMPLE} />
      ) : (
        <div className="event-list" ref={listRef}>
          {events.map((event) => (
            <div className="event-item" key={event.seq}>
              <span className="event-seq">#{event.seq}</span>
              <Tag color={runEventColor(event.type, event.payload)}>{event.type}</Tag>
              <span className="event-time">{formatTime(event.created_at)}</span>
              <span className="event-payload">{summarizeRunEvent(event)}</span>
            </div>
          ))}
        </div>
      )}
    </Card>
  )
}
