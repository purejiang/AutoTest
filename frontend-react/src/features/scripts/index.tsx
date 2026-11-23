// 脚本列表：搜索 / 新建 / 编辑 / 运行 / 复制 / 导入 / 导出 / 删除
import { useRef, useState } from 'react'
import {
  Alert,
  App,
  Button,
  Card,
  Empty,
  Form,
  Input,
  Modal,
  Popconfirm,
  Space,
  Table,
  Tag,
  Typography,
  type TableProps,
} from 'antd'
import {
  CopyOutlined,
  DeleteOutlined,
  DownloadOutlined,
  EditOutlined,
  ImportOutlined,
  PlayCircleOutlined,
  PlusOutlined,
  ReloadOutlined,
} from '@ant-design/icons'
import { useNavigate } from 'react-router-dom'

import { describeApiError } from '@/api/client'
import {
  exportScript,
  useCreateScript,
  useDeleteScript,
  useDuplicateScript,
  useImportScript,
  useScripts,
} from '@/api/scripts'
import type { Script } from '@/types/api'
import { formatDateTime, safeParseJson, sourceMeta } from '@/lib/format'

import { scriptStepCount } from './nodeDefaults'
import RunScriptModal from './RunScriptModal'

interface CreateFormValues {
  name: string
  notes?: string
}

const PAGE_SIZE = 20

export default function ScriptsPage() {
  const { message } = App.useApp()
  const navigate = useNavigate()
  const fileInputRef = useRef<HTMLInputElement | null>(null)

  const [search, setSearch] = useState('')
  const [page, setPage] = useState(1)
  const [createOpen, setCreateOpen] = useState(false)
  const [createForm] = Form.useForm<CreateFormValues>()
  const [runScript, setRunScript] = useState<Script | null>(null)

  const scriptsQuery = useScripts({ query: search, limit: PAGE_SIZE, offset: (page - 1) * PAGE_SIZE })
  const createMutation = useCreateScript()
  const duplicateMutation = useDuplicateScript()
  const deleteMutation = useDeleteScript()
  const importMutation = useImportScript()

  const scripts = scriptsQuery.data?.items ?? []
  const total = scriptsQuery.data?.total ?? 0

  const handleCreate = async (): Promise<void> => {
    try {
      const values = await createForm.validateFields()
      const script = await createMutation.mutateAsync({
        name: values.name.trim(),
        notes: values.notes?.trim() ?? '',
      })
      message.success('脚本已创建')
      setCreateOpen(false)
      createForm.resetFields()
      navigate(`/scripts/${script.id}`)
    } catch {
      // 校验/请求失败由全局提示兜底
    }
  }

  const handleDuplicate = (script: Script): void => {
    duplicateMutation.mutate(
      { id: script.id },
      {
        onSuccess: (created) => {
          message.success(`已复制为「${created.name}」`)
          navigate(`/scripts/${created.id}`)
        },
      },
    )
  }

  const handleDelete = (script: Script): void => {
    deleteMutation.mutate(script.id, {
      onSuccess: () => message.success(`已删除「${script.name}」`),
    })
  }

  const handleImportFile = async (event: React.ChangeEvent<HTMLInputElement>): Promise<void> => {
    const file = event.target.files?.[0]
    event.target.value = ''
    if (!file) return
    let text: string
    try {
      text = await file.text()
    } catch {
      message.error('读取文件失败')
      return
    }
    const parsed = safeParseJson<unknown>(text)
    if (!parsed.ok) {
      message.error(`JSON 解析失败：${parsed.error}`)
      return
    }
    importMutation.mutate(
      { data: parsed.value },
      {
        onSuccess: (script) => {
          message.success(`导入成功：${script.name}`)
          navigate(`/scripts/${script.id}`)
        },
      },
    )
  }

  const columns: TableProps<Script>['columns'] = [
    {
      title: '名称',
      dataIndex: 'name',
      render: (_, script) => (
        <div>
          <Typography.Link onClick={() => navigate(`/scripts/${script.id}`)}>
            {script.name || script.id}
          </Typography.Link>
          <div className="small-text muted code-text">{script.id}</div>
        </div>
      ),
    },
    {
      title: '节点数',
      width: 90,
      render: (_, script) => scriptStepCount(script),
    },
    {
      title: '来源',
      dataIndex: 'source',
      width: 100,
      render: (source: string) => {
        const meta = sourceMeta(source)
        return <Tag color={meta.color}>{meta.label}</Tag>
      },
    },
    {
      title: '更新时间',
      dataIndex: 'updated_at',
      width: 180,
      render: (value: string) => <span className="small-text">{formatDateTime(value)}</span>,
    },
    {
      title: '操作',
      width: 330,
      render: (_, script) => (
        <Space size={0} wrap>
          <Button size="small" type="link" icon={<EditOutlined />} onClick={() => navigate(`/scripts/${script.id}`)}>
            编辑
          </Button>
          <Button size="small" type="link" icon={<PlayCircleOutlined />} onClick={() => setRunScript(script)}>
            运行
          </Button>
          <Button size="small" type="link" icon={<CopyOutlined />} onClick={() => handleDuplicate(script)}>
            复制
          </Button>
          <Button
            size="small"
            type="link"
            icon={<DownloadOutlined />}
            onClick={() => exportScript(script.id, script.name)}
          >
            导出
          </Button>
          <Popconfirm
            title={`删除「${script.name}」？`}
            description="删除后不可恢复（不会删除历史运行记录）"
            okText="删除"
            cancelText="取消"
            onConfirm={() => handleDelete(script)}
          >
            <Button size="small" type="link" danger icon={<DeleteOutlined />}>
              删除
            </Button>
          </Popconfirm>
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
            <span>脚本</span>
            <Tag color="blue">{total}</Tag>
          </Space>
        }
        extra={
          <Space wrap>
            <Input.Search
              allowClear
              placeholder="按名称搜索脚本"
              defaultValue={search}
              style={{ width: 240 }}
              onSearch={(value) => {
                setSearch(value.trim())
                setPage(1)
              }}
            />
            <Button
              icon={<ImportOutlined />}
              loading={importMutation.isPending}
              onClick={() => fileInputRef.current?.click()}
            >
              导入
            </Button>
            <Button icon={<ReloadOutlined />} loading={scriptsQuery.isFetching} onClick={() => void scriptsQuery.refetch()}>
              刷新
            </Button>
            <Button type="primary" icon={<PlusOutlined />} onClick={() => setCreateOpen(true)}>
              新建脚本
            </Button>
            <input
              ref={fileInputRef}
              type="file"
              accept=".json,application/json"
              style={{ display: 'none' }}
              onChange={(event) => void handleImportFile(event)}
            />
          </Space>
        }
      >
        {scriptsQuery.isError ? (
          <Alert
            style={{ marginBottom: 12 }}
            type="error"
            showIcon
            message="脚本列表加载失败"
            description={describeApiError(scriptsQuery.error)}
            action={<Button size="small" onClick={() => void scriptsQuery.refetch()}>重试</Button>}
          />
        ) : null}

        <Table<Script>
          size="small"
          rowKey="id"
          loading={scriptsQuery.isLoading}
          dataSource={scripts}
          columns={columns}
          locale={{
            emptyText: (
              <Empty description={search ? '未找到匹配的脚本' : '还没有脚本，先新建一个或从录制生成'}>
                <Space>
                  <Button type="primary" icon={<PlusOutlined />} onClick={() => setCreateOpen(true)}>
                    新建脚本
                  </Button>
                  <Button icon={<ImportOutlined />} onClick={() => fileInputRef.current?.click()}>
                    导入 JSON
                  </Button>
                </Space>
              </Empty>
            ),
          }}
          pagination={{
            current: page,
            pageSize: PAGE_SIZE,
            total,
            showSizeChanger: false,
            showTotal: (count) => `共 ${count} 个脚本`,
            onChange: (nextPage) => setPage(nextPage),
          }}
          scroll={{ x: 900 }}
        />
      </Card>

      <Modal
        open={createOpen}
        title="新建脚本"
        okText="创建并编辑"
        cancelText="取消"
        confirmLoading={createMutation.isPending}
        onOk={() => void handleCreate()}
        onCancel={() => setCreateOpen(false)}
      >
        <Form form={createForm} layout="vertical" initialValues={{ name: '', notes: '' }}>
          <Form.Item
            name="name"
            label="脚本名称"
            rules={[{ required: true, message: '请输入脚本名称' }, { max: 200, message: '名称最长 200 字符' }]}
          >
            <Input placeholder="例如：登录并打开设置" onPressEnter={() => void handleCreate()} />
          </Form.Item>
          <Form.Item name="notes" label="备注">
            <Input.TextArea rows={2} placeholder="可选" />
          </Form.Item>
        </Form>
        <Typography.Text type="secondary" className="small-text">
          创建后进入编辑器，会自动带一个默认 click 节点（由后端 empty_script 提供）。
        </Typography.Text>
      </Modal>

      {runScript ? (
        <RunScriptModal
          open={Boolean(runScript)}
          scriptId={runScript.id}
          scriptName={runScript.name}
          onClose={() => setRunScript(null)}
        />
      ) : null}
    </div>
  )
}
