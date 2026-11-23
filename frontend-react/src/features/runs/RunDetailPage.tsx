// 运行详情：状态/控制、步骤进度、WS 实时事件流、产物、报告
import { useEffect, useMemo, useState } from 'react'
import {
  Alert,
  Button,
  Card,
  Col,
  Descriptions,
  Progress,
  Result,
  Row,
  Skeleton,
  Space,
  Tag,
  Typography,
  type DescriptionsProps,
} from 'antd'
import { ArrowLeftOutlined, ReloadOutlined } from '@ant-design/icons'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { useQueryClient } from '@tanstack/react-query'

import { describeApiError } from '@/api/client'
import { useRun, useRunEvents, runKeys } from '@/api/runs'
import { useScript } from '@/api/scripts'
import { useRunEventsWs } from '@/api/useRunEventsWs'
import type { RunEvent } from '@/types/api'
import {
  formatDateTime,
  formatDuration,
  isTerminalRunStatus,
  runDurationMs,
  runStatusMeta,
} from '@/lib/format'
import { useUiStore } from '@/store/ui'

import RunArtifactsCard from './RunArtifactsCard'
import RunControls from './RunControls'
import RunEventsPanel from './RunEventsPanel'
import RunReportsCard from './RunReportsCard'
import RunStepsTable from './RunStepsTable'

export default function RunDetailPage() {
  const { id } = useParams<{ id: string }>()
  const navigate = useNavigate()
  const queryClient = useQueryClient()

  const runId = Number(id)
  const validId = Number.isFinite(runId) && runId > 0

  const runQuery = useRun(validId ? runId : undefined)
  const scriptQuery = useScript(runQuery.data?.script_id ?? undefined)
  const eventsQuery = useRunEvents(validId ? runId : undefined, { limit: 500 })
  const ws = useRunEventsWs(validId ? runId : undefined)

  const autoScroll = useUiStore((state) => state.eventsAutoScroll)
  const setAutoScroll = useUiStore((state) => state.setEventsAutoScroll)

  const liveStatus = ws.status ?? runQuery.data?.status ?? ''

  // WS 推送状态变化后刷新运行详情（步骤/产物）；终态时补拉一次事件历史
  useEffect(() => {
    if (!validId || !ws.status) return
    void queryClient.invalidateQueries({ queryKey: runKeys.detail(runId) })
    if (isTerminalRunStatus(ws.status)) {
      void queryClient.invalidateQueries({ queryKey: ['runs', 'events', runId] })
    }
  }, [ws.status, validId, runId, queryClient])

  const historyEvents = eventsQuery.data?.items
  const [clearedSeq, setClearedSeq] = useState(0)
  const events = useMemo<RunEvent[]>(() => {
    const merged = new Map<number, RunEvent>()
    for (const event of historyEvents ?? []) merged.set(event.seq, event)
    for (const event of ws.events) merged.set(event.seq, event)
    return Array.from(merged.values()).sort((a, b) => a.seq - b.seq)
  }, [historyEvents, ws.events])

  const visibleEvents = useMemo(
    () => (clearedSeq > 0 ? events.filter((event) => event.seq > clearedSeq) : events),
    [events, clearedSeq],
  )

  if (!validId) {
    return (
      <Result
        status="404"
        title="无效的运行 ID"
        extra={
          <Button type="primary" onClick={() => navigate('/runs')}>
            返回运行列表
          </Button>
        }
      />
    )
  }

  if (runQuery.isLoading) {
    return (
      <div className="page">
        <Skeleton active paragraph={{ rows: 10 }} />
      </div>
    )
  }

  if (runQuery.isError || !runQuery.data) {
    return (
      <Result
        status="warning"
        title="运行详情加载失败"
        subTitle={describeApiError(runQuery.error)}
        extra={
          <Space>
            <Button onClick={() => void runQuery.refetch()}>重试</Button>
            <Button type="primary" onClick={() => navigate('/runs')}>
              返回运行列表
            </Button>
          </Space>
        }
      />
    )
  }

  const run = runQuery.data
  const statusMeta = runStatusMeta(liveStatus)
  const totalSteps = run.total_steps || run.steps.length || 0
  const currentIndex = run.step_index ?? 0
  const percent = totalSteps > 0 ? Math.min(100, Math.round((currentIndex / totalSteps) * 100)) : 0
  const progressStatus: 'exception' | 'active' | 'success' | 'normal' =
    liveStatus === 'failed'
      ? 'exception'
      : liveStatus === 'running'
        ? 'active'
        : liveStatus === 'succeeded'
          ? 'success'
          : 'normal'

  const infoItems: DescriptionsProps['items'] = [
    {
      key: 'script',
      label: '脚本',
      children: run.script_id ? (
        <Link to={`/scripts/${run.script_id}`}>{run.script_name || run.script_id}</Link>
      ) : (
        run.script_name || '—'
      ),
    },
    { key: 'device', label: '设备', children: <span className="code-text">{run.device_serial || '—'}</span> },
    {
      key: 'status',
      label: '状态',
      children: <Tag color={statusMeta.color}>{statusMeta.label}</Tag>,
    },
    { key: 'progress', label: '进度', children: `${currentIndex} / ${totalSteps}` },
    {
      key: 'current',
      label: '当前节点',
      children: <span className="code-text">{run.current_step_id || '—'}</span>,
    },
    { key: 'created', label: '创建时间', children: formatDateTime(run.created_at) },
    { key: 'started', label: '开始时间', children: formatDateTime(run.started_at) },
    { key: 'ended', label: '结束时间', children: formatDateTime(run.ended_at) },
    { key: 'duration', label: '耗时', children: formatDuration(runDurationMs(run)) },
    {
      key: 'summary',
      label: '通过/失败/跳过',
      children: `${run.summary?.passed_steps ?? 0} / ${run.summary?.failed_steps ?? 0} / ${run.summary?.skipped_steps ?? 0}`,
    },
  ]

  return (
    <div className="page">
      <div className="page-toolbar">
        <Space wrap>
          <Button icon={<ArrowLeftOutlined />} onClick={() => navigate('/runs')}>
            返回
          </Button>
          <Typography.Title level={5} style={{ margin: 0 }}>
            运行 #{run.id}
          </Typography.Title>
          <Tag color={statusMeta.color}>{statusMeta.label}</Tag>
          {run.debug?.step_mode ? <Tag color="purple">单步调试</Tag> : null}
          <Typography.Text type="secondary" className="small-text">
            {run.script_name || run.script_id || ''}
          </Typography.Text>
        </Space>
        <Button icon={<ReloadOutlined />} loading={runQuery.isFetching} onClick={() => void runQuery.refetch()}>
          刷新
        </Button>
      </div>

      <Card size="small">
        <Descriptions size="small" column={{ xs: 1, sm: 2, lg: 3 }} bordered items={infoItems} />
        <div style={{ marginTop: 12 }}>
          <Progress percent={percent} status={progressStatus} format={() => `${currentIndex}/${totalSteps}`} />
        </div>
      </Card>

      {run.error?.message || run.error?.code ? (
        <Alert
          type="error"
          showIcon
          message={`运行失败${run.error?.code ? `（${run.error.code}）` : ''}`}
          description={run.error?.message ?? '（无错误详情）'}
        />
      ) : null}

      <Row gutter={[16, 16]}>
        <Col xs={24} xl={15}>
          <Space direction="vertical" style={{ width: '100%' }} size={16}>
            <RunControls run={run} liveStatus={liveStatus} script={scriptQuery.data} />
            <Card size="small" title="步骤进度">
              <RunStepsTable steps={run.steps ?? []} currentStepId={run.current_step_id} />
            </Card>
            <RunEventsPanel
              events={visibleEvents}
              connected={ws.connected}
              reconnectAttempt={ws.reconnectAttempt}
              autoScroll={autoScroll}
              onToggleAutoScroll={setAutoScroll}
              onClear={() => {
                const maxSeq = events.length > 0 ? events[events.length - 1].seq : 0
                setClearedSeq(maxSeq)
              }}
            />
          </Space>
        </Col>
        <Col xs={24} xl={9}>
          <Space direction="vertical" style={{ width: '100%' }} size={16}>
            <Card size="small">
              <RunArtifactsCard artifacts={run.artifacts ?? []} />
            </Card>
            <Card size="small">
              <RunReportsCard runId={run.id} />
            </Card>
          </Space>
        </Col>
      </Row>
    </div>
  )
}
