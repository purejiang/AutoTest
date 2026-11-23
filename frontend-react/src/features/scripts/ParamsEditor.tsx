// 12 类节点 params 动态表单（契约 §3 节点 params）
import {
  Form,
  Input,
  InputNumber,
  Radio,
  Select,
  Space,
  Switch,
  Typography,
} from 'antd'

import { useScripts } from '@/api/scripts'
import type {
  AppParams,
  AssertParams,
  ClickParams,
  InputParams,
  KeyParams,
  LongClickParams,
  NodeType,
  ScreenshotParams,
  ShellParams,
  SubScriptParams,
  SwipeParams,
  WaitParams,
} from '@/types/api'
import { numberOrNull } from '@/lib/format'

import KeyValueEditor from './KeyValueEditor'
import TargetEditor from './TargetEditor'

const KEY_OPTIONS = [
  'back',
  'home',
  'recents',
  'enter',
  'delete',
  'volume_up',
  'volume_down',
  'power',
  'menu',
  'notification',
].map((key) => ({ value: key, label: key }))

const STATE_OPTIONS = [
  { value: 'visible', label: '可见 visible' },
  { value: 'gone', label: '消失 gone' },
]

const APP_ACTIONS = [
  { value: 'launch', label: '启动 launch' },
  { value: 'stop', label: '停止 stop' },
  { value: 'clear', label: '清数据 clear' },
  { value: 'restart', label: '重启 restart' },
]

const ASSERT_KINDS = [
  { value: 'exists', label: '存在 exists' },
  { value: 'not_exists', label: '不存在 not_exists' },
  { value: 'text_contains', label: '文本包含 text_contains' },
  { value: 'foreground', label: '前台应用 foreground' },
]

export interface ParamsEditorProps {
  type: NodeType
  params: Record<string, unknown>
  onChange: (params: Record<string, unknown>) => void
}

function SubScriptEditor({
  params,
  onChange,
}: {
  params: Record<string, unknown>
  onChange: (params: Record<string, unknown>) => void
}) {
  const p = params as unknown as SubScriptParams
  const scriptsQuery = useScripts({ limit: 200 })
  const options = (scriptsQuery.data?.items ?? []).map((script) => ({
    value: script.id,
    label: `${script.name}（${script.step_count ?? script.data?.steps?.length ?? '?'} 节点）`,
  }))

  return (
    <>
      <Form.Item label="子脚本" required>
        <Select
          showSearch
          loading={scriptsQuery.isLoading}
          value={p.script_id || undefined}
          placeholder="选择要调用的脚本"
          options={options}
          onChange={(value) => onChange({ ...params, script_id: value })}
          filterOption={(input, option) =>
            String(option?.label ?? '').toLowerCase().includes(input.toLowerCase())
          }
        />
      </Form.Item>
      <Form.Item label="变量覆盖（可选）">
        <KeyValueEditor
          value={p.variables ?? {}}
          onChange={(variables) => onChange({ ...params, variables })}
          emptyHint="暂无变量覆盖，子脚本使用自身 variables"
        />
      </Form.Item>
    </>
  )
}

export default function ParamsEditor({ type, params, onChange }: ParamsEditorProps) {
  switch (type) {
    case 'click': {
      const p = params as unknown as ClickParams
      return (
        <Form.Item label="点击目标" required>
          <TargetEditor
            value={p.target}
            onChange={(target) => {
              if (target) onChange({ ...params, target })
            }}
          />
        </Form.Item>
      )
    }

    case 'long_click': {
      const p = params as unknown as LongClickParams
      return (
        <>
          <Form.Item label="长按目标" required>
            <TargetEditor
              value={p.target}
              onChange={(target) => {
                if (target) onChange({ ...params, target })
              }}
            />
          </Form.Item>
          <Form.Item label="长按时长 (ms)">
            <InputNumber
              min={100}
              max={10000}
              value={p.duration_ms ?? 800}
              onChange={(value) => onChange({ ...params, duration_ms: numberOrNull(value) ?? 800 })}
              style={{ width: 180 }}
            />
          </Form.Item>
        </>
      )
    }

    case 'swipe':
    case 'drag': {
      const p = params as unknown as SwipeParams
      return (
        <>
          <Form.Item label="起点 from" required>
            <TargetEditor
              value={p.from}
              onChange={(from) => {
                if (from) onChange({ ...params, from })
              }}
            />
          </Form.Item>
          <Form.Item label="终点 to" required>
            <TargetEditor
              value={p.to}
              onChange={(to) => {
                if (to) onChange({ ...params, to })
              }}
            />
          </Form.Item>
          <Form.Item label="持续时间 (ms)">
            <InputNumber
              min={50}
              max={10000}
              value={p.duration_ms ?? (type === 'drag' ? 1000 : 300)}
              onChange={(value) =>
                onChange({ ...params, duration_ms: numberOrNull(value) ?? (type === 'drag' ? 1000 : 300) })
              }
              style={{ width: 180 }}
            />
          </Form.Item>
        </>
      )
    }

    case 'input': {
      const p = params as unknown as InputParams
      return (
        <>
          <Form.Item label="输入文本" required>
            <Input.TextArea
              value={p.text ?? ''}
              onChange={(event) => onChange({ ...params, text: event.target.value })}
              autoSize={{ minRows: 2, maxRows: 4 }}
              placeholder="支持 {{var}} 变量插值"
            />
          </Form.Item>
          <Form.Item label="定位输入框（可选）">
            <TargetEditor
              allowNull
              value={p.target ?? null}
              onChange={(target) => onChange({ ...params, target })}
            />
          </Form.Item>
          <Form.Item>
            <Space>
              <Switch
                size="small"
                checked={Boolean(p.clear_first)}
                onChange={(checked) => onChange({ ...params, clear_first: checked })}
              />
              <Typography.Text>输入前清空输入框</Typography.Text>
            </Space>
          </Form.Item>
        </>
      )
    }

    case 'key': {
      const p = params as unknown as KeyParams
      return (
        <>
          <Form.Item label="按键 key">
            <Select
              allowClear
              value={p.key ?? undefined}
              options={KEY_OPTIONS}
              placeholder="选择按键名"
              style={{ width: 240 }}
              onChange={(value) => onChange({ ...params, key: value, keycode: undefined })}
            />
          </Form.Item>
          <Form.Item label="或 keycode" extra="key 与 keycode 二选一，优先 key">
            <InputNumber
              min={0}
              value={p.keycode ?? null}
              placeholder="例如 4（返回键）"
              style={{ width: 240 }}
              onChange={(value) => onChange({ ...params, keycode: numberOrNull(value) ?? undefined, key: undefined })}
            />
          </Form.Item>
        </>
      )
    }

    case 'wait': {
      const p = params as unknown as WaitParams
      return (
        <>
          <Form.Item label="等待模式">
            <Radio.Group
              optionType="button"
              value={p.mode ?? 'sleep'}
              onChange={(event) => onChange({ ...params, mode: event.target.value })}
              options={[
                { value: 'sleep', label: '固定等待' },
                { value: 'until', label: '等待元素' },
              ]}
            />
          </Form.Item>
          {p.mode === 'until' ? (
            <>
              <Form.Item label="等待目标" required>
                <TargetEditor
                  value={p.target}
                  onChange={(target) => {
                    if (target) onChange({ ...params, target })
                  }}
                />
              </Form.Item>
              <Space wrap size={12}>
                <Form.Item label="状态">
                  <Select
                    value={p.state ?? 'visible'}
                    options={STATE_OPTIONS}
                    style={{ width: 150 }}
                    onChange={(value) => onChange({ ...params, state: value })}
                  />
                </Form.Item>
                <Form.Item label="超时 (ms)">
                  <InputNumber
                    min={100}
                    max={600000}
                    value={p.timeout_ms ?? 5000}
                    onChange={(value) => onChange({ ...params, timeout_ms: numberOrNull(value) ?? 5000 })}
                    style={{ width: 150 }}
                  />
                </Form.Item>
                <Form.Item label="轮询间隔 (ms)">
                  <InputNumber
                    min={100}
                    max={5000}
                    value={p.interval_ms ?? 350}
                    onChange={(value) => onChange({ ...params, interval_ms: numberOrNull(value) ?? 350 })}
                    style={{ width: 150 }}
                  />
                </Form.Item>
              </Space>
            </>
          ) : (
            <Form.Item label="等待时长 (ms)" required>
              <InputNumber
                min={0}
                max={600000}
                value={p.ms ?? 1000}
                onChange={(value) => onChange({ ...params, ms: numberOrNull(value) ?? 1000 })}
                style={{ width: 180 }}
              />
            </Form.Item>
          )}
        </>
      )
    }

    case 'app': {
      const p = params as unknown as AppParams
      const action = p.action ?? 'launch'
      return (
        <>
          <Form.Item label="操作">
            <Select
              value={action}
              options={APP_ACTIONS}
              style={{ width: 220 }}
              onChange={(value) => onChange({ ...params, action: value })}
            />
          </Form.Item>
          <Form.Item label="package">
            <Input
              value={p.package ?? ''}
              placeholder="com.example.app"
              onChange={(event) => onChange({ ...params, package: event.target.value || null })}
              style={{ maxWidth: 360 }}
            />
          </Form.Item>
          {action === 'launch' || action === 'restart' ? (
            <Form.Item label="activity（可选）">
              <Input
                value={p.activity ?? ''}
                placeholder=".MainActivity（留空用 launcher activity）"
                onChange={(event) => onChange({ ...params, activity: event.target.value || null })}
                style={{ maxWidth: 360 }}
              />
            </Form.Item>
          ) : null}
        </>
      )
    }

    case 'shell': {
      const p = params as unknown as ShellParams
      return (
        <>
          <Form.Item label="命令" required>
            <Input.TextArea
              value={p.command ?? ''}
              onChange={(event) => onChange({ ...params, command: event.target.value })}
              autoSize={{ minRows: 2, maxRows: 5 }}
              placeholder="adb shell 命令（不含 adb shell 前缀），支持 {{var}}"
            />
          </Form.Item>
          <Space wrap size={12}>
            <Form.Item label="超时 (ms)">
              <InputNumber
                min={100}
                max={600000}
                value={p.timeout_ms ?? 10000}
                onChange={(value) => onChange({ ...params, timeout_ms: numberOrNull(value) ?? 10000 })}
                style={{ width: 170 }}
              />
            </Form.Item>
            <Form.Item label="检查退出码">
              <Switch
                checked={p.check_exit_code !== false}
                onChange={(checked) => onChange({ ...params, check_exit_code: checked })}
              />
            </Form.Item>
          </Space>
        </>
      )
    }

    case 'assert': {
      const p = params as unknown as AssertParams
      const kind = p.kind ?? 'exists'
      const needsTarget = kind === 'exists' || kind === 'not_exists' || kind === 'text_contains'
      const needsValue = kind === 'text_contains' || kind === 'foreground'
      return (
        <>
          <Form.Item label="断言类型">
            <Select
              value={kind}
              options={ASSERT_KINDS}
              style={{ width: 240 }}
              onChange={(value) => onChange({ ...params, kind: value })}
            />
          </Form.Item>
          {needsTarget ? (
            <Form.Item label="断言目标" required>
              <TargetEditor
                value={p.target}
                onChange={(target) => {
                  if (target) onChange({ ...params, target })
                }}
              />
            </Form.Item>
          ) : null}
          {needsValue ? (
            <Form.Item
              label={kind === 'foreground' ? '前台包名' : '包含文本'}
              required
            >
              <Input
                value={p.value ?? ''}
                onChange={(event) => onChange({ ...params, value: event.target.value || null })}
                placeholder={kind === 'foreground' ? 'com.example.app' : '期望包含的文本'}
                style={{ maxWidth: 360 }}
              />
            </Form.Item>
          ) : null}
        </>
      )
    }

    case 'sub_script':
      return <SubScriptEditor params={params} onChange={onChange} />

    case 'screenshot': {
      const p = params as unknown as ScreenshotParams
      return (
        <Form.Item label="截图名称（可选）">
          <Input
            value={p.name ?? ''}
            placeholder="留空自动命名"
            onChange={(event) => onChange({ ...params, name: event.target.value || null })}
            style={{ maxWidth: 360 }}
          />
        </Form.Item>
      )
    }

    default:
      return null
  }
}
