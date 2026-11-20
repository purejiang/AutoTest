// 运行详情 · 报告：生成 / 列表 / 下载 / 删除
import { useState } from 'react'
import { App, Alert, Button, Checkbox, Empty, Popconfirm, Space, Table, Tag, Typography, type TableProps } from 'antd'
import { DeleteOutlined, DownloadOutlined, FileAddOutlined } from '@ant-design/icons'

import { describeApiError } from '@/api/client'
import { downloadReport, useDeleteReport, useGenerateReport, useReports } from '@/api/reports'
import type { Report } from '@/types/api'
import { formatBytes, formatDateTime } from '@/lib/format'

export interface RunReportsCardProps {
  runId: number
}

export default function RunReportsCard({ runId }: RunReportsCardProps) {
  const { message } = App.useApp()
  const reportsQuery = useReports(runId)
  const generateMutation = useGenerateReport()
  const deleteMutation = useDeleteReport()
  const [includeVideo, setIncludeVideo] = useState(false)

  const reports = reportsQuery.data ?? []

  const columns: TableProps<Report>['columns'] = [
    {
      title: '创建时间',
      dataIndex: 'created_at',
      render: (value: string) => <span className="small-text">{formatDateTime(value)}</span>,
    },
    {
      title: '大小',
      dataIndex: 'size',
      width: 90,
      render: (size: number) => <span className="small-text">{formatBytes(size)}</span>,
    },
    {
      title: '状态',
      dataIndex: 'status',
      width: 90,
      render: (status: string) => (
        <Tag color={status === 'ready' ? 'success' : 'default'}>{status}</Tag>
      ),
    },
    {
      title: '操作',
      width: 150,
      render: (_, report) => (
        <Space size={0}>
          <Button
            size="small"
            type="link"
            icon={<DownloadOutlined />}
            onClick={() => downloadReport(report.id, `${report.id}.zip`)}
          >
            下载
          </Button>
          <Popconfirm
            title="删除该报告？"
            okText="删除"
            cancelText="取消"
            onConfirm={() =>
              deleteMutation.mutate(report.id, {
                onSuccess: () => message.success('报告已删除'),
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
    <div>
      <Space size={8} style={{ marginBottom: 8 }} wrap>
        <Typography.Text strong>报告</Typography.Text>
        <Checkbox checked={includeVideo} onChange={(event) => setIncludeVideo(event.target.checked)}>
          包含视频
        </Checkbox>
        <Button
          size="small"
          type="primary"
          icon={<FileAddOutlined />}
          loading={generateMutation.isPending}
          onClick={() =>
            generateMutation.mutate(
              { run_id: runId, include_video: includeVideo },
              {
                onSuccess: (report) => message.success(`报告已生成：${report.id}`),
              },
            )
          }
        >
          生成报告
        </Button>
      </Space>

      {reportsQuery.isError ? (
        <Alert
          type="warning"
          showIcon
          message="报告列表加载失败"
          description={describeApiError(reportsQuery.error)}
          style={{ marginBottom: 8 }}
        />
      ) : null}

      <Table<Report>
        size="small"
        rowKey="id"
        dataSource={reports}
        columns={columns}
        loading={reportsQuery.isLoading}
        pagination={false}
        locale={{
          emptyText: <Empty description="还没有报告，点「生成报告」打包 index.html/junit.xml/截图" image={Empty.PRESENTED_IMAGE_SIMPLE} />,
        }}
      />
    </div>
  )
}
