// 运行脚本弹窗：选设备 + 可选 variables JSON -> POST /api/runs -> 跳运行详情
import { useState } from 'react'
import { Alert, App, Form, Input, Modal, Select, Space, Typography } from 'antd'
import { useNavigate } from 'react-router-dom'

import { describeApiError } from '@/api/client'
import { useDevices } from '@/api/devices'
import { useStartRun } from '@/api/runs'
import { safeParseJson } from '@/lib/format'

export interface RunScriptModalProps {
  open: boolean
  scriptId: string
  scriptName?: string
  onClose: () => void
}

interface RunFormValues {
  serial?: string
  variablesText?: string
}

function parseVariables(text?: string): Record<string, string> | null {
  const trimmed = (text ?? '').trim()
  if (!trimmed) return {}
  const parsed = safeParseJson<unknown>(trimmed)
  if (!parsed.ok) return null
  const value = parsed.value
  if (!value || typeof value !== 'object' || Array.isArray(value)) return null
  const result: Record<string, string> = {}
  for (const [key, item] of Object.entries(value as Record<string, unknown>)) {
    result[key] = typeof item === 'string' ? item : String(item)
  }
  return result
}

export default function RunScriptModal({ open, scriptId, scriptName, onClose }: RunScriptModalProps) {
  const { message } = App.useApp()
  const navigate = useNavigate()
  const [form] = Form.useForm<RunFormValues>()
  const [submitting, setSubmitting] = useState(false)
  const devicesQuery = useDevices({ refetchInterval: 8000, enabled: open })
  const startMutation = useStartRun()

  const devices = devicesQuery.data ?? []
  const deviceOptions = devices.map((device) => ({
    value: device.serial,
    label: `${device.serial}${device.model ? ` · ${device.model}` : ''}${device.busy ? '（占用中）' : ''}`,
  }))

  const handleOk = async (): Promise<void> => {
    let values: RunFormValues
    try {
      values = await form.validateFields()
    } catch {
      return
    }
    const variables = parseVariables(values.variablesText)
    if (variables === null) {
      message.error('variables 必须是 JSON 对象，例如 {"user":"alice"}')
      return
    }
    setSubmitting(true)
    try {
      const run = await startMutation.mutateAsync({
        script_id: scriptId,
        serial: values.serial || undefined,
        variables,
      })
      message.success(`已创建运行 #${run.id}`)
      onClose()
      form.resetFields()
      navigate(`/runs/${run.id}`)
    } catch {
      // 错误（如设备占用 409）已由全局提示
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <Modal
      open={open}
      title={`运行脚本${scriptName ? ` · ${scriptName}` : ''}`}
      okText="创建运行"
      cancelText="取消"
      confirmLoading={submitting || startMutation.isPending}
      onOk={() => void handleOk()}
      onCancel={() => {
        onClose()
      }}
      width={560}
    >
      <Form form={form} layout="vertical" initialValues={{ serial: '', variablesText: '' }}>
        <Form.Item label="目标设备" name="serial" extra="留空则由后端选择唯一在线设备">
          <Select
            allowClear
            placeholder="自动（唯一在线设备）"
            options={[{ value: '', label: '自动（唯一在线设备）' }, ...deviceOptions]}
            loading={devicesQuery.isLoading}
            notFoundContent={devicesQuery.isError ? '设备列表不可用' : '未检测到设备'}
          />
        </Form.Item>
        <Form.Item
          label="变量覆盖（可选）"
          name="variablesText"
          extra='JSON 对象，例如 {"user": "alice"}；会覆盖脚本 variables'
        >
          <Input.TextArea rows={4} placeholder='{"user": "alice"}' className="code-text" />
        </Form.Item>
      </Form>
      {devicesQuery.isError ? (
        <Alert
          type="warning"
          showIcon
          message="设备列表加载失败"
          description={describeApiError(devicesQuery.error)}
          style={{ marginBottom: 8 }}
        />
      ) : null}
      <Space direction="vertical" size={2}>
        <Typography.Text type="secondary" className="small-text">
          运行创建后可在「运行详情」实时查看步骤与事件流；暂停/停止/单步调试也在详情页。
        </Typography.Text>
      </Space>
    </Modal>
  )
}
