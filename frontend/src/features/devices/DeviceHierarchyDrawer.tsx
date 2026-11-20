// 层级树抽屉：Tree 展示 class/text/res_id/bounds，点击节点复制选择器
import { useEffect, useMemo, useState, type ReactNode } from 'react'
import { Alert, App, Button, Drawer, Empty, Skeleton, Space, Switch, Tag, Tree, Typography, type TreeProps } from 'antd'
import { ReloadOutlined } from '@ant-design/icons'

import { describeApiError } from '@/api/client'
import { useDeviceHierarchy } from '@/api/devices'
import type { HierarchyNode } from '@/types/api'

export interface DeviceHierarchyDrawerProps {
  serial: string | null
  open: boolean
  onClose: () => void
}

interface HierarchyTreeNode {
  key: string
  title: ReactNode
  children: HierarchyTreeNode[]
}

function shortClass(name: string): string {
  if (!name) return 'node'
  return name.split('.').pop() ?? name
}

/** 与脚本 Target.by 对应的可复制选择器。 */
function selectorFor(node: HierarchyNode): string {
  if (node.resource_id) return `resource_id=${node.resource_id}`
  if (node.text) return `text=${node.text}`
  if (node.content_desc) return `content_desc=${node.content_desc}`
  return `class_name=${node.class_name}`
}

function collectKeys(node: HierarchyNode, prefix = '0', depth = 0, out: string[] = []): string[] {
  out.push(prefix)
  if (depth < 6) {
    node.children.forEach((child, index) => collectKeys(child, `${prefix}-${index}`, depth + 1, out))
  }
  return out
}

async function copyText(text: string): Promise<boolean> {
  try {
    if (navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(text)
      return true
    }
  } catch {
    // fall through to execCommand
  }
  try {
    const textarea = document.createElement('textarea')
    textarea.value = text
    textarea.style.position = 'fixed'
    textarea.style.opacity = '0'
    document.body.appendChild(textarea)
    textarea.select()
    const ok = document.execCommand('copy')
    textarea.remove()
    return ok
  } catch {
    return false
  }
}

export default function DeviceHierarchyDrawer({ serial, open, onClose }: DeviceHierarchyDrawerProps) {
  const { message } = App.useApp()
  const [raw, setRaw] = useState(false)
  const query = useDeviceHierarchy(serial, { raw, enabled: open })
  const hierarchy = query.data ?? null
  const data = hierarchy?.node ?? null

  const treeData = useMemo<HierarchyTreeNode[]>(() => {
    if (!data) return []
    const build = (node: HierarchyNode, key: string): HierarchyTreeNode => ({
      key,
      title: (
        <span
          className="hierarchy-title"
          title={`点击复制选择器：${selectorFor(node)}`}
          onClick={async (event) => {
            event.stopPropagation()
            const selector = selectorFor(node)
            const ok = await copyText(selector)
            if (ok) message.success(`已复制：${selector}`)
            else message.error('复制失败，请手动选择文本')
          }}
        >
          <Tag style={{ marginRight: 4 }}>{shortClass(node.class_name)}</Tag>
          {node.text ? <span>&quot;{node.text}&quot; </span> : null}
          {node.resource_id ? (
            <Typography.Text type="secondary">#{node.resource_id.split('/').pop()}</Typography.Text>
          ) : null}
          <span className="hierarchy-bounds">[{node.bounds?.join(',') ?? ''}]</span>
        </span>
      ),
      children: node.children.map((child, index) => build(child, `${key}-${index}`)),
    })
    return [build(data, '0')]
  }, [data, message])

  const allKeys = useMemo(() => (data ? collectKeys(data) : []), [data])
  const [expandedKeys, setExpandedKeys] = useState<React.Key[]>([])

  useEffect(() => {
    setExpandedKeys(allKeys.slice(0, 400))
  }, [allKeys])

  return (
    <Drawer
      open={open}
      onClose={onClose}
      width={920}
      title={
        <Space>
          <span>层级树</span>
          <Typography.Text type="secondary" className="code-text">
            {serial ?? ''}
          </Typography.Text>
        </Space>
      }
      extra={
        <Space>
          <span className="small-text muted">原始 XML</span>
          <Switch size="small" checked={raw} onChange={setRaw} />
          <Button size="small" icon={<ReloadOutlined />} loading={query.isFetching} onClick={() => void query.refetch()}>
            刷新
          </Button>
        </Space>
      }
    >
      <Typography.Paragraph type="secondary" className="small-text">
        {raw
          ? '原始 uiautomator XML（raw=true）；切回关闭可看可点击的树。'
          : '点击节点复制选择器（优先 resource_id → text → content_desc → class_name），可直接粘贴到脚本的 Target 里。'}
      </Typography.Paragraph>
      {query.isLoading ? (
        <Skeleton active paragraph={{ rows: 10 }} />
      ) : query.isError ? (
        <Alert
          type="error"
          showIcon
          message="层级获取失败"
          description={describeApiError(query.error)}
          action={<Button size="small" onClick={() => void query.refetch()}>重试</Button>}
        />
      ) : raw ? (
        hierarchy?.raw ? (
          <pre className="pre-block" style={{ maxHeight: '72vh' }}>
            {hierarchy.raw}
          </pre>
        ) : (
          <Alert
            type="info"
            showIcon
            message="当前驱动不提供原始 XML"
            description={hierarchy?.rawUnavailable ?? '后端返回 raw_unavailable'}
          />
        )
      ) : treeData.length === 0 ? (
        <Empty description="暂无层级数据（设备可能离线或未授权 uiautomator）" image={Empty.PRESENTED_IMAGE_SIMPLE} />
      ) : (
        <Tree
          showLine
          blockNode
          expandedKeys={expandedKeys}
          onExpand={(keys) => setExpandedKeys(keys)}
          treeData={treeData as TreeProps['treeData']}
        />
      )}
    </Drawer>
  )
}
