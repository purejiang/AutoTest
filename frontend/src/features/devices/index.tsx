// 设备页：设备列表 + 镜像挂载点（含镜像面板内置录制工具条）+ 手动控制 / 截图 / 层级 / logcat / 录屏
import { useEffect, useMemo, useState } from 'react'
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
  Skeleton,
  Space,
  Table,
  Tag,
  Typography,
  type TableProps,
} from 'antd'
import {
  ApartmentOutlined,
  CameraOutlined,
  DisconnectOutlined,
  FileSearchOutlined,
  LinkOutlined,
  ReloadOutlined,
} from '@ant-design/icons'

import { describeApiError } from '@/api/client'
import { useConnectDevice, useDevices, useDisconnectDevice } from '@/api/devices'
import { useSettings } from '@/api/settings'
import MirrorPanel from '@/features/mirror'
import type { Device } from '@/types/api'
import { useUiStore } from '@/store/ui'

import DeviceControlPanel from './DeviceControlPanel'
import DeviceHierarchyDrawer from './DeviceHierarchyDrawer'
import DeviceLogcatDrawer from './DeviceLogcatDrawer'
import ScreenshotModal from './ScreenshotModal'

interface ConnectFormValues {
  address: string
}

function deviceStateBadge(state: string): { status: 'success' | 'error' | 'warning' | 'default'; text: string } {
  if (state === 'device') return { status: 'success', text: '在线' }
  if (state === 'offline') return { status: 'error', text: '离线' }
  if (state === 'unauthorized') return { status: 'warning', text: '未授权' }
  return { status: 'default', text: state || '未知' }
}

export default function DevicesPage() {
  const { message } = App.useApp()
  const { data: devices, isLoading, isError, error, refetch, isFetching } = useDevices()
  const selectedSerial = useUiStore((state) => state.selectedSerial)
  const setSelectedSerial = useUiStore((state) => state.setSelectedSerial)

  const [connectOpen, setConnectOpen] = useState(false)
  const [connectForm] = Form.useForm<ConnectFormValues>()
  const [screenshotSerial, setScreenshotSerial] = useState<string | null>(null)
  const [hierarchySerial, setHierarchySerial] = useState<string | null>(null)
  const [logcatSerial, setLogcatSerial] = useState<string | null>(null)

  const connectMutation = useConnectDevice()
  const disconnectMutation = useDisconnectDevice()
  const settingsQuery = useSettings()

  // 配置了 security.token 时，镜像 WS 需要带 token（录制工具条在镜像面板内）
  const securityToken = useMemo(() => {
    const value = settingsQuery.data?.effective?.['security.token']
    return typeof value === 'string' && value.trim() ? value.trim() : undefined
  }, [settingsQuery.data])

  const selected = useMemo(
    () => devices?.find((device) => device.serial === selectedSerial) ?? null,
    [devices, selectedSerial],
  )

  useEffect(() => {
    if (!devices || devices.length === 0) return
    if (!selectedSerial || !devices.some((device) => device.serial === selectedSerial)) {
      setSelectedSerial(devices[0].serial)
    }
  }, [devices, selectedSerial, setSelectedSerial])

  const handleConnect = async (): Promise<void> => {
    try {
      const values = await connectForm.validateFields()
      await connectMutation.mutateAsync({ address: values.address.trim() })
      message.success('已发起连接，请查看设备列表状态')
      setConnectOpen(false)
      connectForm.resetFields()
    } catch {
      // 校验/请求失败：请求错误已由全局错误提示
    }
  }

  const handleDisconnect = (serial: string): void => {
    disconnectMutation.mutate(serial, {
      onSuccess: () => message.success(`已断开 ${serial}`),
    })
  }

  const columns: TableProps<Device>['columns'] = [
    {
      title: '状态',
      dataIndex: 'state',
      width: 96,
      render: (state: string) => {
        const badge = deviceStateBadge(state)
        return <Badge status={badge.status} text={badge.text} />
      },
    },
    {
      title: '设备',
      dataIndex: 'serial',
      render: (serial: string, device) => (
        <div>
          <div className="device-serial">{serial}</div>
          <div className="small-text muted">
            {[device.brand, device.model].filter(Boolean).join(' ') || '—'}
            {device.android_version ? ` · Android ${device.android_version}` : ''}
          </div>
        </div>
      ),
    },
    {
      title: '分辨率',
      width: 150,
      render: (_, device) =>
        device.screen_width ? (
          <span className="small-text">
            {device.screen_width}×{device.screen_height} · {device.rotation}°
          </span>
        ) : (
          '—'
        ),
    },
    {
      title: '占用',
      width: 140,
      render: (_, device) =>
        device.busy ? (
          <Tag color="red">{device.busy_reason || '占用中'}</Tag>
        ) : (
          <Tag color="green">空闲</Tag>
        ),
    },
    {
      title: '镜像',
      width: 130,
      render: (_, device) =>
        device.mirror?.active ? (
          <Tag color="processing">
            镜像中{device.mirror.width ? ` ${device.mirror.width}×${device.mirror.height}` : ''}
          </Tag>
        ) : (
          <Tag>未镜像</Tag>
        ),
    },
    {
      title: '操作',
      width: 170,
      render: (_, device) => (
        <Space size={4} onClick={(event) => event.stopPropagation()}>
          <Button size="small" type="link" onClick={() => setSelectedSerial(device.serial)}>
            {device.serial === selectedSerial ? '已选' : '选择'}
          </Button>
          {device.serial.includes(':') ? (
            <Popconfirm
              title={`断开 ${device.serial}？`}
              okText="断开"
              cancelText="取消"
              onConfirm={() => handleDisconnect(device.serial)}
            >
              <Button size="small" type="link" danger icon={<DisconnectOutlined />}>
                断开
              </Button>
            </Popconfirm>
          ) : null}
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
            <span>设备</span>
            <Badge count={devices?.length ?? 0} showZero color="#1677ff" />
          </Space>
        }
        extra={
          <Space>
            <Button icon={<ReloadOutlined />} loading={isFetching} onClick={() => void refetch()}>
              刷新
            </Button>
            <Button type="primary" icon={<LinkOutlined />} onClick={() => setConnectOpen(true)}>
              连接设备
            </Button>
          </Space>
        }
      >
        {isLoading ? (
          <Skeleton active paragraph={{ rows: 4 }} />
        ) : isError ? (
          <Alert
            type="error"
            showIcon
            message="设备列表加载失败"
            description={describeApiError(error)}
            action={<Button size="small" onClick={() => void refetch()}>重试</Button>}
          />
        ) : !devices || devices.length === 0 ? (
          <Empty description="未检测到在线设备：请连接数据线 / 启动模拟器，或通过 adb connect 添加网络设备">
            <Space>
              <Button type="primary" icon={<LinkOutlined />} onClick={() => setConnectOpen(true)}>
                连接设备
              </Button>
              <Button icon={<ReloadOutlined />} onClick={() => void refetch()}>
                重新检测
              </Button>
            </Space>
          </Empty>
        ) : (
          <Table<Device>
            size="small"
            rowKey="serial"
            pagination={false}
            dataSource={devices}
            columns={columns}
            onRow={(record) => ({
              onClick: () => setSelectedSerial(record.serial),
            })}
            rowClassName={(record) => (record.serial === selectedSerial ? 'device-row-selected' : 'device-row')}
            scroll={{ x: 900 }}
          />
        )}
      </Card>

      {selected ? (
        <div className="devices-grid">
          <div>
            <Card
              size="small"
              title={
                <Space>
                  <span>镜像</span>
                  <Typography.Text type="secondary" className="code-text">
                    {selected.serial}
                  </Typography.Text>
                </Space>
              }
              extra={
                <Button size="small" icon={<ReloadOutlined />} loading={isFetching} onClick={() => void refetch()}>
                  刷新设备
                </Button>
              }
            >
              <div className="mirror-host">
                <MirrorPanel
                  serial={selected.serial}
                  token={securityToken}
                  onDeviceChange={() => {
                    void refetch()
                  }}
                />
              </div>
              <div style={{ marginTop: 8 }}>
                <Typography.Text type="secondary" className="small-text">
                  {selected.mirror?.active
                    ? '镜像已启动：录制入口在镜像面板工具栏内（开始 / 暂停 / 停止 + 事件数）。'
                    : '启动镜像后可录制：镜像面板工具栏内含录制入口。'}
                </Typography.Text>
              </div>
            </Card>
          </div>

          <Card
            size="small"
            title={
              <Space>
                <span>手动控制</span>
                <Typography.Text type="secondary" className="code-text">
                  {selected.serial}
                </Typography.Text>
              </Space>
            }
            extra={
              <Space size={4} wrap>
                <Button size="small" icon={<CameraOutlined />} onClick={() => setScreenshotSerial(selected.serial)}>
                  截图
                </Button>
                <Button size="small" icon={<ApartmentOutlined />} onClick={() => setHierarchySerial(selected.serial)}>
                  层级
                </Button>
                <Button size="small" icon={<FileSearchOutlined />} onClick={() => setLogcatSerial(selected.serial)}>
                  logcat
                </Button>
              </Space>
            }
          >
            <DeviceControlPanel device={selected} />
          </Card>
        </div>
      ) : (
        <Card size="small">
          <Empty description="选择一台设备后可查看镜像与手动控制" image={Empty.PRESENTED_IMAGE_SIMPLE} />
        </Card>
      )}

      <Modal
        open={connectOpen}
        title="连接网络设备"
        okText="连接"
        cancelText="取消"
        confirmLoading={connectMutation.isPending}
        onOk={() => void handleConnect()}
        onCancel={() => setConnectOpen(false)}
      >
        <Form form={connectForm} layout="vertical" onFinish={() => void handleConnect()}>
          <Form.Item
            name="address"
            label="设备地址"
            rules={[
              { required: true, message: '请输入 adb 连接地址' },
              { pattern: /^[\w.-]+:\d{1,5}$/, message: '地址格式应为 host:port，例如 192.168.1.20:5555' },
            ]}
          >
            <Input placeholder="192.168.1.20:5555" onPressEnter={() => void handleConnect()} />
          </Form.Item>
        </Form>
      </Modal>

      <ScreenshotModal
        serial={screenshotSerial}
        open={Boolean(screenshotSerial)}
        onClose={() => setScreenshotSerial(null)}
      />
      <DeviceHierarchyDrawer
        serial={hierarchySerial}
        open={Boolean(hierarchySerial)}
        onClose={() => setHierarchySerial(null)}
      />
      <DeviceLogcatDrawer
        serial={logcatSerial}
        open={Boolean(logcatSerial)}
        onClose={() => setLogcatSerial(null)}
      />
    </div>
  )
}
