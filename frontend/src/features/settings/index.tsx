// 设置页：按 GET /api/settings 的 schema 动态渲染，含健康信息与维护工具
import { useEffect, useMemo, useState } from 'react'
import {
  Alert,
  App,
  Button,
  Card,
  Checkbox,
  Descriptions,
  Empty,
  Form,
  Input,
  InputNumber,
  Popconfirm,
  Result,
  Skeleton,
  Space,
  Switch,
  Tag,
  Tooltip,
  Typography,
  type DescriptionsProps,
} from 'antd'
import { DeleteOutlined, ReloadOutlined, SaveOutlined, UndoOutlined } from '@ant-design/icons'

import { describeApiError } from '@/api/client'
import {
  useCleanup,
  useDeleteSettingOverride,
  useHealth,
  useMaintenanceStats,
  useResetSettings,
  useSettings,
  useUpdateSettings,
} from '@/api/settings'
import type { SettingEntry } from '@/types/api'
import { formatBytes, formatDateTime, numberOrNull, prettyJson, settingSourceMeta } from '@/lib/format'

function FieldLabel({
  entry,
  source,
  onReset,
  resetting,
}: {
  entry: SettingEntry
  source: 'default' | 'file' | 'db'
  onReset: () => void
  resetting: boolean
}) {
  const meta = settingSourceMeta(source)
  return (
    <span className="settings-field-label">
      {entry.title}
      <Tag color={meta.color}>{meta.label}</Tag>
      {source !== 'default' ? (
        <Tooltip title="删除数据库/文件层覆盖，回到默认值">
          <Button
            size="small"
            type="text"
            icon={<UndoOutlined />}
            loading={resetting}
            onClick={(event) => {
              event.preventDefault()
              onReset()
            }}
          />
        </Tooltip>
      ) : null}
    </span>
  )
}

function SettingsForm() {
  const { message } = App.useApp()
  const [form] = Form.useForm()
  const { data, isLoading, isError, error, refetch } = useSettings()
  const updateMutation = useUpdateSettings()
  const resetMutation = useResetSettings()
  const deleteOverride = useDeleteSettingOverride()

  useEffect(() => {
    if (data) form.setFieldsValue(data.effective)
  }, [data, form])

  const handleSave = async (): Promise<void> => {
    try {
      const values = (await form.validateFields()) as Record<string, unknown>
      await updateMutation.mutateAsync(values)
      message.success('设置已保存')
    } catch {
      // 表单校验失败或请求失败：请求错误已由全局 MutationCache 提示
    }
  }

  if (isLoading) return <Skeleton active paragraph={{ rows: 8 }} />

  if (isError || !data) {
    return (
      <Result
        status="warning"
        title="设置加载失败"
        subTitle={describeApiError(error)}
        extra={
          <Button type="primary" onClick={() => void refetch()}>
            重试
          </Button>
        }
      />
    )
  }

  return (
    <Form form={form} layout="vertical" size="small">
      <div className="settings-grid">
        {data.schema.map((entry) => {
          const source = data.sources[entry.key] ?? 'default'
          return (
            <Form.Item
              key={entry.key}
              name={entry.key}
              label={<FieldLabel
                entry={entry}
                source={source}
                resetting={deleteOverride.isPending && deleteOverride.variables === entry.key}
                onReset={() => deleteOverride.mutate(entry.key)}
              />}
              extra={entry.desc ? <Typography.Text type="secondary" className="small-text">{entry.desc}</Typography.Text> : undefined}
              valuePropName={entry.type === 'bool' ? 'checked' : 'value'}
            >
              {entry.type === 'bool' ? (
                <Switch />
              ) : entry.type === 'int' ? (
                <InputNumber
                  style={{ width: '100%' }}
                  min={entry.min}
                  max={entry.max}
                  step={1}
                  onChange={(value) => form.setFieldValue(entry.key, numberOrNull(value))}
                />
              ) : (
                <Input allowClear />
              )}
            </Form.Item>
          )
        })}
      </div>

      <Space style={{ marginTop: 8 }}>
        <Button
          type="primary"
          icon={<SaveOutlined />}
          loading={updateMutation.isPending}
          onClick={() => void handleSave()}
        >
          保存设置
        </Button>
        <Popconfirm
          title="恢复默认设置？"
          description="清空 DB/文件层覆盖，所有配置回到代码默认值。"
          okText="恢复默认"
          cancelText="取消"
          onConfirm={() => {
            resetMutation.mutate(undefined, {
              onSuccess: () => message.success('已恢复默认设置'),
            })
          }}
        >
          <Button icon={<ReloadOutlined />} loading={resetMutation.isPending} danger>
            恢复默认
          </Button>
        </Popconfirm>
        <Button onClick={() => void refetch()}>重新加载</Button>
      </Space>
    </Form>
  )
}

function HealthCard() {
  const { data, isError, error, isLoading, refetch, isFetching } = useHealth()

  const items: DescriptionsProps['items'] = data
    ? [
        {
          key: 'status',
          label: '状态',
          children: <Tag color={data.status === 'ok' ? 'success' : 'warning'}>{data.status}</Tag>,
        },
        { key: 'version', label: '版本', children: data.version },
        { key: 'db', label: '数据库', children: <span className="code-text">{data.db}</span> },
        {
          key: 'adb',
          label: 'adb',
          children: (
            <span className="code-text">
              {data.adb?.path || '—'} {data.adb?.version ? `· ${data.adb.version}` : ''} · 设备 {data.adb?.devices ?? 0}
            </span>
          ),
        },
        { key: 'data_dir', label: '数据目录', children: <span className="code-text">{data.data_dir}</span> },
      ]
    : []

  return (
    <Card
      size="small"
      title="健康信息"
      extra={<Button size="small" icon={<ReloadOutlined />} loading={isFetching} onClick={() => void refetch()}>刷新</Button>}
    >
      {isLoading ? (
        <Skeleton active paragraph={{ rows: 3 }} />
      ) : isError || !data ? (
        <Alert
          type="warning"
          showIcon
          message="后端未连接"
          description={describeApiError(error)}
          action={<Button size="small" onClick={() => void refetch()}>重试</Button>}
        />
      ) : (
        <Descriptions size="small" column={1} bordered items={items} />
      )}
    </Card>
  )
}

function MaintenanceCard() {
  const { message } = App.useApp()
  const statsQuery = useMaintenanceStats()
  const cleanup = useCleanup()
  const [retentionDays, setRetentionDays] = useState<number | null>(null)
  const [dryRun, setDryRun] = useState(true)

  useEffect(() => {
    if (statsQuery.data) setRetentionDays(statsQuery.data.retention_days)
  }, [statsQuery.data])

  const statsItems: DescriptionsProps['items'] = statsQuery.data
    ? [
        { key: 'db_size', label: 'DB 体积', children: formatBytes(statsQuery.data.db_size) },
        { key: 'artifacts_size', label: '产物体积', children: formatBytes(statsQuery.data.artifacts_size) },
        { key: 'runs', label: '运行数', children: statsQuery.data.runs },
        { key: 'artifacts', label: '产物数', children: statsQuery.data.artifacts },
        {
          key: 'oldest_run_at',
          label: '最早运行',
          children: formatDateTime(statsQuery.data.oldest_run_at),
        },
        { key: 'retention_days', label: '保留天数', children: statsQuery.data.retention_days },
      ]
    : []

  const cleanupResult = cleanup.data
  const cleanupSummary = useMemo(() => {
    if (!cleanupResult) return ''
    const summaryKeys = [
      'dry_run',
      'retention_days',
      'cutoff',
      'runs_matched',
      'runs_deleted',
      'artifacts_matched',
      'artifacts_deleted',
      'files_deleted',
      'freed_bytes',
    ]
    const parts: string[] = []
    for (const key of summaryKeys) {
      if (cleanupResult[key] !== undefined && cleanupResult[key] !== null) {
        const value = cleanupResult[key]
        const text =
          key === 'freed_bytes' && typeof value === 'number'
            ? formatBytes(value)
            : typeof value === 'object'
              ? prettyJson(value)
              : String(value)
        parts.push(`${key}: ${text}`)
      }
    }
    return parts.join(' · ')
  }, [cleanupResult])

  const handleCleanup = (): void => {
    cleanup.mutate(
      { retention_days: retentionDays ?? undefined, dry_run: dryRun },
      {
        onSuccess: () => message.success(dryRun ? '演练完成（未删除任何数据）' : '清理完成'),
      },
    )
  }

  return (
    <Card
      size="small"
      title="维护"
      extra={<Button size="small" icon={<ReloadOutlined />} loading={statsQuery.isFetching} onClick={() => void statsQuery.refetch()}>刷新统计</Button>}
    >
      {statsQuery.isLoading ? (
        <Skeleton active paragraph={{ rows: 4 }} />
      ) : statsQuery.isError ? (
        <Alert type="warning" showIcon message="统计加载失败" description={describeApiError(statsQuery.error)} />
      ) : (
        <>
          <Descriptions size="small" column={2} bordered items={statsItems} />
          <div className="maintenance-actions">
            <span>保留天数</span>
            <InputNumber
              min={1}
              max={3650}
              value={retentionDays}
              onChange={(value) => setRetentionDays(numberOrNull(value))}
              style={{ width: 120 }}
            />
            <Checkbox checked={dryRun} onChange={(event) => setDryRun(event.target.checked)}>
              仅演练（dry_run，不删除）
            </Checkbox>
            <Popconfirm
              title={dryRun ? '执行清理演练？' : '确认执行清理？此操作会删除过期数据'}
              okText="执行"
              cancelText="取消"
              onConfirm={handleCleanup}
            >
              <Button danger={!dryRun} type={dryRun ? 'default' : 'primary'} icon={<DeleteOutlined />} loading={cleanup.isPending}>
                执行清理
              </Button>
            </Popconfirm>
          </div>
          {cleanupResult ? (
            <div style={{ marginTop: 12 }}>
              {cleanupSummary ? <Alert type="info" showIcon message={cleanupSummary} style={{ marginBottom: 8 }} /> : null}
              <pre className="pre-block">{prettyJson(cleanupResult)}</pre>
            </div>
          ) : null}
        </>
      )}
    </Card>
  )
}

export default function SettingsPage() {
  return (
    <div className="page">
      <Card size="small" title="配置（三级：默认 < 文件 < 数据库）">
        <SettingsForm />
      </Card>
      <HealthCard />
      <MaintenanceCard />
      <Typography.Text type="secondary" className="small-text">
        配置优先级：代码默认 &lt; 配置文件 &lt; 数据库覆盖。保存只会写数据库层；恢复默认会清空全部覆盖。
      </Typography.Text>
      <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="更多设置项由后端 /api/settings 的 schema 决定（scrcpy 白名单等）" />
    </div>
  )
}
