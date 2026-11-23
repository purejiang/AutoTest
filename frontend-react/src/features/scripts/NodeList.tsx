// 节点列表：序号/类型/名称/enabled/上移下移/删除 + 新增 12 类节点
import { Button, Dropdown, Popconfirm, Space, Switch, Tag, Typography } from 'antd'
import { ArrowDownOutlined, ArrowUpOutlined, DeleteOutlined, PlusOutlined } from '@ant-design/icons'

import type { NodeType, StepNode } from '@/types/api'

import { NODE_TYPE_META, NODE_TYPES } from './nodeDefaults'

export interface NodeListProps {
  steps: StepNode[]
  selectedId: string | null
  onSelect: (id: string) => void
  onAdd: (type: NodeType) => void
  onMove: (id: string, direction: -1 | 1) => void
  onDelete: (id: string) => void
  onToggleEnabled: (id: string, enabled: boolean) => void
}

export default function NodeList({
  steps,
  selectedId,
  onSelect,
  onAdd,
  onMove,
  onDelete,
  onToggleEnabled,
}: NodeListProps) {
  const addMenuItems = NODE_TYPES.map((type) => ({
    key: type,
    label: `${NODE_TYPE_META[type].icon} ${NODE_TYPE_META[type].label}（${type}）`,
    onClick: () => onAdd(type),
  }))

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%', minHeight: 0 }}>
      <div className="editor-col-header">
        <Space size={6}>
          <Typography.Text strong>节点</Typography.Text>
          <Tag color="blue">{steps.length}</Tag>
        </Space>
        <Dropdown menu={{ items: addMenuItems }} trigger={['click']} placement="bottomRight">
          <Button size="small" type="primary" icon={<PlusOutlined />}>
            新增节点
          </Button>
        </Dropdown>
      </div>
      <div className="editor-col-body">
        {steps.length === 0 ? (
          <div style={{ padding: 24, textAlign: 'center' }}>
            <Typography.Text type="secondary">还没有节点，点「新增节点」添加</Typography.Text>
          </div>
        ) : (
          steps.map((step, index) => {
            const meta = NODE_TYPE_META[step.type]
            return (
              <div
                key={step.id}
                className={`node-item${step.id === selectedId ? ' selected' : ''}`}
                onClick={() => onSelect(step.id)}
                title={`${step.id} · ${step.type}${step.enabled ? '' : '（已禁用）'}`}
              >
                <span className="node-index">{index + 1}</span>
                <span className="node-item-icon">{meta.icon}</span>
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div className="node-name" style={{ opacity: step.enabled ? 1 : 0.5 }}>
                    {step.name || step.id}
                  </div>
                  <div className="small-text muted">{step.type}</div>
                </div>
                <div className="node-item-tools" onClick={(event) => event.stopPropagation()}>
                  <Switch
                    size="small"
                    checked={step.enabled}
                    onChange={(checked) => onToggleEnabled(step.id, checked)}
                    title="启用/禁用"
                  />
                  <Button
                    size="small"
                    type="text"
                    icon={<ArrowUpOutlined />}
                    disabled={index === 0}
                    onClick={() => onMove(step.id, -1)}
                    title="上移"
                  />
                  <Button
                    size="small"
                    type="text"
                    icon={<ArrowDownOutlined />}
                    disabled={index === steps.length - 1}
                    onClick={() => onMove(step.id, 1)}
                    title="下移"
                  />
                  <Popconfirm
                    title="删除该节点？"
                    okText="删除"
                    cancelText="取消"
                    disabled={steps.length <= 1}
                    onConfirm={() => onDelete(step.id)}
                  >
                    <Button
                      size="small"
                      type="text"
                      danger
                      icon={<DeleteOutlined />}
                      disabled={steps.length <= 1}
                      title={steps.length <= 1 ? '至少保留一个节点' : '删除'}
                    />
                  </Popconfirm>
                </div>
              </div>
            )
          })
        )}
      </div>
      <div className="editor-col-header" style={{ borderTop: '1px solid #f0f0f0', borderBottom: 'none' }}>
        <Typography.Text type="secondary" className="small-text">
          共 {steps.length} 个节点
        </Typography.Text>
      </div>
    </div>
  )
}
