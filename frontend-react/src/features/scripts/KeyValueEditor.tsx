// 简单的 key-value 编辑器（脚本 variables / 子脚本变量覆盖）
import { Button, Input, Space, Typography } from 'antd'
import { DeleteOutlined, PlusOutlined } from '@ant-design/icons'

export interface KeyValueEditorProps {
  value: Record<string, string>
  onChange: (value: Record<string, string>) => void
  keyPlaceholder?: string
  valuePlaceholder?: string
  emptyHint?: string
}

export default function KeyValueEditor({
  value,
  onChange,
  keyPlaceholder = '变量名',
  valuePlaceholder = '值（运行时可被请求覆盖）',
  emptyHint = '暂无变量',
}: KeyValueEditorProps) {
  const entries = Object.entries(value)

  const renameKey = (oldKey: string, nextKey: string): void => {
    if (nextKey === oldKey) return
    const next: Record<string, string> = {}
    for (const [key, itemValue] of Object.entries(value)) {
      next[key === oldKey ? nextKey : key] = itemValue
    }
    onChange(next)
  }

  const setValue = (key: string, nextValue: string): void => {
    onChange({ ...value, [key]: nextValue })
  }

  const removeKey = (key: string): void => {
    const next = { ...value }
    delete next[key]
    onChange(next)
  }

  const addKey = (): void => {
    let index = Object.keys(value).length + 1
    let key = `var_${index}`
    while (key in value) {
      index += 1
      key = `var_${index}`
    }
    onChange({ ...value, [key]: '' })
  }

  return (
    <Space direction="vertical" style={{ width: '100%' }} size={6}>
      {entries.length === 0 ? (
        <Typography.Text type="secondary" className="small-text">
          {emptyHint}
        </Typography.Text>
      ) : null}
      {entries.map(([key, itemValue], index) => (
        <Space.Compact key={`kv-${index}`} style={{ width: '100%' }}>
          <Input
            value={key}
            style={{ width: '35%' }}
            placeholder={keyPlaceholder}
            onChange={(event) => renameKey(key, event.target.value)}
          />
          <Input
            value={itemValue}
            placeholder={valuePlaceholder}
            onChange={(event) => setValue(key, event.target.value)}
          />
          <Button icon={<DeleteOutlined />} onClick={() => removeKey(key)} title="删除" />
        </Space.Compact>
      ))}
      <Button size="small" type="dashed" icon={<PlusOutlined />} onClick={addKey}>
        添加变量
      </Button>
    </Space>
  )
}
