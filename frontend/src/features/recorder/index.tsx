// 录制列表：状态/模式/设备/事件数；新建录制入口；无数据时引导去设备页
import { useState } from 'react'
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
  Radio,
  Select,
  Space,
  Table,
  Tag,
  Typography,
  type TableProps,
} from 'antd'
import { DeleteOutlined, EyeOutlined, PlusOutlined, ReloadOutlined } from '@ant-design/icons'
import { useNavigate } from 'react-router-dom'

import { describeApiError } from '@/api/client'
import { useDevices } from '@/api/devices'
import { useCreateRecording, useDeleteRecording, useRecordings } from '@/api/recordings'
import type { Recording } from '@/types/api'
import { formatDateTime, recordingStatusMeta } from '@/lib/format'

interface CreateRecordingFormValues {
  serial: string
  mode: 'web' | 'physical'
  name?: string
}

export default function RecorderPage() {
  const { message } = App.useApp()
  const navigate = useNavigate()
  const [createOpen, setCreateOpen] = useState(false)
  const [createForm] = Form.useForm<CreateRecordingFormValues>()

  const recordingsQuery = useRecordings({ limit: 100 }, { refetchInterval: 5000 })
  const devicesQuery = useDevices({ refetchInterval: 10000, enabled: createOpen })
  const createMutation = useCreateRecording()
  const deleteMutation = useDeleteRecording()

  const recordings = recordingsQuery.data?.items ?? []
  const total = recordingsQuery.data?.total ?? 0
  const deviceOptions = (devicesQuery.data ?? []).map((device) => ({
    value: device.serial,
    label: `${device.serial}${device.model ? ` · ${device.model}` : ''}${device.busy ? '（占用中）' : ''}`,
    disabled: device.busy,
  }))

  const handleCreate = async (): Promise<void> => {
    try {
      const values = await createForm.validateFields()
      const recording = await createMutation.mutateAsync({
        serial: values.serial,
        mode: values.mode,
        name: values.name?.trim() || undefined,
      })
      message.success('录制已创建')
      setCreateOpen(false)
      createForm.resetFields()
      navigate(`/recorder/${recording.id}`)
    } catch {
      // 错误由全局提示
    }
  }

  const columns: TableProps<Recording>['columns'] = [
    {
      title: '名称',
      dataIndex: 'name',
      render: (name: string, recording) => (
        <div>
          <Typography.Link onClick={() => navigate(`/recorder/${recording.id}`)}>
            {name || recording.id}
          </Typography.Link>
          <div className="small-text muted code-text">{recording.id}</div>
        </div>
      ),
    },
    {
      title: '状态',
      dataIndex: 'status',
      width: 100,
      render: (status: string) => {
        const meta = recordingStatusMeta(status)
        return <Tag color={meta.color}>{meta.label}</Tag>
      },
    },
    {
      title: '模式',
      dataIndex: 'mode',
      width: 100,
      render: (mode: string) => (
        <Tag color={mode === 'physical' ? 'purple' : 'blue'}>
          {mode === 'physical' ? '物理录制' : 'web 录制'}
        </Tag>
      ),
    },
    {
      title: '设备',
      dataIndex: 'serial',
      width: 180,
      render: (serial: string) => <span className="code-text">{serial || '—'}</span>,
    },
    {
      title: '事件数',
      dataIndex: 'event_count',
      width: 90,
      render: (count: number) => <Badge count={count} showZero color="#1677ff" />,
    },
    {
      title: '开始时间',
      dataIndex: 'started_at',
      width: 180,
      render: (value: string | null) => <span className="small-text">{formatDateTime(value)}</span>,
    },
    {
      title: '操作',
      width: 160,
      render: (_, recording) => (
        <Space size={0}>
          <Button
            size="small"
            type="link"
            icon={<EyeOutlined />}
            onClick={() => navigate(`/recorder/${recording.id}`)}
          >
            详情
          </Button>
          <Popconfirm
            title={`删除录制「${recording.name || recording.id}」？`}
            description="录制事件会一并删除"
            okText="删除"
            cancelText="取消"
            onConfirm={() =>
              deleteMutation.mutate(recording.id, {
                onSuccess: () => message.success('录制已删除'),
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
            <span>录制</span>
            <Tag color="blue">{total}</Tag>
          </Space>
        }
        extra={
          <Space wrap>
            <Button icon={<ReloadOutlined />} loading={recordingsQuery.isFetching} onClick={() => void recordingsQuery.refetch()}>
              刷新
            </Button>
            <Button type="primary" icon={<PlusOutlined />} onClick={() => setCreateOpen(true)}>
              新建录制
            </Button>
            <Button onClick={() => navigate('/devices')}>去设备页录制</Button>
          </Space>
        }
      >
        {recordingsQuery.isError ? (
          <Alert
            style={{ marginBottom: 12 }}
            type="error"
            showIcon
            message="录制列表加载失败"
            description={describeApiError(recordingsQuery.error)}
            action={<Button size="small" onClick={() => void recordingsQuery.refetch()}>重试</Button>}
          />
        ) : null}
        <Table<Recording>
          size="small"
          rowKey="id"
          loading={recordingsQuery.isLoading}
          dataSource={recordings}
          columns={columns}
          locale={{
            emptyText: (
              <Empty description="还没有录制。前往设备页在镜像上操作并开启录制，或在这里新建 web/物理录制。">
                <Space>
                  <Button type="primary" onClick={() => navigate('/devices')}>
                    去设备页录制
                  </Button>
                  <Button icon={<PlusOutlined />} onClick={() => setCreateOpen(true)}>
                    新建录制
                  </Button>
                </Space>
              </Empty>
            ),
          }}
          pagination={{ pageSize: 20, showSizeChanger: false, showTotal: (count) => `共 ${count} 条录制` }}
          scroll={{ x: 1000 }}
        />
      </Card>

      <Modal
        open={createOpen}
        title="新建录制"
        okText="创建"
        cancelText="取消"
        confirmLoading={createMutation.isPending}
        onOk={() => void handleCreate()}
        onCancel={() => setCreateOpen(false)}
      >
        <Form form={createForm} layout="vertical" initialValues={{ mode: 'web', name: '' }}>
          <Form.Item
            name="serial"
            label="设备"
            rules={[{ required: true, message: '请选择要录制的设备' }]}
            extra={devicesQuery.isError ? `设备列表不可用：${describeApiError(devicesQuery.error)}` : '占用中的设备不可选'}
          >
            <Select
              placeholder="选择设备"
              options={deviceOptions}
              loading={devicesQuery.isLoading}
              notFoundContent={devicesQuery.isError ? '设备列表不可用' : '未检测到设备'}
            />
          </Form.Item>
          <Form.Item name="mode" label="录制模式">
            <Radio.Group
              options={[
                { value: 'web', label: 'web 录制（镜像操作留痕）' },
                { value: 'physical', label: '物理录制（getevent）' },
              ]}
            />
          </Form.Item>
          <Form.Item name="name" label="名称（可选）">
            <Input placeholder="例如：登录流程录制" />
          </Form.Item>
        </Form>
      </Modal>
    </div>
  )
}
