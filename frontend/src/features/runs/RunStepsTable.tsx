// 运行详情 · 步骤进度表（状态/尝试次数/耗时/错误，展开看 output）
import { Table, Tag, Tooltip, Typography, type TableProps } from 'antd'

import type { StepResult } from '@/types/api'
import { formatDuration, prettyJson, stepStatusMeta } from '@/lib/format'

export interface RunStepsTableProps {
  steps: StepResult[]
  currentStepId?: string | null
}

export default function RunStepsTable({ steps, currentStepId }: RunStepsTableProps) {
  const columns: TableProps<StepResult>['columns'] = [
    {
      title: '#',
      width: 56,
      render: (_, __, index) => index + 1,
    },
    {
      title: '节点',
      render: (_, step) => (
        <div>
          <SpaceInline>
            <span>{step.step_name || step.step_id}</span>
            {step.step_id === currentStepId ? <Tag color="blue">当前</Tag> : null}
          </SpaceInline>
          <div className="small-text muted code-text">
            {step.step_id} · {step.step_type}
          </div>
        </div>
      ),
    },
    {
      title: '状态',
      dataIndex: 'status',
      width: 100,
      render: (status: string) => {
        const meta = stepStatusMeta(status)
        return <Tag color={meta.color}>{meta.label}</Tag>
      },
    },
    {
      title: '尝试',
      dataIndex: 'attempts',
      width: 70,
      render: (attempts: number) => attempts ?? 1,
    },
    {
      title: '耗时',
      dataIndex: 'duration_ms',
      width: 100,
      render: (duration: number) => <span className="small-text">{formatDuration(duration)}</span>,
    },
    {
      title: '错误',
      dataIndex: 'error',
      render: (error: StepResult['error']) =>
        error?.message ? (
          <Tooltip title={`${error.code ?? ''} ${error.message}`}>
            <Typography.Text type="danger" ellipsis style={{ maxWidth: 260 }}>
              {error.message}
            </Typography.Text>
          </Tooltip>
        ) : (
          <span className="muted small-text">—</span>
        ),
    },
  ]

  return (
    <Table<StepResult>
      size="small"
      rowKey={(step) => `${step.id}-${step.step_id}`}
      dataSource={steps}
      columns={columns}
      pagination={false}
      rowClassName={(step) =>
        step.status === 'failed' || step.status === 'error' ? 'step-row-failed' : ''
      }
      locale={{ emptyText: '暂无步骤结果' }}
      expandable={{
        expandedRowRender: (step) => (
          <pre className="pre-block">{step.output ? prettyJson(step.output) : '（无 output）'}</pre>
        ),
        rowExpandable: () => true,
      }}
      scroll={{ x: 760 }}
    />
  )
}

function SpaceInline({ children }: { children: React.ReactNode }) {
  return <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}>{children}</span>
}
