// 脚本级设置表单：名称/notes/variables/默认超时/默认失败策略/logcat/重试/失败截图
import { Col, Form, Input, InputNumber, Row, Select, Space, Switch, Typography } from 'antd'

import type { ScriptData, ScriptSettings } from '@/types/api'
import { numberOrNull } from '@/lib/format'

import KeyValueEditor from './KeyValueEditor'

const ON_FAIL_OPTIONS = [
  { value: 'abort', label: 'abort（中止整个运行）' },
  { value: 'continue', label: 'continue（继续下一步）' },
]

export interface ScriptSettingsFormProps {
  data: ScriptData
  onChange: (data: ScriptData) => void
}

export default function ScriptSettingsForm({ data, onChange }: ScriptSettingsFormProps) {
  const patchData = (partial: Partial<ScriptData>): void => onChange({ ...data, ...partial })
  const patchSettings = (partial: Partial<ScriptSettings>): void =>
    onChange({ ...data, settings: { ...data.settings, ...partial } })

  return (
    <Form layout="vertical" size="small">
      <Row gutter={12}>
        <Col xs={24} md={12}>
          <Form.Item label="脚本名称">
            <Input value={data.name} onChange={(event) => patchData({ name: event.target.value })} />
          </Form.Item>
        </Col>
        <Col xs={24} md={12}>
          <Form.Item label="备注 notes">
            <Input value={data.notes} onChange={(event) => patchData({ notes: event.target.value })} />
          </Form.Item>
        </Col>
      </Row>

      <Form.Item
        label="脚本变量 variables"
        extra="运行时可由发起请求的 variables 覆盖；步骤字符串字段支持 {{var}} 插值"
      >
        <KeyValueEditor
          value={data.variables}
          onChange={(variables) => patchData({ variables })}
          emptyHint="暂无脚本变量"
        />
      </Form.Item>

      <Row gutter={12}>
        <Col xs={12} md={6}>
          <Form.Item label="默认节点超时 (ms)">
            <InputNumber
              min={100}
              max={600000}
              value={data.settings.default_timeout_ms}
              onChange={(value) => patchSettings({ default_timeout_ms: numberOrNull(value) ?? 8000 })}
              style={{ width: '100%' }}
            />
          </Form.Item>
        </Col>
        <Col xs={12} md={6}>
          <Form.Item label="默认失败策略">
            <Select
              value={data.settings.on_fail}
              options={ON_FAIL_OPTIONS}
              onChange={(value) => patchSettings({ on_fail: value as 'abort' | 'continue' })}
            />
          </Form.Item>
        </Col>
        <Col xs={12} md={6}>
          <Form.Item label="运行采集 logcat">
            <Switch checked={data.settings.logcat} onChange={(checked) => patchSettings({ logcat: checked })} />
          </Form.Item>
        </Col>
        <Col xs={12} md={6}>
          <Form.Item label="失败自动截图">
            <Switch
              checked={data.settings.screenshot_on_fail}
              onChange={(checked) => patchSettings({ screenshot_on_fail: checked })}
            />
          </Form.Item>
        </Col>
      </Row>

      <Form.Item label="脚本级默认重试">
        <Space wrap>
          <Switch
            checked={data.settings.retry !== null}
            onChange={(checked) => patchSettings({ retry: checked ? { count: 1, interval_ms: 1000 } : null })}
          />
          {data.settings.retry ? (
            <>
              <InputNumber
                addonBefore="次数"
                min={0}
                max={20}
                value={data.settings.retry.count}
                onChange={(value) =>
                  patchSettings({
                    retry: { ...data.settings.retry!, count: numberOrNull(value) ?? 0 },
                  })
                }
              />
              <InputNumber
                addonBefore="间隔 ms"
                min={0}
                max={60000}
                value={data.settings.retry.interval_ms}
                onChange={(value) =>
                  patchSettings({
                    retry: { ...data.settings.retry!, interval_ms: numberOrNull(value) ?? 0 },
                  })
                }
              />
            </>
          ) : (
            <Typography.Text type="secondary" className="small-text">节点未单独配置时不重试</Typography.Text>
          )}
        </Space>
      </Form.Item>
    </Form>
  )
}
