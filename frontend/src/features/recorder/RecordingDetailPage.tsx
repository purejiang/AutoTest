// 录制详情：事件表（低置信高亮/可删除）+ 转脚本 + 暂停/继续/停止 + 删除录制
import { useEffect, useMemo, useRef, useState } from 'react'
import {
  Alert,
  App,
  Badge,
  Button,
  Card,
  Empty,
  Form,
  Input,
  Modal,
  Popconfirm,
  Result,
  Skeleton,
  Space,
  Table,
  Tag,
  Tooltip,
  Typography,
  type TableProps,
} from 'antd'
import {
  ArrowLeftOutlined,
  DeleteOutlined,
  FileAddOutlined,
  PauseCircleOutlined,
  PlayCircleOutlined,
  ReloadOutlined,
  StopOutlined,
} from '@ant-design/icons'
import { useNavigate, useParams, useSearchParams } from 'react-router-dom'

import { describeApiError } from '@/api/client'
import {
  useConvertRecording,
  useDeleteRecording,
  useDeleteRecordingEvent,
  usePauseRecording,
  useRecording,
  useRecordingEvents,
  useResumeRecording,
  useStopRecording,
} from '@/api/recordings'
import type { RecordingEvent, Target } from '@/types/api'
import {
  confidenceMeta,
  formatDateTime,
  formatDuration,
  recordingStatusMeta,
} from '@/lib/format'

const KIND_META: Record<string, { label: string; color: string }> = {
  tap: { label: '点击 tap', color: 'blue' },
  long_press: { label: '长按 long_press', color: 'geekblue' },
  swipe: { label: '滑动 swipe', color: 'cyan' },
  drag: { label: '拖拽 drag', color: 'purple' },
  input: { label: '输入 input', color: 'orange' },
  key: { label: '按键 key', color: 'gold' },
  app: { label: '应用 app', color: 'magenta' },
}

function kindMeta(kind: string): { label: string; color: string } {
  return KIND_META[kind] ?? { label: kind, color: 'default' }
}

function targetText(event: RecordingEvent): string {
  // 后端落库字段是 payload.node；types/api.ts 同时保留 payload.target 兼容，优先 node。
  const target = (event.payload.node as Target | undefined) ?? event.payload.target ?? null
  if (!target) return '—'
  if (target.by === 'point') return `point (${target.x ?? '?'}, ${target.y ?? '?'})`
  return `${target.by} = ${target.value ?? ''}`
}

interface PointLike {
  x?: number
  y?: number
}

function formatPoint(point: PointLike | undefined): string {
  if (!point) return '—'
  return `(${point.x ?? '?'}, ${point.y ?? '?'})`
}

function coordText(event: RecordingEvent): string {
  const payload = event.payload
  // swipe/drag 的 payload 是 from/to 结构，优先展示；其余事件退回 x/y。
  const from = payload.from as PointLike | undefined
  const to = payload.to as PointLike | undefined
  if (from || to) return `${formatPoint(from)} → ${formatPoint(to)}`
  if (payload.x !== undefined || payload.y !== undefined) {
    return `${payload.x ?? '?'}, ${payload.y ?? '?'}`
  }
  return '—'
}

interface ConvertFormValues {
  name?: string
}

export default function RecordingDetailPage() {
  const { id } = useParams<{ id: string }>()
  const navigate = useNavigate()
  const { message } = App.useApp()

  const recordingQuery = useRecording(id)
  const eventsQuery = useRecordingEvents(id, {
    limit: 1000,
    refetchInterval: recordingQuery.data?.status === 'stopped' ? false : 4000,
  })
  const pauseMutation = usePauseRecording(id)
  const resumeMutation = useResumeRecording(id)
  const stopMutation = useStopRecording(id)
  const deleteEventMutation = useDeleteRecordingEvent(id)
  const convertMutation = useConvertRecording(id)
  const deleteRecordingMutation = useDeleteRecording()

  const [convertOpen, setConvertOpen] = useState(false)
  const [convertName, setConvertName] = useState('')
  const [convertForm] = Form.useForm<ConvertFormValues>()
  const [searchParams, setSearchParams] = useSearchParams()
  const autoConvertRef = useRef(false)

  const events = useMemo(() => eventsQuery.data?.items ?? [], [eventsQuery.data])

  // fe-mirror 停止录制后跳 /recorder/{id}?action=to_script：自动打开转脚本弹窗（只触发一次）
  useEffect(() => {
    if (autoConvertRef.current) return
    if (searchParams.get('action') !== 'to_script') return
    const recording = recordingQuery.data
    if (!recording) return
    autoConvertRef.current = true
    setConvertName(`${recording.name || '录制'} 脚本`)
    setConvertOpen(true)
    const next = new URLSearchParams(searchParams)
    next.delete('action')
    setSearchParams(next, { replace: true })
  }, [searchParams, setSearchParams, recordingQuery.data])

  if (recordingQuery.isLoading) {
    return (
      <div className="page">
        <Skeleton active paragraph={{ rows: 10 }} />
      </div>
    )
  }

  if (recordingQuery.isError || !recordingQuery.data) {
    return (
      <Result
        status="warning"
        title="录制加载失败"
        subTitle={describeApiError(recordingQuery.error)}
        extra={
          <Space>
            <Button onClick={() => void recordingQuery.refetch()}>重试</Button>
            <Button type="primary" onClick={() => navigate('/recorder')}>
              返回录制列表
            </Button>
          </Space>
        }
      />
    )
  }

  const recording = recordingQuery.data
  const statusMeta = recordingStatusMeta(recording.status)

  const handleConvert = async (): Promise<void> => {
    let values: ConvertFormValues
    try {
      values = await convertForm.validateFields()
    } catch {
      return
    }
    try {
      const result = await convertMutation.mutateAsync({
        name: values.name?.trim() || undefined,
      })
      const warnings = Array.isArray(result.warnings) ? result.warnings : []
      if (warnings.length > 0) {
        message.warning(`已生成脚本，但有 ${warnings.length} 条提示：${warnings[0]}`)
      }
      const scriptId = result.script?.id ?? result.script_id
      if (scriptId) {
        message.success('已生成脚本，正在打开编辑器')
        setConvertOpen(false)
        convertForm.resetFields()
        navigate(`/scripts/${scriptId}`)
      } else {
        message.success('已生成脚本，请到脚本列表查看')
        setConvertOpen(false)
        navigate('/scripts')
      }
    } catch {
      // 全局错误提示
    }
  }

  const handleDeleteRecording = (): void => {
    deleteRecordingMutation.mutate(recording.id, {
      onSuccess: () => {
        message.success('录制已删除')
        navigate('/recorder')
      },
    })
  }

  const columns: TableProps<RecordingEvent>['columns'] = [
    {
      title: 'seq',
      dataIndex: 'seq',
      width: 70,
    },
    {
      title: '类型',
      dataIndex: 'kind',
      width: 150,
      render: (kind: string) => {
        const meta = kindMeta(kind)
        return <Tag color={meta.color}>{meta.label}</Tag>
      },
    },
    {
      title: '坐标',
      width: 190,
      render: (_, event) => <span className="code-text small-text">{coordText(event)}</span>,
    },
    {
      title: '目标',
      render: (_, event) => <span className="code-text small-text">{targetText(event)}</span>,
    },
    {
      title: '时长',
      width: 90,
      render: (_, event) => (
        <span className="small-text">
          {event.payload.duration_ms !== undefined ? formatDuration(event.payload.duration_ms) : '—'}
        </span>
      ),
    },
    {
      title: '置信度',
      dataIndex: 'confidence',
      width: 110,
      render: (confidence: string, event) => {
        const meta = confidenceMeta(confidence)
        return (
          <Tooltip title={String(event.payload.confidence_reason ?? '')}>
            <Tag color={meta.color}>{meta.label}</Tag>
          </Tooltip>
        )
      },
    },
    {
      title: '时间',
      dataIndex: 'created_at',
      width: 120,
      render: (value: string) => <span className="small-text">{formatDateTime(value)}</span>,
    },
    {
      title: '操作',
      width: 90,
      render: (_, event) => (
        <Popconfirm
          title={`删除事件 #${event.seq}？`}
          description="删除后转脚本时不会包含该事件"
          okText="删除"
          cancelText="取消"
          onConfirm={() =>
            deleteEventMutation.mutate(event.seq, {
              onSuccess: () => message.success(`已删除事件 #${event.seq}`),
            })
          }
        >
          <Button size="small" type="link" danger icon={<DeleteOutlined />}>
            删除
          </Button>
        </Popconfirm>
      ),
    },
  ]

  const isRecording = recording.status === 'recording'
  const isPaused = recording.status === 'paused'

  return (
    <div className="page">
      <div className="page-toolbar">
        <Space wrap>
          <Button icon={<ArrowLeftOutlined />} onClick={() => navigate('/recorder')}>
            返回
          </Button>
          <Typography.Title level={5} style={{ margin: 0 }}>
            {recording.name || recording.id}
          </Typography.Title>
          <Tag color={statusMeta.color}>{statusMeta.label}</Tag>
          <Tag color={recording.mode === 'physical' ? 'purple' : 'blue'}>
            {recording.mode === 'physical' ? '物理录制' : 'web 录制'}
          </Tag>
          <Typography.Text type="secondary" className="code-text">
            {recording.serial}
          </Typography.Text>
          <Badge count={recording.event_count} showZero color="#1677ff" title="事件数" />
        </Space>
        <Space wrap>
          {isRecording ? (
            <Button
              icon={<PauseCircleOutlined />}
              loading={pauseMutation.isPending}
              onClick={() =>
                pauseMutation.mutate(undefined, { onSuccess: () => message.success('录制已暂停') })
              }
            >
              暂停
            </Button>
          ) : null}
          {isPaused ? (
            <Button
              icon={<PlayCircleOutlined />}
              loading={resumeMutation.isPending}
              onClick={() =>
                resumeMutation.mutate(undefined, { onSuccess: () => message.success('录制已继续') })
              }
            >
              继续
            </Button>
          ) : null}
          {isRecording || isPaused ? (
            <Popconfirm
              title="停止录制？"
              description="停止后不能再追加事件，但可以转脚本"
              okText="停止"
              cancelText="取消"
              onConfirm={() =>
                stopMutation.mutate(undefined, { onSuccess: () => message.success('录制已停止') })
              }
            >
              <Button danger icon={<StopOutlined />} loading={stopMutation.isPending}>
                停止
              </Button>
            </Popconfirm>
          ) : null}
          <Button
            type="primary"
            icon={<FileAddOutlined />}
            onClick={() => {
              setConvertName(`${recording.name || '录制'} 脚本`)
              setConvertOpen(true)
            }}
          >
            转脚本
          </Button>
          <Button icon={<ReloadOutlined />} loading={recordingQuery.isFetching || eventsQuery.isFetching} onClick={() => {
            void recordingQuery.refetch()
            void eventsQuery.refetch()
          }}>
            刷新
          </Button>
          <Popconfirm
            title="删除该录制？"
            description="录制与其事件会被永久删除"
            okText="删除"
            cancelText="取消"
            onConfirm={handleDeleteRecording}
          >
            <Button danger icon={<DeleteOutlined />} loading={deleteRecordingMutation.isPending}>
              删除录制
            </Button>
          </Popconfirm>
        </Space>
      </div>

      <Card
        size="small"
        title={
          <Space>
            <span>事件清单</span>
            <Tag color="blue">{events.length}</Tag>
            {isRecording ? <Tag color="processing">录制中，实时刷新</Tag> : null}
          </Space>
        }
      >
        {eventsQuery.isError ? (
          <Alert
            style={{ marginBottom: 12 }}
            type="error"
            showIcon
            message="事件加载失败"
            description={describeApiError(eventsQuery.error)}
            action={<Button size="small" onClick={() => void eventsQuery.refetch()}>重试</Button>}
          />
        ) : null}

        {events.length === 0 && !eventsQuery.isLoading ? (
          <Empty description="暂无事件：去设备页在镜像上操作并开启录制，事件会实时出现在这里">
            <Button type="primary" onClick={() => navigate('/devices')}>
              去设备页录制
            </Button>
          </Empty>
        ) : (
          <Table<RecordingEvent>
            size="small"
            rowKey="seq"
            loading={eventsQuery.isLoading}
            dataSource={events}
            columns={columns}
            pagination={false}
            rowClassName={(event) => (event.confidence === 'low' ? 'recording-event-low' : '')}
            scroll={{ x: 1000, y: 520 }}
            locale={{ emptyText: '暂无事件' }}
          />
        )}
      </Card>

      <Modal
        open={convertOpen}
        title="转脚本"
        okText="生成脚本"
        cancelText="取消"
        destroyOnHidden
        confirmLoading={convertMutation.isPending}
        onOk={() => void handleConvert()}
        onCancel={() => setConvertOpen(false)}
      >
        <Form key={convertName || 'convert-form'} form={convertForm} layout="vertical" initialValues={{ name: convertName }}>
          <Form.Item
            name="name"
            label="脚本名称"
            rules={[{ max: 200, message: '名称最长 200 字符' }]}
            extra="事件会按手势分类转换为 12 类节点，低置信度事件会带 warning 标注"
          >
            <Input placeholder="留空由后端自动命名" onPressEnter={() => void handleConvert()} />
          </Form.Item>
        </Form>
        <Typography.Text type="secondary" className="small-text">
          已删除的事件不会参与转换；转换完成后会跳转到脚本编辑器。
        </Typography.Text>
      </Modal>
    </div>
  )
}
