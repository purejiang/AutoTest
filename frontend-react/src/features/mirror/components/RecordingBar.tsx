// 录制工具条（与镜像同页面）：开始 web 录制 -> 暂停/继续/停止 + 事件数轮询。
// 契约 §7.4：POST /api/recordings {name?, serial, mode}；POST /api/recordings/{id}/pause|resume|stop；
//           GET /api/recordings/{id}（每 2s 轮询事件数）。

import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import {
  Alert,
  Button,
  Card,
  Input,
  Space,
  Tag,
  Typography,
} from 'antd'
import {
  CaretRightOutlined,
  PauseOutlined,
  StopOutlined,
  VideoCameraOutlined,
} from '@ant-design/icons'
import type { Recording } from '@/types/api'
import { errorMessage, get, post } from '../lib/api'

export interface RecordingBarProps {
  serial: string
  onDeviceChange?: () => void
  disabled?: boolean
}

type BusyAction = 'start' | 'pause' | 'resume' | 'stop' | null

const STATUS_TAG: Record<string, { color: string; text: string }> = {
  recording: { color: 'red', text: '录制中' },
  paused: { color: 'orange', text: '已暂停' },
  stopped: { color: 'default', text: '已停止' },
}

export function RecordingBar({ serial, onDeviceChange, disabled }: RecordingBarProps) {
  const navigate = useNavigate()
  const [name, setName] = useState('')
  const [recording, setRecording] = useState<Recording | null>(null)
  const [busy, setBusy] = useState<BusyAction>(null)
  const [error, setError] = useState<string | null>(null)
  const [stoppedId, setStoppedId] = useState<string | null>(null)

  // 切换设备时清空本地录制状态（远端录制不自动停止）
  useEffect(() => {
    setRecording(null)
    setStoppedId(null)
    setError(null)
    setBusy(null)
  }, [serial])

  // 录制中每 2s 轮询一次事件数/状态（依赖 id/status 而不是整个对象，避免每次合并都重建定时器）
  const recordingId = recording?.id
  const recordingStatus = recording?.status
  useEffect(() => {
    if (!recordingId || recordingStatus === 'stopped') return
    let cancelled = false
    const load = async (): Promise<void> => {
      try {
        const fresh = await get<Recording>(`/recordings/${encodeURIComponent(recordingId)}`)
        if (cancelled) return
        setRecording((prev) => (prev && prev.id === recordingId ? { ...prev, ...fresh } : prev))
      } catch {
        // 轮询失败静默，下一轮再试（不打断录制 UI）
      }
    }
    const timer = setInterval(() => void load(), 2000)
    return () => {
      cancelled = true
      clearInterval(timer)
    }
  }, [recordingId, recordingStatus])

  const startRecording = async (): Promise<void> => {
    setBusy('start')
    setError(null)
    try {
      const body: Record<string, unknown> = { serial, mode: 'web' }
      if (name.trim()) body.name = name.trim()
      const created = await post<Recording>('/recordings', body)
      setStoppedId(null)
      setRecording(created)
      setName('')
    } catch (err) {
      setError(`开始录制失败：${errorMessage(err)}`)
    } finally {
      setBusy(null)
    }
  }

  const control = async (action: 'pause' | 'resume' | 'stop'): Promise<void> => {
    if (!recording) return
    const id = recording.id
    setBusy(action)
    setError(null)
    try {
      const updated = await post<Recording>(`/recordings/${encodeURIComponent(id)}/${action}`)
      const nextStatus = action === 'stop' ? 'stopped' : action === 'pause' ? 'paused' : 'recording'
      setRecording((prev) =>
        prev && prev.id === id
          ? { ...prev, ...(updated && typeof updated === 'object' ? updated : {}), status: nextStatus }
          : prev,
      )
      if (action === 'stop') {
        setStoppedId(id)
        onDeviceChange?.()
      }
    } catch (err) {
      const label = action === 'pause' ? '暂停录制' : action === 'resume' ? '继续录制' : '停止录制'
      setError(`${label}失败：${errorMessage(err)}`)
    } finally {
      setBusy(null)
    }
  }

  const reset = (): void => {
    setRecording(null)
    setStoppedId(null)
    setError(null)
  }

  const meta = recording ? STATUS_TAG[recording.status] ?? { color: 'default', text: recording.status } : null

  return (
    <Card
      size="small"
      className="mirror-recording"
      title={
        <Space size={8}>
          <VideoCameraOutlined />
          <span>录制工具条</span>
          <Tag color="blue">web 录制</Tag>
          {meta ? <Tag color={meta.color}>{meta.text}</Tag> : null}
        </Space>
      }
      extra={
        recording ? (
          <Typography.Text type="secondary">
            事件 {recording.event_count ?? 0}
          </Typography.Text>
        ) : null
      }
    >
      {error ? (
        <Alert
          type="error"
          showIcon
          message={error}
          style={{ marginBottom: 12 }}
          closable
          onClose={() => setError(null)}
        />
      ) : null}

      {!recording || recording.status === 'stopped' ? (
        <Space wrap size={[8, 8]}>
          <Input
            size="small"
            placeholder="录制名称（可选）"
            value={name}
            onChange={(e) => setName(e.target.value)}
            style={{ width: 220 }}
            disabled={disabled || busy === 'start'}
            onPressEnter={() => void startRecording()}
          />
          <Button
            type="primary"
            size="small"
            icon={<VideoCameraOutlined />}
            loading={busy === 'start'}
            disabled={disabled}
            onClick={() => void startRecording()}
          >
            开始 web 录制
          </Button>
          <Typography.Text type="secondary">
            录制期间在镜像上手动操作会被记录为脚本步骤
          </Typography.Text>
        </Space>
      ) : (
        <Space wrap size={[8, 8]}>
          {recording.status === 'recording' ? (
            <Button
              size="small"
              icon={<PauseOutlined />}
              loading={busy === 'pause'}
              onClick={() => void control('pause')}
            >
              暂停
            </Button>
          ) : (
            <Button
              size="small"
              icon={<CaretRightOutlined />}
              loading={busy === 'resume'}
              onClick={() => void control('resume')}
            >
              继续
            </Button>
          )}
          <Button
            size="small"
            danger
            icon={<StopOutlined />}
            loading={busy === 'stop'}
            onClick={() => void control('stop')}
          >
            停止
          </Button>
          <Tag color="geekblue">id: {recording.id}</Tag>
          <Typography.Text type="secondary">
            事件数 {(recording.event_count ?? 0).toString()} · 每 2s 刷新
          </Typography.Text>
        </Space>
      )}

      {stoppedId && (!recording || recording.status === 'stopped') ? (
        <Alert
          type="success"
          showIcon
          style={{ marginTop: 12 }}
          message="录制已停止"
          description={
            <Space wrap>
              <Button
                size="small"
                type="primary"
                onClick={() => navigate(`/recorder/${encodeURIComponent(stoppedId)}`)}
              >
                查看录制
              </Button>
              <Button
                size="small"
                onClick={() =>
                  // fe-core 的录制详情页（/recorder/:id）已有「转脚本」弹窗入口；
                  // 用 query 标记意图，待详情页支持后可直接自动打开弹窗（当前不影响跳转）。
                  navigate(`/recorder/${encodeURIComponent(stoppedId)}?action=to_script`)
                }
              >
                转脚本
              </Button>
              <Button size="small" onClick={reset}>
                再次录制
              </Button>
            </Space>
          }
        />
      ) : null}
    </Card>
  )
}
