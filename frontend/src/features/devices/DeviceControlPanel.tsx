// 手动控制面板：按键 / 文本 / shell / 应用操作 / 录屏 / 设备信息
import { useState } from 'react'
import {
  Alert,
  App,
  Button,
  Descriptions,
  Divider,
  Input,
  InputNumber,
  Select,
  Space,
  Switch,
  Tabs,
  Tag,
  Typography,
  type DescriptionsProps,
  type TabsProps,
} from 'antd'
import {
  ArrowLeftOutlined,
  BellOutlined,
  DeleteOutlined,
  EnterOutlined,
  HomeOutlined,
  MenuOutlined,
  PoweroffOutlined,
  SoundOutlined,
  SoundTwoTone,
  AppstoreOutlined,
} from '@ant-design/icons'

import {
  useDeviceAppAction,
  useDeviceKey,
  useDeviceShell,
  useDeviceText,
  useMirrorStatus,
  useScreenRecordStart,
  useScreenRecordStop,
} from '@/api/devices'
import type { Device } from '@/types/api'
import { describeApiError } from '@/api/client'
import { formatDateTime, numberOrNull, prettyJson } from '@/lib/format'

export interface DeviceControlPanelProps {
  device: Device
}

const QUICK_KEYS = [
  { key: 'back', label: '返回', icon: <ArrowLeftOutlined /> },
  { key: 'home', label: '桌面', icon: <HomeOutlined /> },
  { key: 'recents', label: '最近任务', icon: <AppstoreOutlined /> },
  { key: 'menu', label: '菜单', icon: <MenuOutlined /> },
  { key: 'notification', label: '通知栏', icon: <BellOutlined /> },
  { key: 'volume_up', label: '音量 +', icon: <SoundOutlined /> },
  { key: 'volume_down', label: '音量 -', icon: <SoundTwoTone /> },
  { key: 'power', label: '电源键', icon: <PoweroffOutlined /> },
  { key: 'enter', label: '回车', icon: <EnterOutlined /> },
  { key: 'delete', label: '删除', icon: <DeleteOutlined /> },
]

const APP_ACTIONS = [
  { value: 'launch', label: '启动 launch' },
  { value: 'stop', label: '停止 stop' },
  { value: 'clear', label: '清数据 clear' },
  { value: 'restart', label: '重启 restart' },
]

function ManualTab({ device }: DeviceControlPanelProps) {
  const { message } = App.useApp()
  const keyMutation = useDeviceKey(device.serial)
  const textMutation = useDeviceText(device.serial)
  const shellMutation = useDeviceShell(device.serial)
  const [text, setText] = useState('')
  const [clearFirst, setClearFirst] = useState(false)
  const [command, setCommand] = useState('getprop ro.product.model')
  const [shellOutput, setShellOutput] = useState<string | null>(null)

  const handleKey = (key: string, label: string): void => {
    keyMutation.mutate(
      { key },
      {
        onSuccess: () => message.success(`已发送：${label}`, 1.2),
      },
    )
  }

  const handleText = (): void => {
    if (!text) {
      message.warning('请输入要发送的文本')
      return
    }
    textMutation.mutate(
      { text, clear_first: clearFirst },
      {
        onSuccess: () => {
          message.success('文本已发送')
          setText('')
        },
      },
    )
  }

  const handleShell = (): void => {
    if (!command.trim()) {
      message.warning('请输入 shell 命令')
      return
    }
    shellMutation.mutate(
      { command: command.trim(), timeout_ms: 15000 },
      {
        onSuccess: (data) => {
          setShellOutput(prettyJson(data))
          message.success('命令已执行')
        },
      },
    )
  }

  return (
    <Space direction="vertical" style={{ width: '100%' }} size={12}>
      <div>
        <Typography.Text strong>按键</Typography.Text>
        <div className="key-button-grid" style={{ marginTop: 8 }}>
          {QUICK_KEYS.map((item) => (
            <Button
              key={item.key}
              icon={item.icon}
              disabled={keyMutation.isPending || device.busy}
              onClick={() => handleKey(item.key, item.label)}
            >
              {item.label}
            </Button>
          ))}
        </div>
        {device.busy ? (
          <Alert style={{ marginTop: 8 }} type="warning" showIcon message={`设备被占用：${device.busy_reason ?? '运行/录制中'}`} />
        ) : null}
      </div>

      <Divider style={{ margin: '4px 0' }} />

      <div>
        <Typography.Text strong>文本输入</Typography.Text>
        <Space.Compact style={{ width: '100%', marginTop: 8 }}>
          <Input
            value={text}
            onChange={(event) => setText(event.target.value)}
            placeholder="输入要发送到设备的文本（支持中文需设备端输入法配合）"
            onPressEnter={handleText}
            disabled={device.busy}
          />
          <Button type="primary" loading={textMutation.isPending} onClick={handleText} disabled={device.busy}>
            发送
          </Button>
        </Space.Compact>
        <Space style={{ marginTop: 6 }}>
          <Switch size="small" checked={clearFirst} onChange={setClearFirst} />
          <Typography.Text className="small-text">发送前清空输入框</Typography.Text>
        </Space>
      </div>

      <Divider style={{ margin: '4px 0' }} />

      <div>
        <Typography.Text strong>Shell 命令</Typography.Text>
        <Space.Compact style={{ width: '100%', marginTop: 8 }}>
          <Input
            value={command}
            onChange={(event) => setCommand(event.target.value)}
            placeholder="adb shell 命令（不含 adb shell 前缀）"
            onPressEnter={handleShell}
          />
          <Button loading={shellMutation.isPending} onClick={handleShell}>
            执行
          </Button>
        </Space.Compact>
        {shellMutation.isError ? (
          <Alert style={{ marginTop: 8 }} type="error" showIcon message={describeApiError(shellMutation.error)} />
        ) : null}
        {shellOutput ? <pre className="pre-block" style={{ marginTop: 8 }}>{shellOutput}</pre> : null}
      </div>
    </Space>
  )
}

function AppTab({ device }: DeviceControlPanelProps) {
  const { message } = App.useApp()
  const appMutation = useDeviceAppAction(device.serial)
  const [action, setAction] = useState<'launch' | 'stop' | 'clear' | 'restart'>('launch')
  const [packageName, setPackageName] = useState('')
  const [activity, setActivity] = useState('')

  const handleExecute = (): void => {
    if (!packageName.trim()) {
      message.warning('请输入 package（如 com.android.settings）')
      return
    }
    appMutation.mutate(
      {
        action,
        package: packageName.trim(),
        activity: activity.trim() || undefined,
      },
      {
        onSuccess: (data) => {
          message.success(`${action} 已执行`)
          const text = prettyJson(data)
          if (text && text !== '{}') message.info(text.slice(0, 200))
        },
      },
    )
  }

  return (
    <Space direction="vertical" style={{ width: '100%' }} size={12}>
      <div>
        <Typography.Text strong>应用操作</Typography.Text>
        <Select
          style={{ width: '100%', marginTop: 8 }}
          value={action}
          onChange={(value) => setAction(value)}
          options={APP_ACTIONS}
        />
      </div>
      <Input
        value={packageName}
        onChange={(event) => setPackageName(event.target.value)}
        placeholder="package：com.example.app"
        addonBefore="package"
      />
      {action === 'launch' || action === 'restart' ? (
        <Input
          value={activity}
          onChange={(event) => setActivity(event.target.value)}
          placeholder="activity（可空，默认 launcher activity）"
          addonBefore="activity"
        />
      ) : null}
      <Button type="primary" loading={appMutation.isPending} onClick={handleExecute} disabled={device.busy}>
        执行
      </Button>
      <Typography.Text type="secondary" className="small-text">
        清数据（clear）会清空应用数据，请谨慎操作。
      </Typography.Text>
    </Space>
  )
}

function ScreenRecordTab({ device }: DeviceControlPanelProps) {
  const { message } = App.useApp()
  const startMutation = useScreenRecordStart(device.serial)
  const stopMutation = useScreenRecordStop(device.serial)
  const [name, setName] = useState('')
  const [bitRate, setBitRate] = useState<number>(4_000_000)
  const [timeLimit, setTimeLimit] = useState<number>(180)
  const [lastResult, setLastResult] = useState<string | null>(null)

  const handleStart = (): void => {
    startMutation.mutate(
      {
        name: name.trim() || undefined,
        bit_rate: bitRate,
        time_limit_s: timeLimit,
      },
      {
        onSuccess: (data) => {
          setLastResult(prettyJson(data))
          message.success('设备录屏已开始')
        },
      },
    )
  }

  const handleStop = (): void => {
    stopMutation.mutate(undefined, {
      onSuccess: (data) => {
        setLastResult(prettyJson(data))
        message.success('设备录屏已停止')
      },
    })
  }

  return (
    <Space direction="vertical" style={{ width: '100%' }} size={12}>
      <Input value={name} onChange={(event) => setName(event.target.value)} placeholder="录屏名称（可选）" addonBefore="name" />
      <InputNumber
        style={{ width: '100%' }}
        value={bitRate}
        min={1_000_000}
        max={20_000_000}
        step={500_000}
        onChange={(value) => setBitRate(numberOrNull(value) ?? 4_000_000)}
        addonBefore="bit_rate"
      />
      <InputNumber
        style={{ width: '100%' }}
        value={timeLimit}
        min={5}
        max={180}
        onChange={(value) => setTimeLimit(numberOrNull(value) ?? 180)}
        addonBefore="time_limit_s"
      />
      <Space>
        <Button type="primary" loading={startMutation.isPending} onClick={handleStart} disabled={device.busy}>
          开始录屏
        </Button>
        <Button danger loading={stopMutation.isPending} onClick={handleStop}>
          停止并保存
        </Button>
      </Space>
      <Typography.Text type="secondary" className="small-text">
        走设备端 screenrecord（最长 180s），产物由后端保存为 video artifact。
      </Typography.Text>
      {lastResult ? <pre className="pre-block">{lastResult}</pre> : null}
    </Space>
  )
}

function InfoTab({ device }: DeviceControlPanelProps) {
  const mirrorQuery = useMirrorStatus(device.serial)

  const items: DescriptionsProps['items'] = [
    { key: 'serial', label: 'serial', children: <span className="code-text">{device.serial}</span> },
    { key: 'state', label: '状态', children: <Tag color={device.state === 'device' ? 'success' : 'error'}>{device.state}</Tag> },
    { key: 'model', label: '型号', children: `${device.brand ?? ''} ${device.model ?? ''}`.trim() || '—' },
    { key: 'android', label: 'Android', children: `${device.android_version ?? '—'} (SDK ${device.sdk ?? '—'})` },
    {
      key: 'screen',
      label: '分辨率',
      children: `${device.screen_width} × ${device.screen_height} · 旋转 ${device.rotation}°`,
    },
    {
      key: 'busy',
      label: '占用',
      children: device.busy ? <Tag color="red">{device.busy_reason ?? '占用中'}</Tag> : <Tag color="green">空闲</Tag>,
    },
    {
      key: 'mirror',
      label: '镜像',
      children: mirrorQuery.data ? (
        mirrorQuery.data.active ? (
          <Tag color="processing">
            镜像中 {mirrorQuery.data.width ?? '?'}×{mirrorQuery.data.height ?? '?'}
          </Tag>
        ) : (
          <Tag>未镜像</Tag>
        )
      ) : mirrorQuery.isError ? (
        <Tag color="default">状态不可用</Tag>
      ) : (
        <Tag color="default">查询中…</Tag>
      ),
    },
    { key: 'updated', label: '刷新时间', children: formatDateTime(new Date().toISOString()) },
  ]

  return <Descriptions size="small" column={1} bordered items={items} />
}

export default function DeviceControlPanel({ device }: DeviceControlPanelProps) {
  const items: TabsProps['items'] = [
    { key: 'manual', label: '手动控制', children: <ManualTab device={device} /> },
    { key: 'app', label: '应用', children: <AppTab device={device} /> },
    { key: 'screenrecord', label: '设备录屏', children: <ScreenRecordTab device={device} /> },
    { key: 'info', label: '设备信息', children: <InfoTab device={device} /> },
  ]

  return <Tabs size="small" items={items} destroyInactiveTabPane={false} />
}
