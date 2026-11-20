// 运行列表：状态筛选 / 详情 / 删除
import { useState } from 'react'
import {
  Alert,
  App,
  Button,
  Card,
  Empty,
  Popconfirm,
  Select,
  Space,
  Table,
  Tag,
  Typography,
  type TableProps,
} from 'antd'
import { DeleteOutlined, EyeOutlined, ReloadOutlined } from '@ant-design/icons'
import { useNavigate } from 'react-router-dom'

import { describeApiError } from '@/api/client'
import { useDeleteRun, useRuns } from '@/api/runs'
import type { Run } from '@/types/api'
import {
  RUN_STATUS_OPTIONS,
  formatDateTime,
  formatDuration,
  runDurationMs,
  runStatusMeta,
} from '@/lib/format'

export default function RunsPage() {
  const { message } = App.useApp()
  const navigate = useNavigate()
  const [status, setStatus] = useState<string | undefined>(undefined)

  const runsQuery = useRuns({ status, limit: 100 }, { refetchInterval: 5000 })
  const deleteMutation = useDeleteRun()

  const runs = runsQuery.data?.items ?? []
  const total = runsQuery.data?.total ?? 0

  const columns: TableProps<Run>['columns'] = [
    {
      title: 'ID',
      dataIndex: 'id',
      width: 80,
      render: (id: number) => (
        <Typography.Link onClick={() => navigate(`/runs/${id}`)}>#{id}</Typography.Link>
      ),
    },
    {
      title: '脚本',
      dataIndex: 'script_name',
      render: (name: string, run) => (
        <div>
          <div>{name || run.script_id || '—'}</div>
          <div className="small-text muted code-text">{run.script_id ?? ''}</div>
        </div>
      ),
    },
    {
      title: '设备',
      dataIndex: 'device_serial',
      width: 190,
      render: (serial: string) => <span className="code-text">{serial || '—'}</span>,
    },
    {
      title: '状态',
      dataIndex: 'status',
      width: 110,
      render: (value: string) => {
        const meta = runStatusMeta(value)
        return <Tag color={meta.color}>{meta.label}</Tag>
      },
    },
    {
      title: '进度',
      width: 100,
      render: (_, run) => `${run.step_index ?? 0} / ${run.total_steps ?? 0}`,
    },
    {
      title: '开始时间',
      dataIndex: 'started_at',
      width: 180,
      render: (value: string | null) => <span className="small-text">{formatDateTime(value)}</span>,
    },
    {
      title: '耗时',
      width: 110,
      render: (_, run) => <span className="small-text">{formatDuration(runDurationMs(run))}</span>,
    },
    {
      title: '操作',
      width: 160,
      render: (_, run) => (
        <Space size={0}>
          <Button size="small" type="link" icon={<EyeOutlined />} onClick={() => navigate(`/runs/${run.id}`)}>
            详情
          </Button>
          <Popconfirm
            title={`删除运行 #${run.id}？`}
            description="运行记录、步骤、事件与产物记录会被删除"
            okText="删除"
            cancelText="取消"
            onConfirm={() =>
              deleteMutation.mutate(run.id, {
                onSuccess: () => message.success(`已删除运行 #${run.id}`),
              })
            }
          >
            <Button size="small" type="link" danger icon={<DeleteOutlined />}>
              删除
            </Button>
          </Popconfirm>
        </Space>
      ),
    },
  ]

  return (
    <div className="page">
      <Card
        size="small"
        title={
          <Space>
            <span>运行</span>
            <Tag color="blue">{total}</Tag>
          </Space>
        }
        extra={
          <Space wrap>
            <Select
              allowClear
              placeholder="按状态筛选"
              style={{ width: 160 }}
              value={status}
              onChange={(value) => setStatus(value ?? undefined)}
              options={RUN_STATUS_OPTIONS}
            />
            <Button icon={<ReloadOutlined />} loading={runsQuery.isFetching} onClick={() => void runsQuery.refetch()}>
              刷新
            </Button>
            <Button type="primary" onClick={() => navigate('/scripts')}>
              从脚本发起运行
            </Button>
          </Space>
        }
      >
        {runsQuery.isError ? (
          <Alert
            style={{ marginBottom: 12 }}
            type="error"
            showIcon
            message="运行列表加载失败"
            description={describeApiError(runsQuery.error)}
            action={<Button size="small" onClick={() => void runsQuery.refetch()}>重试</Button>}
          />
        ) : null}
        <Table<Run>
          size="small"
          rowKey="id"
          loading={runsQuery.isLoading}
          dataSource={runs}
          columns={columns}
          locale={{
            emptyText: (
              <Empty description={status ? '没有该状态的运行' : '还没有运行记录'}>
                <Button type="primary" onClick={() => navigate('/scripts')}>
                  去脚本页发起运行
                </Button>
              </Empty>
            ),
          }}
          pagination={{
            pageSize: 20,
            showSizeChanger: false,
            showTotal: (count) => `共 ${count} 条运行`,
          }}
          scroll={{ x: 1000 }}
        />
      </Card>
    </div>
  )
}
