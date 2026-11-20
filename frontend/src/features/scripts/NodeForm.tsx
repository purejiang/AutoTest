// 节点表单：公共字段 + when + retry/on_fail + 12 类 params
import { Col, Divider, Form, Input, InputNumber, Row, Select, Space, Switch, Tag, Typography } from 'antd'

import type { StepNode } from '@/types/api'
import { numberOrNull } from '@/lib/format'

import { NODE_TYPE_META } from './nodeDefaults'
import ParamsEditor from './ParamsEditor'
import TargetEditor from './TargetEditor'

const ON_FAIL_OPTIONS = [
  { value: 'none', label: '继承/不设置' },
  { value: 'abort', label: '中止 abort' },
  { value: 'continue', label: '继续 continue' },
  { value: 'goto', label: '跳转 goto' },
]

const RERUN_SAFETY_OPTIONS = [
  { value: 'safe', label: 'safe（可安全重跑）' },
  { value: 'stateful', label: 'stateful（依赖状态）' },
  { value: 'destructive', label: 'destructive（破坏性，禁止重跑）' },
]

const WHEN_MODE_OPTIONS = [
  { value: 'require', label: 'require 必须出现' },
  { value: 'optional', label: 'optional 可选' },
]

const WHEN_STATE_OPTIONS = [
  { value: 'visible', label: 'visible 可见' },
  { value: 'gone', label: 'gone 消失' },
]

export interface NodeFormProps {
  node: StepNode
  index: number
  total: number
  steps: StepNode[]
  onChange: (next: StepNode) => void
}

export default function NodeForm({ node, index, total, steps, onChange }: NodeFormProps) {
  const meta = NODE_TYPE_META[node.type]
  const patch = (partial: Partial<StepNode>): void => onChange({ ...node, ...partial })
  const otherSteps = steps.filter((step) => step.id !== node.id)
  const onFailAction = node.on_fail?.action ?? 'none'

  return (
    <Form layout="vertical" size="small">
      <div style={{ marginBottom: 8 }}>
        <Space wrap>
          <Tag color={meta.color}>{meta.icon} {meta.label}</Tag>
          <Typography.Text type="secondary">
            第 {index + 1} / {total} 个节点 · id=<span className="code-text">{node.id}</span>
          </Typography.Text>
        </Space>
      </div>

      <Row gutter={12}>
        <Col xs={24} md={12}>
          <Form.Item label="节点名称">
            <Input value={node.name} onChange={(event) => patch({ name: event.target.value })} placeholder="例如：点击登录按钮" />
          </Form.Item>
        </Col>
        <Col xs={24} md={6}>
          <Form.Item label="步骤 ID" extra="唯一，[A-Za-z0-9_-]{1,64}">
            <Input value={node.id} disabled />
          </Form.Item>
        </Col>
        <Col xs={24} md={6}>
          <Form.Item label="启用">
            <Switch checked={node.enabled} onChange={(checked) => patch({ enabled: checked })} />
          </Form.Item>
        </Col>
      </Row>

      <Form.Item label="描述">
        <Input.TextArea
          value={node.description}
          onChange={(event) => patch({ description: event.target.value })}
          autoSize={{ minRows: 1, maxRows: 3 }}
          placeholder="这一步做什么（可留空）"
        />
      </Form.Item>

      <Row gutter={12}>
        <Col xs={12} md={6}>
          <Form.Item label="节点超时 (ms)" extra="空=继承脚本默认">
            <InputNumber
              min={100}
              max={600000}
              value={node.timeout_ms}
              placeholder="继承"
              onChange={(value) => patch({ timeout_ms: numberOrNull(value) })}
              style={{ width: '100%' }}
            />
          </Form.Item>
        </Col>
        <Col xs={12} md={6}>
          <Form.Item label="执行前延迟 (ms)">
            <InputNumber
              min={0}
              max={600000}
              value={node.delay_before_ms}
              onChange={(value) => patch({ delay_before_ms: numberOrNull(value) ?? 0 })}
              style={{ width: '100%' }}
            />
          </Form.Item>
        </Col>
        <Col xs={12} md={6}>
          <Form.Item label="执行后延迟 (ms)">
            <InputNumber
              min={0}
              max={600000}
              value={node.delay_after_ms}
              onChange={(value) => patch({ delay_after_ms: numberOrNull(value) ?? 0 })}
              style={{ width: '100%' }}
            />
          </Form.Item>
        </Col>
        <Col xs={12} md={6}>
          <Form.Item label="重跑安全性">
            <Select
              value={node.rerun_safety}
              options={RERUN_SAFETY_OPTIONS}
              onChange={(value) => patch({ rerun_safety: value })}
            />
          </Form.Item>
        </Col>
      </Row>

      <Divider orientation="left" plain>
        执行条件 when
      </Divider>
      <Space align="center" style={{ marginBottom: 8 }}>
        <Switch
          size="small"
          checked={node.when !== null}
          onChange={(checked) =>
            patch({
              when: checked
                ? {
                    target: { by: 'text', value: '', index: 0 },
                    mode: 'require',
                    timeout_ms: 3000,
                    state: 'visible',
                  }
                : null,
            })
          }
        />
        <Typography.Text>启用条件（不满足时 require=失败，optional=跳过）</Typography.Text>
      </Space>
      {node.when ? (
        <>
          <Form.Item label="条件目标">
            <TargetEditor
              value={node.when.target}
              onChange={(target) => {
                if (target && node.when) patch({ when: { ...node.when, target } })
              }}
            />
          </Form.Item>
          <Row gutter={12}>
            <Col xs={24} md={8}>
              <Form.Item label="模式">
                <Select
                  value={node.when.mode}
                  options={WHEN_MODE_OPTIONS}
                  onChange={(value) => {
                    if (node.when) patch({ when: { ...node.when, mode: value as 'require' | 'optional' } })
                  }}
                />
              </Form.Item>
            </Col>
            <Col xs={12} md={8}>
              <Form.Item label="状态">
                <Select
                  value={node.when.state}
                  options={WHEN_STATE_OPTIONS}
                  onChange={(value) => {
                    if (node.when) patch({ when: { ...node.when, state: value as 'visible' | 'gone' } })
                  }}
                />
              </Form.Item>
            </Col>
            <Col xs={12} md={8}>
              <Form.Item label="条件超时 (ms)">
                <InputNumber
                  min={100}
                  max={600000}
                  value={node.when.timeout_ms}
                  onChange={(value) => {
                    if (node.when) patch({ when: { ...node.when, timeout_ms: numberOrNull(value) ?? 3000 } })
                  }}
                  style={{ width: '100%' }}
                />
              </Form.Item>
            </Col>
          </Row>
        </>
      ) : null}

      <Divider orientation="left" plain>
        失败与重试
      </Divider>
      <Form.Item label="重试">
        <Space wrap>
          <Switch
            checked={node.retry !== null}
            onChange={(checked) => patch({ retry: checked ? { count: 1, interval_ms: 1000 } : null })}
          />
          {node.retry ? (
            <>
              <InputNumber
                addonBefore="次数"
                min={0}
                max={20}
                value={node.retry.count}
                onChange={(value) =>
                  patch({ retry: { ...node.retry!, count: numberOrNull(value) ?? 0 } })
                }
              />
              <InputNumber
                addonBefore="间隔 ms"
                min={0}
                max={60000}
                value={node.retry.interval_ms}
                onChange={(value) =>
                  patch({ retry: { ...node.retry!, interval_ms: numberOrNull(value) ?? 0 } })
                }
              />
            </>
          ) : (
            <Typography.Text type="secondary" className="small-text">不重试</Typography.Text>
          )}
        </Space>
      </Form.Item>

      <Row gutter={12}>
        <Col xs={24} md={12}>
          <Form.Item label="失败策略 on_fail" extra="不设置时继承脚本默认">
            <Space wrap>
              <Select
                value={onFailAction}
                options={ON_FAIL_OPTIONS}
                style={{ width: 170 }}
                onChange={(value) => {
                  if (value === 'none') patch({ on_fail: null })
                  else if (value === 'goto') patch({ on_fail: { action: 'goto', target_id: otherSteps[0]?.id ?? '' } })
                  else patch({ on_fail: { action: value as 'abort' | 'continue' } })
                }}
              />
              {onFailAction === 'goto' ? (
                <Select
                  placeholder="选择 goto 目标节点"
                  value={node.on_fail?.target_id}
                  options={otherSteps.map((step) => ({ value: step.id, label: `${step.id} · ${step.name || step.type}` }))}
                  onChange={(value) => patch({ on_fail: { action: 'goto', target_id: value } })}
                  style={{ width: 220 }}
                />
              ) : null}
            </Space>
          </Form.Item>
        </Col>
        <Col xs={12} md={6}>
          <Form.Item label="失败截图">
            <Switch
              checked={node.screenshot_on_fail}
              onChange={(checked) => patch({ screenshot_on_fail: checked })}
            />
          </Form.Item>
        </Col>
        <Col xs={12} md={6}>
          <Form.Item label="标签">
            <Select
              mode="tags"
              value={node.tags}
              tokenSeparators={[',']}
              placeholder="回车添加"
              onChange={(tags) => patch({ tags })}
            />
          </Form.Item>
        </Col>
      </Row>

      <Divider orientation="left" plain>
        参数 params
      </Divider>
      <ParamsEditor type={node.type} params={node.params} onChange={(params) => patch({ params })} />
    </Form>
  )
}
