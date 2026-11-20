// Target 编辑组件：by 下拉 + value + index（point 时为 x/y），可切换"不指定"
import { Input, InputNumber, Select, Space, Switch, Typography } from 'antd'

import type { Target, TargetBy } from '@/types/api'
import { numberOrNull } from '@/lib/format'

export const TARGET_BY_OPTIONS: { value: TargetBy; label: string }[] = [
  { value: 'text', label: '文本 text' },
  { value: 'text_contains', label: '文本包含 text_contains' },
  { value: 'resource_id', label: '资源 ID resource_id' },
  { value: 'content_desc', label: '内容描述 content_desc' },
  { value: 'class_name', label: '类名 class_name' },
  { value: 'xpath', label: 'XPath xpath' },
  { value: 'point', label: '坐标点 point' },
]

const PLACEHOLDERS: Record<TargetBy, string> = {
  text: '文本，例如：登录',
  text_contains: '包含的文本片段',
  resource_id: 'com.example.app:id/btn_login',
  content_desc: '内容描述',
  class_name: 'android.widget.Button',
  xpath: '//android.widget.Button[@text="登录"]',
  point: '',
}

export interface TargetEditorProps {
  value: Target | null | undefined
  onChange: (target: Target | null) => void
  /** 允许置空（input/assert 等可选 target） */
  allowNull?: boolean
  disabled?: boolean
}

export default function TargetEditor({ value, onChange, allowNull = false, disabled }: TargetEditorProps) {
  const enabled = !allowNull || value !== null && value !== undefined
  const target: Target = value ?? { by: 'text', value: '', index: 0 }
  const isPoint = target.by === 'point'

  const changeBy = (by: TargetBy): void => {
    if (by === 'point') {
      onChange({ by: 'point', x: target.x ?? 0, y: target.y ?? 0 })
      return
    }
    onChange({ by, value: isPoint ? '' : (target.value ?? ''), index: target.index ?? 0 })
  }

  return (
    <Space wrap align="center" className="target-editor">
      {allowNull ? (
        <Switch
          size="small"
          checked={enabled}
          onChange={(checked) => onChange(checked ? { by: 'text', value: '', index: 0 } : null)}
          checkedChildren="指定"
          unCheckedChildren="空"
          disabled={disabled}
        />
      ) : null}

      {!enabled ? (
        <Typography.Text type="secondary" className="small-text">
          未指定目标（可为空）
        </Typography.Text>
      ) : (
        <>
          <Select
            value={target.by}
            onChange={(next) => changeBy(next as TargetBy)}
            options={TARGET_BY_OPTIONS}
            style={{ width: 170 }}
            disabled={disabled}
          />
          {isPoint ? (
            <>
              <InputNumber
                addonBefore="X"
                min={0}
                value={target.x ?? null}
                onChange={(next) => onChange({ by: 'point', x: numberOrNull(next) ?? undefined, y: target.y })}
                style={{ width: 130 }}
                disabled={disabled}
              />
              <InputNumber
                addonBefore="Y"
                min={0}
                value={target.y ?? null}
                onChange={(next) => onChange({ by: 'point', x: target.x, y: numberOrNull(next) ?? undefined })}
                style={{ width: 130 }}
                disabled={disabled}
              />
            </>
          ) : (
            <>
              <Input
                value={target.value ?? ''}
                placeholder={PLACEHOLDERS[target.by]}
                onChange={(event) =>
                  onChange({ by: target.by, value: event.target.value, index: target.index ?? 0 })
                }
                style={{ width: 260 }}
                disabled={disabled}
              />
              <InputNumber
                addonBefore="index"
                min={0}
                value={target.index ?? 0}
                onChange={(next) =>
                  onChange({ by: target.by, value: target.value ?? '', index: numberOrNull(next) ?? 0 })
                }
                style={{ width: 130 }}
                disabled={disabled}
              />
            </>
          )}
        </>
      )}
    </Space>
  )
}
